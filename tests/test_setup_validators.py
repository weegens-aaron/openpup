"""Tests for the setup wizard's live validators (HTTP mocked)."""

import pytest

from openpup.setup import validators


class FakeResp:
    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data or {}
        self.text = text

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self._resp

    async def post(self, *a, **k):
        return self._resp


def patch_httpx(monkeypatch, resp):
    monkeypatch.setattr(validators.httpx, "AsyncClient", lambda *a, **k: FakeClient(resp))


# ---- empty-input guards --------------------------------------------------
@pytest.mark.asyncio
async def test_telegram_empty_token():
    ok, _ = await validators.validate_telegram("")
    assert ok is False


@pytest.mark.asyncio
async def test_twilio_missing_field():
    ok, _ = await validators.validate_twilio("sid", "")
    assert ok is False


# ---- Telegram ------------------------------------------------------------
@pytest.mark.asyncio
async def test_telegram_ok(monkeypatch):
    patch_httpx(
        monkeypatch,
        FakeResp(200, {"ok": True, "result": {"username": "pupbot", "first_name": "Pup"}}),
    )
    ok, detail = await validators.validate_telegram("123:abc")
    assert ok is True
    assert "@pupbot" in detail


@pytest.mark.asyncio
async def test_telegram_bad(monkeypatch):
    patch_httpx(monkeypatch, FakeResp(200, {"ok": False, "description": "Unauthorized"}))
    ok, detail = await validators.validate_telegram("123:bad")
    assert ok is False
    assert "Unauthorized" in detail


@pytest.mark.asyncio
async def test_telegram_discover_chat_id(monkeypatch):
    patch_httpx(monkeypatch, FakeResp(200, {"result": [{"message": {"chat": {"id": 555}}}]}))
    chat_id = await validators.telegram_discover_chat_id("123:abc")
    assert chat_id == "555"


@pytest.mark.asyncio
async def test_telegram_discover_chat_id_empty(monkeypatch):
    patch_httpx(monkeypatch, FakeResp(200, {"result": []}))
    assert await validators.telegram_discover_chat_id("123:abc") is None


# ---- Discord -------------------------------------------------------------
@pytest.mark.asyncio
async def test_discord_ok(monkeypatch):
    patch_httpx(monkeypatch, FakeResp(200, {"username": "openpup", "discriminator": "0"}))
    ok, detail = await validators.validate_discord("tok")
    assert ok is True
    assert "openpup" in detail


@pytest.mark.asyncio
async def test_discord_bad(monkeypatch):
    patch_httpx(monkeypatch, FakeResp(401, {}, text="401: Unauthorized"))
    ok, _ = await validators.validate_discord("badtok")
    assert ok is False


# ---- Twilio --------------------------------------------------------------
@pytest.mark.asyncio
async def test_twilio_ok(monkeypatch):
    patch_httpx(monkeypatch, FakeResp(200, {"friendly_name": "My Acct", "status": "active"}))
    ok, detail = await validators.validate_twilio("ACxx", "tok")
    assert ok is True
    assert "My Acct" in detail


# ---- WhatsApp ------------------------------------------------------------
@pytest.mark.asyncio
async def test_whatsapp_ok(monkeypatch):
    patch_httpx(
        monkeypatch, FakeResp(200, {"verified_name": "Biz", "display_phone_number": "+1 555"})
    )
    ok, detail = await validators.validate_whatsapp("PNID", "tok")
    assert ok is True
    assert "Biz" in detail


@pytest.mark.asyncio
async def test_whatsapp_bad(monkeypatch):
    patch_httpx(monkeypatch, FakeResp(400, {}, text="bad token"))
    ok, _ = await validators.validate_whatsapp("PNID", "badtok")
    assert ok is False


# ---- Email ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_email_ok(monkeypatch):
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

    monkeypatch.setattr(imap_tools, "MailBox", FakeMailbox)
    ok, detail = await validators.validate_email("imap.x.com", 993, "me@x.com", "pw")
    assert ok is True
    assert "me@x.com" in detail


@pytest.mark.asyncio
async def test_email_login_failure(monkeypatch):
    import imap_tools

    class FakeMailbox:
        def __init__(self, host, port):
            pass

        def login(self, user, pw):
            raise RuntimeError("auth failed")

    monkeypatch.setattr(imap_tools, "MailBox", FakeMailbox)
    ok, detail = await validators.validate_email("imap.x.com", 993, "me@x.com", "wrong")
    assert ok is False
    assert "failed" in detail.lower()
