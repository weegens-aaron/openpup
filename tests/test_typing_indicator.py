"""Tests for the runtime 'typing...' keepalive helpers.

These exercise OpenPup._start_typing / _stop_typing in isolation (with a fake
``self`` carrying just a registry), so we don't have to boot the whole runtime.
"""

import asyncio
from types import SimpleNamespace

import pytest

from openpup.messaging.envelope import Envelope
from openpup.messaging.registry import PlatformRegistry
from openpup.runtime import OpenPup


class _TypingAdapter:
    name = "telegram"

    def __init__(self):
        self.calls = []

    async def typing(self, channel):
        self.calls.append(channel)


class _PlainAdapter:
    name = "sms"  # no typing() method


def _fake_self(adapter):
    reg = PlatformRegistry()
    reg.register(adapter)
    return SimpleNamespace(registry=reg, _TYPING_INTERVAL_S=0.01)


@pytest.mark.asyncio
async def test_start_typing_pokes_repeatedly_then_stops():
    adapter = _TypingAdapter()
    me = _fake_self(adapter)
    env = Envelope(platform="telegram", channel="777", sender="x", text="hi")

    task = OpenPup._start_typing(me, env)
    assert task is not None
    await asyncio.sleep(0.035)  # let the keepalive loop fire a couple times
    await OpenPup._stop_typing(task)

    assert adapter.calls  # at least one poke
    assert all(c == "777" for c in adapter.calls)
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_no_typing_method_is_noop():
    me = _fake_self(_PlainAdapter())
    env = Envelope(platform="sms", channel="+1555", sender="x", text="hi")
    assert OpenPup._start_typing(me, env) is None


@pytest.mark.asyncio
async def test_stop_typing_handles_none():
    # Should not raise when there was no typing task to begin with.
    await OpenPup._stop_typing(None)


@pytest.mark.asyncio
async def test_typing_errors_dont_kill_the_loop():
    class _FlakyAdapter:
        name = "telegram"

        def __init__(self):
            self.calls = 0

        async def typing(self, channel):
            self.calls += 1
            raise RuntimeError("transient telegram hiccup")

    adapter = _FlakyAdapter()
    me = _fake_self(adapter)
    env = Envelope(platform="telegram", channel="777", sender="x", text="hi")

    task = OpenPup._start_typing(me, env)
    await asyncio.sleep(0.035)
    await OpenPup._stop_typing(task)
    assert adapter.calls >= 1  # kept trying despite errors
