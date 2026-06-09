"""AgentHost transient-error retry (handles streaming connection drops)."""

from types import SimpleNamespace

import httpx
import pytest

from openpup.agent_host import AgentHost, _is_transient


def test_is_transient_detects_remote_protocol_error():
    exc = httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body (incomplete chunked read)"
    )
    assert _is_transient(exc)


def test_is_transient_detects_wrapped_chain():
    inner = httpx.RemoteProtocolError("peer closed connection")
    try:
        try:
            raise inner
        except Exception as e:
            raise RuntimeError("agent run failed") from e
    except RuntimeError as outer:
        assert _is_transient(outer)


def test_is_transient_detects_exception_group():
    inner = httpx.ReadError("connection reset")
    group = ExceptionGroup("boom", [inner])
    assert _is_transient(group)


def test_is_transient_false_for_logic_error():
    assert not _is_transient(ValueError("bad argument"))


class _FakeAgent:
    def __init__(self, fail_times, exc):
        self.fail_times = fail_times
        self.exc = exc
        self.calls = 0
        self._hist = []

    def set_message_history(self, h):
        self._hist = list(h)

    def get_message_history(self):
        return self._hist

    async def run_with_mcp(self, prompt):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return SimpleNamespace(output="ok")


@pytest.mark.asyncio
async def test_run_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr("openpup.agent_host.asyncio.sleep", _fast_sleep)
    host = AgentHost(max_retries=3)
    host._agent = _FakeAgent(2, httpx.RemoteProtocolError("incomplete chunked read"))
    out = await host.run("hi", conversation="telegram:1")
    assert out == "ok"
    assert host._agent.calls == 3


@pytest.mark.asyncio
async def test_run_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr("openpup.agent_host.asyncio.sleep", _fast_sleep)
    host = AgentHost(max_retries=2)
    host._agent = _FakeAgent(5, httpx.RemoteProtocolError("peer closed connection"))
    with pytest.raises(httpx.RemoteProtocolError):
        await host.run("hi")
    assert host._agent.calls == 2


@pytest.mark.asyncio
async def test_run_does_not_retry_logic_error(monkeypatch):
    monkeypatch.setattr("openpup.agent_host.asyncio.sleep", _fast_sleep)
    host = AgentHost(max_retries=3)
    host._agent = _FakeAgent(5, ValueError("nope"))
    with pytest.raises(ValueError):
        await host.run("hi")
    assert host._agent.calls == 1


async def _fast_sleep(_seconds):
    return None
