"""Layer 1 integration tests: exercise every adapter's real code paths with
mocked transports. No live credentials or network required.

Covers, per platform:
  * outbound send() calls the right client/API with the right arguments
  * inbound parsing produces correct Envelopes and dispatches them
Plus the shared FastAPI webhook server routes (WhatsApp verify + inbound, SMS).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openpup.config import Settings
from openpup.messaging.envelope import Envelope
from openpup.messaging.registry import PlatformRegistry


def make_settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def collector():
    received = []
    reg = PlatformRegistry()

    async def handler(env):
        received.append(env)

    reg.set_inbound_handler(handler)
    return reg, received


# =============================================================================
# Discord
# =============================================================================
class TestDiscord:
    def _adapter(self, reg):
        from openpup.platforms.discord_adapter import DiscordAdapter

        s = make_settings(DISCORD_ENABLED=True, DISCORD_BOT_TOKEN="tok")
        return DiscordAdapter(s, reg)

    def test_dm_becomes_envelope(self):
        reg, _ = collector()
        adapter = self._adapter(reg)
        msg = SimpleNamespace(
            author="alice",
            guild=None,  # DM
            mentions=[],
            channel=SimpleNamespace(id=999),
            content="hello pup",
        )
        env = adapter._message_to_envelope(msg)
        assert env is not None
        assert env.platform == "discord"
        assert env.channel == "999"
        assert env.text == "hello pup"

    def test_non_mention_guild_message_ignored(self):
        reg, _ = collector()
        adapter = self._adapter(reg)
        msg = SimpleNamespace(
            author="bob",
            guild=SimpleNamespace(id=1),  # in a guild
            mentions=[],  # not mentioned
            channel=SimpleNamespace(id=5),
            content="just chatting",
        )
        assert adapter._message_to_envelope(msg) is None

    @pytest.mark.asyncio
    async def test_handle_message_dispatches(self):
        reg, received = collector()
        adapter = self._adapter(reg)
        msg = SimpleNamespace(
            author="alice", guild=None, mentions=[], channel=SimpleNamespace(id=42), content="hi"
        )
        await adapter._handle_message(msg)
        assert len(received) == 1
        assert received[0].text == "hi"

    @pytest.mark.asyncio
    async def test_send_calls_channel_send(self):
        reg, _ = collector()
        adapter = self._adapter(reg)
        channel = MagicMock()
        channel.send = AsyncMock()
        adapter._client = MagicMock()
        adapter._client.get_channel.return_value = channel
        await adapter.send(Envelope.to("discord:123", "yo"))
        adapter._client.get_channel.assert_called_once_with(123)
        channel.send.assert_awaited_once_with("yo")

    @pytest.mark.asyncio
    async def test_send_long_message_chunks(self):
        reg, _ = collector()
        adapter = self._adapter(reg)
        channel = MagicMock()
        channel.send = AsyncMock()
        adapter._client = MagicMock()
        adapter._client.get_channel.return_value = channel
        await adapter.send(Envelope.to("discord:1", "x" * 4000))
        # 4000 chars / 1900 per chunk -> 3 sends
        assert channel.send.await_count == 3


# =============================================================================
# Telegram
# =============================================================================
class TestTelegram:
    def _adapter(self, reg):
        from openpup.platforms.telegram_adapter import TelegramAdapter

        s = make_settings(TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="123:abc")
        return TelegramAdapter(s, reg)

    @pytest.mark.asyncio
    async def test_inbound_update_dispatches(self):
        reg, received = collector()
        adapter = self._adapter(reg)
        update = SimpleNamespace(
            effective_message=SimpleNamespace(text="ping"),
            effective_chat=SimpleNamespace(id=777),
            effective_user=SimpleNamespace(username="carol"),
        )
        await adapter._on_message(update, None)
        assert len(received) == 1
        assert received[0].channel == "777"
        assert received[0].sender == "carol"
        assert received[0].text == "ping"

    @pytest.mark.asyncio
    async def test_empty_message_ignored(self):
        reg, received = collector()
        adapter = self._adapter(reg)
        update = SimpleNamespace(effective_message=None, effective_chat=None, effective_user=None)
        await adapter._on_message(update, None)
        assert received == []

    @pytest.mark.asyncio
    async def test_send_calls_bot(self):
        reg, _ = collector()
        adapter = self._adapter(reg)
        adapter._app = MagicMock()
        adapter._app.bot.send_message = AsyncMock()
        await adapter.send(Envelope.to("telegram:555", "hi there"))
        adapter._app.bot.send_message.assert_awaited_once_with(chat_id=555, text="hi there")

    @pytest.mark.asyncio
    async def test_typing_sends_chat_action(self):
        reg, _ = collector()
        adapter = self._adapter(reg)
        adapter._app = MagicMock()
        adapter._app.bot.send_chat_action = AsyncMock()
        await adapter.typing("555")
        adapter._app.bot.send_chat_action.assert_awaited_once()
        kwargs = adapter._app.bot.send_chat_action.call_args.kwargs
        assert kwargs["chat_id"] == 555


# =============================================================================
# WhatsApp
# =============================================================================
class TestWhatsApp:
    def _adapter(self, reg):
        from openpup.platforms.whatsapp_adapter import WhatsAppAdapter

        s = make_settings(
            WHATSAPP_ENABLED=True,
            WHATSAPP_PHONE_NUMBER_ID="PNID",
            WHATSAPP_ACCESS_TOKEN="ATOK",
            WHATSAPP_VERIFY_TOKEN="VTOK",
        )
        return WhatsAppAdapter(s, reg)

    @pytest.mark.asyncio
    async def test_handle_webhook_parses_text(self):
        reg, received = collector()
        adapter = self._adapter(reg)
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "type": "text",
                                        "from": "15551230000",
                                        "id": "wamid.X",
                                        "text": {"body": "hey from whatsapp"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        envs = await adapter.handle_webhook(payload)
        assert len(envs) == 1
        assert received[0].channel == "15551230000"
        assert received[0].text == "hey from whatsapp"
        assert received[0].meta["wamid"] == "wamid.X"

    @pytest.mark.asyncio
    async def test_handle_webhook_skips_non_text(self):
        reg, received = collector()
        adapter = self._adapter(reg)
        payload = {"entry": [{"changes": [{"value": {"messages": [{"type": "image"}]}}]}]}
        envs = await adapter.handle_webhook(payload)
        assert envs == []
        assert received == []

    @pytest.mark.asyncio
    async def test_send_posts_to_graph_api(self):
        reg, _ = collector()
        adapter = self._adapter(reg)
        resp = MagicMock(status_code=200)
        adapter._client = MagicMock()
        adapter._client.post = AsyncMock(return_value=resp)
        await adapter.send(Envelope.to("whatsapp:15551230000", "reply text"))
        args, kwargs = adapter._client.post.call_args
        assert "PNID/messages" in args[0]
        assert kwargs["headers"]["Authorization"] == "Bearer ATOK"
        assert kwargs["json"]["to"] == "15551230000"
        assert kwargs["json"]["text"]["body"] == "reply text"


# =============================================================================
# SMS (Twilio)
# =============================================================================
class TestSMS:
    def _adapter(self, reg):
        from openpup.platforms.sms_adapter import SMSAdapter

        s = make_settings(
            SMS_ENABLED=True,
            TWILIO_ACCOUNT_SID="ACxxx",
            TWILIO_AUTH_TOKEN="tok",
            TWILIO_FROM_NUMBER="+15550009999",
        )
        return SMSAdapter(s, reg)

    @pytest.mark.asyncio
    async def test_handle_webhook(self):
        reg, received = collector()
        adapter = self._adapter(reg)
        form = {"Body": "txt msg", "From": "+15551112222", "MessageSid": "SM1"}
        env = await adapter.handle_webhook(form)
        assert env is not None
        assert received[0].channel == "+15551112222"
        assert received[0].text == "txt msg"

    @pytest.mark.asyncio
    async def test_handle_webhook_empty_ignored(self):
        reg, received = collector()
        adapter = self._adapter(reg)
        assert await adapter.handle_webhook({}) is None
        assert received == []

    @pytest.mark.asyncio
    async def test_send_calls_twilio(self):
        reg, _ = collector()
        adapter = self._adapter(reg)
        adapter._client = MagicMock()
        await adapter.send(Envelope.to("sms:+15551112222", "outbound sms"))
        adapter._client.messages.create.assert_called_once()
        _, kwargs = adapter._client.messages.create.call_args
        assert kwargs["to"] == "+15551112222"
        assert kwargs["from_"] == "+15550009999"
        assert kwargs["body"] == "outbound sms"


# =============================================================================
# Email
# =============================================================================
class TestEmail:
    def _adapter(self, reg):
        from openpup.platforms.email_adapter import EmailAdapter

        s = make_settings(
            EMAIL_ENABLED=True,
            EMAIL_IMAP_HOST="imap.x.com",
            EMAIL_SMTP_HOST="smtp.x.com",
            EMAIL_USERNAME="me@x.com",
            EMAIL_PASSWORD="pw",
        )
        return EmailAdapter(s, reg)

    @pytest.mark.asyncio
    async def test_send_prefixes_re_and_calls_smtp(self, monkeypatch):
        reg, _ = collector()
        adapter = self._adapter(reg)
        import aiosmtplib

        sent = {}

        async def fake_send(message, **kwargs):
            sent["subject"] = message["Subject"]
            sent["to"] = message["To"]
            sent["kwargs"] = kwargs

        monkeypatch.setattr(aiosmtplib, "send", fake_send)
        env = Envelope.to("email:friend@x.com", "the body", subject="Question")
        await adapter.send(env)
        assert sent["to"] == "friend@x.com"
        assert sent["subject"] == "Re: Question"
        assert sent["kwargs"]["hostname"] == "smtp.x.com"

    def _patch_mailbox(self, monkeypatch, messages):
        """Patch imap_tools.MailBox to return ``messages`` from fetch()."""
        import imap_tools

        class FakeMailbox:
            def __init__(self, host, port):
                pass

            def login(self, user, pw):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def fetch(self, *a, **k):
                # Read-only sensor must never mark messages seen.
                assert k.get("mark_seen") is False
                return list(messages)

        monkeypatch.setattr(imap_tools, "MailBox", FakeMailbox)

    def _msg(self, uid, subject="Hi", body="email body text"):
        return SimpleNamespace(
            from_="sender@x.com",
            subject=subject,
            uid=uid,
            text=body,
            html="",
            date=None,
        )

    @pytest.mark.asyncio
    async def test_is_one_way_no_poll_once_or_dispatch(self, monkeypatch):
        """Email is a read-only sensor: no inbound poll loop, no auto-reply."""
        reg, received = collector()
        adapter = self._adapter(reg)
        # Lifecycle is a no-op (no background task) and there is no poll_once.
        await adapter.start()
        await adapter.stop()
        assert not hasattr(adapter, "poll_once")
        assert received == []  # nothing ever dispatched inbound

    @pytest.mark.asyncio
    async def test_fetch_recent_read_only(self, monkeypatch):
        reg, _ = collector()
        adapter = self._adapter(reg)
        self._patch_mailbox(monkeypatch, [self._msg("42")])
        items = await adapter.fetch_recent(limit=5)
        assert len(items) == 1
        assert items[0]["from_addr"] == "sender@x.com"
        assert items[0]["subject"] == "Hi"
        assert items[0]["uid"] == "42"

    @pytest.mark.asyncio
    async def test_only_new_watermark(self, monkeypatch, tmp_path):
        reg, _ = collector()
        adapter = self._adapter(reg)
        monkeypatch.setattr(
            type(adapter.settings), "state_dir", property(lambda self: tmp_path)
        )

        # First watched check establishes the watermark, returns nothing.
        self._patch_mailbox(monkeypatch, [self._msg("42")])
        assert await adapter.fetch_recent(only_new=True) == []

        # No new mail -> still nothing.
        self._patch_mailbox(monkeypatch, [self._msg("42")])
        assert await adapter.fetch_recent(only_new=True) == []

        # A newer UID arrives -> reported exactly once.
        self._patch_mailbox(monkeypatch, [self._msg("43", subject="New"), self._msg("42")])
        new = await adapter.fetch_recent(only_new=True)
        assert [i["uid"] for i in new] == ["43"]

        # Re-checking the same inbox -> nothing new again.
        self._patch_mailbox(monkeypatch, [self._msg("43", subject="New"), self._msg("42")])
        assert await adapter.fetch_recent(only_new=True) == []

    def _patch_search_mailbox(self, monkeypatch, messages):
        """Patch imap_tools for search: capture the criteria passed to fetch()."""
        import imap_tools

        record = {"criteria": None, "kwargs": None}
        # Collapse AND(**kw) to the kwargs dict so we can assert on the filters.
        monkeypatch.setattr(imap_tools, "AND", lambda **kw: kw)

        class FakeMailbox:
            def __init__(self, host, port):
                pass

            def login(self, user, pw):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def fetch(self, criteria=None, **k):
                record["criteria"] = criteria
                record["kwargs"] = k
                assert k.get("mark_seen") is False  # search never marks seen
                return list(messages)

        monkeypatch.setattr(imap_tools, "MailBox", FakeMailbox)
        return record

    @pytest.mark.asyncio
    async def test_search_builds_criteria_and_maps_items(self, monkeypatch):
        reg, _ = collector()
        adapter = self._adapter(reg)
        record = self._patch_search_mailbox(
            monkeypatch, [self._msg("7", subject="Invoice")]
        )
        items = await adapter.search(query="invoice", from_addr="amazon.com", limit=5)
        assert [i["uid"] for i in items] == ["7"]
        assert items[0]["subject"] == "Invoice"
        assert record["criteria"] == {"text": "invoice", "from_": "amazon.com"}
        assert record["kwargs"]["limit"] == 5
        assert record["kwargs"]["reverse"] is True

    @pytest.mark.asyncio
    async def test_search_unread_adds_seen_false(self, monkeypatch):
        reg, _ = collector()
        adapter = self._adapter(reg)
        record = self._patch_search_mailbox(monkeypatch, [self._msg("7")])
        await adapter.search(query="invoice", unread=True)
        assert record["criteria"] == {"text": "invoice", "seen": False}

    @pytest.mark.asyncio
    async def test_search_unread_only_no_other_filters(self, monkeypatch):
        reg, _ = collector()
        adapter = self._adapter(reg)
        record = self._patch_search_mailbox(monkeypatch, [self._msg("7")])
        await adapter.search(unread=True)
        assert record["criteria"] == {"seen": False}

    @pytest.mark.asyncio
    async def test_search_since_days_sets_date_filter(self, monkeypatch):
        import datetime as _dt

        reg, _ = collector()
        adapter = self._adapter(reg)
        record = self._patch_search_mailbox(monkeypatch, [])
        await adapter.search(since_days=3)
        assert "date_gte" in record["criteria"]
        expected = _dt.date.today() - _dt.timedelta(days=3)
        assert record["criteria"]["date_gte"] == expected

    @pytest.mark.asyncio
    async def test_search_no_filters_returns_recent(self, monkeypatch):
        reg, _ = collector()
        adapter = self._adapter(reg)
        record = self._patch_search_mailbox(monkeypatch, [self._msg("1"), self._msg("2")])
        items = await adapter.search()
        assert len(items) == 2
        # No filters -> AND(all=True) -> the "ALL" criteria.
        assert record["criteria"] == {"all": True}

    @pytest.mark.asyncio
    async def test_fetch_unread_uses_seen_false_and_maps(self, monkeypatch):
        reg, _ = collector()
        adapter = self._adapter(reg)
        record = self._patch_search_mailbox(
            monkeypatch, [self._msg("3", subject="Unseen")]
        )
        items = await adapter.fetch_unread(limit=7)
        assert [i["uid"] for i in items] == ["3"]
        # Filters to unread only, newest first, and never marks seen.
        assert record["criteria"] == {"seen": False}
        assert record["kwargs"]["limit"] == 7
        assert record["kwargs"]["reverse"] is True

    def _patch_delete_mailbox(self, monkeypatch, messages, folders=(("INBOX", ()), ("Trash", ()))):
        """Patch imap_tools for delete: fetch filters by uid, record move/delete.

        ``folders`` is a list of ``(name, flags)`` advertised by the fake
        server, so trash-folder resolution can be exercised.
        """
        import imap_tools

        record = {"moved": None, "deleted": None, "move_folder": None}
        # Make AND(uid=...) collapse to just the requested uid list so the fake
        # fetch can filter on it without parsing IMAP query syntax.
        monkeypatch.setattr(imap_tools, "AND", lambda **kw: kw.get("uid", []))

        folder_infos = [SimpleNamespace(name=n, flags=f) for n, f in folders]

        class FakeFolderManager:
            def list(self):
                return list(folder_infos)

        class FakeMailbox:
            folder = FakeFolderManager()

            def __init__(self, host, port):
                pass

            def login(self, user, pw):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def fetch(self, criteria=None, **k):
                wanted = set(criteria or [])
                return [m for m in messages if m.uid in wanted]

            def move(self, uids, folder):
                record["moved"] = list(uids)
                record["move_folder"] = folder

            def delete(self, uids):
                record["deleted"] = list(uids)

        monkeypatch.setattr(imap_tools, "MailBox", FakeMailbox)
        return record

    @pytest.mark.asyncio
    async def test_delete_moves_to_trash_by_default(self, monkeypatch):
        reg, _ = collector()
        adapter = self._adapter(reg)  # EMAIL_TRASH_FOLDER defaults to "Trash"
        record = self._patch_delete_mailbox(
            monkeypatch, [self._msg("10", subject="Spam"), self._msg("11", subject="More")]
        )
        # Ask to delete one real uid and one stale uid.
        res = await adapter.delete(["10", "999"])
        assert res["mode"] == "trash"
        assert res["deleted"] == 1
        assert res["uids"] == ["10"]
        assert res["subjects"] == ["Spam"]
        assert res["missing"] == ["999"]
        # Reversible path used; nothing permanently expunged.
        assert record["moved"] == ["10"]
        assert record["move_folder"] == "Trash"
        assert record["deleted"] is None

    @pytest.mark.asyncio
    async def test_delete_autodetects_gmail_trash(self, monkeypatch):
        """Configured 'Trash' doesn't exist on Gmail; resolve via \\Trash flag."""
        reg, _ = collector()
        adapter = self._adapter(reg)  # EMAIL_TRASH_FOLDER defaults to "Trash"
        record = self._patch_delete_mailbox(
            monkeypatch,
            [self._msg("10", subject="Spam")],
            folders=[
                ("INBOX", ("\\HasNoChildren",)),
                ("[Gmail]/Trash", ("\\HasNoChildren", "\\Trash")),
            ],
        )
        res = await adapter.delete(["10"])
        assert res["mode"] == "trash"
        # Did NOT blindly use "Trash"; followed the \Trash special-use flag.
        assert record["move_folder"] == "[Gmail]/Trash"

    @pytest.mark.asyncio
    async def test_delete_raises_clear_error_when_no_trash(self, monkeypatch):
        reg, _ = collector()
        adapter = self._adapter(reg)
        self._patch_delete_mailbox(
            monkeypatch,
            [self._msg("10")],
            folders=[("INBOX", ()), ("Archive", ())],
        )
        with pytest.raises(ValueError) as exc:
            await adapter.delete(["10"])
        # Error is actionable: names the real folders + the permanent escape hatch.
        assert "EMAIL_TRASH_FOLDER" in str(exc.value)
        assert "Archive" in str(exc.value)
        assert "permanent" in str(exc.value)

    @pytest.mark.asyncio
    async def test_delete_permanent_expunges(self, monkeypatch):
        reg, _ = collector()
        adapter = self._adapter(reg)
        record = self._patch_delete_mailbox(monkeypatch, [self._msg("10", subject="Spam")])
        res = await adapter.delete(["10"], permanent=True)
        assert res["mode"] == "permanent"
        assert res["deleted"] == 1
        assert record["deleted"] == ["10"]
        assert record["moved"] is None

    @pytest.mark.asyncio
    async def test_delete_nothing_when_no_match(self, monkeypatch):
        reg, _ = collector()
        adapter = self._adapter(reg)
        record = self._patch_delete_mailbox(monkeypatch, [self._msg("10")])
        res = await adapter.delete(["999"])
        assert res["deleted"] == 0
        assert res["mode"] == "none"
        assert res["missing"] == ["999"]
        assert record["moved"] is None and record["deleted"] is None


# =============================================================================
# Webhook server (FastAPI) for WhatsApp + SMS inbound
# =============================================================================
class TestWebhookServer:
    def _setup(self):
        from openpup.platforms.sms_adapter import SMSAdapter
        from openpup.platforms.whatsapp_adapter import WhatsAppAdapter
        from openpup.webserver import WebhookServer

        s = make_settings(
            OPENPUP_WEB_ENABLED=True,
            WHATSAPP_ENABLED=True,
            WHATSAPP_PHONE_NUMBER_ID="PNID",
            WHATSAPP_ACCESS_TOKEN="ATOK",
            WHATSAPP_VERIFY_TOKEN="VTOK",
            SMS_ENABLED=True,
            TWILIO_ACCOUNT_SID="ACxxx",
            TWILIO_AUTH_TOKEN="tok",
            TWILIO_FROM_NUMBER="+15550009999",
        )
        reg, received = collector()
        reg.register(WhatsAppAdapter(s, reg))
        reg.register(SMSAdapter(s, reg))
        server = WebhookServer(s, reg)
        return server, received

    def _client(self, app):
        from fastapi.testclient import TestClient

        return TestClient(app)

    def test_healthz(self):
        server, _ = self._setup()
        client = self._client(server.app)
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert set(r.json()["platforms"]) == {"whatsapp", "sms"}

    def test_whatsapp_verify_handshake(self):
        server, _ = self._setup()
        client = self._client(server.app)
        r = client.get(
            "/webhook/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "VTOK",
                "hub.challenge": "CHALLENGE123",
            },
        )
        assert r.status_code == 200
        assert r.text == "CHALLENGE123"

    def test_whatsapp_verify_wrong_token(self):
        server, _ = self._setup()
        client = self._client(server.app)
        r = client.get(
            "/webhook/whatsapp",
            params={"hub.mode": "subscribe", "hub.verify_token": "WRONG", "hub.challenge": "x"},
        )
        assert r.status_code == 403

    def test_whatsapp_inbound_post(self):
        server, received = self._setup()
        client = self._client(server.app)
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "type": "text",
                                        "from": "15551230000",
                                        "id": "w1",
                                        "text": {"body": "hello"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        r = client.post("/webhook/whatsapp", json=payload)
        assert r.status_code == 200
        assert received[0].text == "hello"
        assert received[0].platform == "whatsapp"

    def test_sms_inbound_post(self):
        server, received = self._setup()
        client = self._client(server.app)
        r = client.post(
            "/webhook/sms", data={"Body": "sms in", "From": "+15551112222", "MessageSid": "SM9"}
        )
        assert r.status_code == 200
        assert "<Response>" in r.text
        assert received[0].text == "sms in"
        assert received[0].platform == "sms"
