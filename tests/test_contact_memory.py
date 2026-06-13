"""Tests for per-contact memory wings + improved Telegram identity."""

from types import SimpleNamespace

import pytest

from openpup import access, memory
from openpup.messaging.envelope import Envelope
from openpup.messaging.registry import PlatformRegistry
from openpup.runtime import OpenPup


def test_contact_wing_naming():
    assert memory.contact_wing("telegram:123") == "contact:telegram:123"
    assert memory.contact_wing("sms:+15551112222") == "contact:sms:+15551112222"


def _pup() -> OpenPup:
    from openpup.config import Settings

    return OpenPup(settings=Settings(_env_file=None))


def test_context_prefix_owner():
    env = Envelope(platform="telegram", channel="1", sender="Mike", text="hi")
    prefix = _pup()._context_prefix(env, access.OWNER)
    assert "OWNER" in prefix


def test_context_prefix_non_owner_injects_memory(monkeypatch):
    monkeypatch.setattr(memory, "recent_about_contact", lambda addr, top_k=3: ["likes coffee"])
    env = Envelope(platform="telegram", channel="9", sender="Sara", text="yo")
    prefix = _pup()._context_prefix(env, access.ALLOWED)
    assert "NON-owner" in prefix
    assert "What you remember about Sara" in prefix
    assert "likes coffee" in prefix


def test_context_prefix_no_memory(monkeypatch):
    monkeypatch.setattr(memory, "recent_about_contact", lambda addr, top_k=3: [])
    env = Envelope(platform="telegram", channel="9", sender="Sara", text="yo")
    prefix = _pup()._context_prefix(env, access.ALLOWED)
    assert "What you remember" not in prefix


# ---- Telegram identity ---------------------------------------------------
def _telegram_adapter():
    from openpup.config import Settings
    from openpup.platforms.telegram_adapter import TelegramAdapter

    s = Settings(_env_file=None, TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="123:abc")
    return TelegramAdapter(s, PlatformRegistry())


@pytest.mark.asyncio
async def test_telegram_uses_full_name_and_id():
    reg = PlatformRegistry()
    received = []

    async def handler(env):
        received.append(env)

    reg.set_inbound_handler(handler)

    from openpup.config import Settings
    from openpup.platforms.telegram_adapter import TelegramAdapter

    s = Settings(_env_file=None, TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="123:abc")
    adapter = TelegramAdapter(s, reg)

    update = SimpleNamespace(
        effective_message=SimpleNamespace(text="hello"),
        effective_chat=SimpleNamespace(id=999),
        effective_user=SimpleNamespace(full_name="Mike Smith", username=None, id=999),
    )
    await adapter._on_message(update, None)
    assert received[0].sender == "Mike Smith"
    assert received[0].sender_id == "999"


@pytest.mark.asyncio
async def test_telegram_falls_back_to_id_when_no_name():
    reg = PlatformRegistry()
    received = []
    reg.set_inbound_handler(lambda env: received.append(env) or _noop())

    from openpup.config import Settings
    from openpup.platforms.telegram_adapter import TelegramAdapter

    s = Settings(_env_file=None, TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="123:abc")
    adapter = TelegramAdapter(s, reg)
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text="hi"),
        effective_chat=SimpleNamespace(id=555),
        effective_user=SimpleNamespace(full_name=None, username=None, id=555),
    )
    await adapter._on_message(update, None)
    assert received[0].sender == "555"


async def _noop():
    return None
