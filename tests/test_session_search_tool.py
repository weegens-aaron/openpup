"""Tests for the openpup_session_search agent tool (shape inference + gating)."""

import pytest

from openpup import access, agent_tools
from openpup import sessions as sessions_mod
from openpup.sessions import SessionStore


@pytest.fixture(autouse=True)
def _as_owner():
    """Default tests to owner privileges; individual tests can override."""
    access.set_current_role(access.OWNER)
    yield
    access.set_current_role(access.ALLOWED)


class FakeAgent:
    """Captures @agent.tool-decorated functions by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, func):
        self.tools[func.__name__] = func
        return func


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = SessionStore(tmp_path / "sessions.db")
    monkeypatch.setattr(sessions_mod, "get_session_store", lambda: s)
    return s


@pytest.fixture
def tool(store):
    agent = FakeAgent()
    agent_tools.register_session_search(agent)
    return agent.tools["openpup_session_search"]


def _seed(store):
    store.append("sess-a", "telegram", "user", "let's plan the picnic on saturday")
    store.append("sess-a", "telegram", "assistant", "picnic sounds great, I'll find a park")
    store.append("sess-b", "email", "user", "remember to renew the domain")
    store.append("sess-b", "email", "assistant", "domain renewal noted")


# ---- shape inference -------------------------------------------------------
@pytest.mark.asyncio
async def test_browse_mode_when_no_args(store, tool):
    _seed(store)
    out = await tool(None)
    assert out.error is None
    assert out.mode == "browse"
    assert {r["session_id"] for r in out.results} == {"sess-a", "sess-b"}


@pytest.mark.asyncio
async def test_discover_mode_with_query(store, tool):
    _seed(store)
    out = await tool(None, query="picnic")
    assert out.error is None
    assert out.mode == "discover"
    assert len(out.results) == 1  # deduped to best hit per session
    hit = out.results[0]
    assert hit["session_id"] == "sess-a"
    assert "picnic" in hit["snippet"]
    # each hit carries +/-window context around the matched message
    assert any("park" in m["content"] for m in hit["context"])


@pytest.mark.asyncio
async def test_read_mode_with_session_id(store, tool):
    _seed(store)
    out = await tool(None, session_id="sess-b")
    assert out.error is None
    assert out.mode == "read"
    payload = out.results[0]
    assert payload["session"]["id"] == "sess-b"
    assert len(payload["messages"]) == 2


@pytest.mark.asyncio
async def test_scroll_mode_with_anchor(store, tool):
    ids = [store.append("sess-c", "cli", "user", f"message {i}") for i in range(7)]
    out = await tool(None, session_id="sess-c", around_message_id=ids[3], window=1)
    assert out.error is None
    assert out.mode == "scroll"
    assert [m["content"] for m in out.results] == ["message 2", "message 3", "message 4"]
    assert out.message.startswith("3 messages")


@pytest.mark.asyncio
async def test_scroll_requires_session_id(store, tool):
    out = await tool(None, around_message_id=1)
    assert out.error is not None
    assert "session_id" in out.error


@pytest.mark.asyncio
async def test_session_id_wins_over_query(store, tool):
    """Ambiguous shape: session_id takes precedence (read, not discover)."""
    _seed(store)
    out = await tool(None, query="picnic", session_id="sess-b")
    assert out.mode == "read"


# ---- graceful errors -------------------------------------------------------
@pytest.mark.asyncio
async def test_read_unknown_session_sets_error(store, tool):
    out = await tool(None, session_id="nope")
    assert out.mode == "read"
    assert "not found" in out.error


@pytest.mark.asyncio
async def test_discover_no_matches_is_not_an_error(store, tool):
    _seed(store)
    out = await tool(None, query="zeppelin")
    assert out.error is None
    assert out.mode == "discover"
    assert out.results == []
    assert "No transcripts" in out.message


# ---- clamps ----------------------------------------------------------------
class RecordingStore:
    """Stub store that records the clamped args the tool passes through."""

    def __init__(self):
        self.calls = {}

    def search(self, query, limit=5, **_):
        self.calls["search_limit"] = limit
        return []

    def messages_around(self, session_id, message_id, window=5):
        self.calls["window"] = window
        return {"messages": [], "messages_before": 0, "messages_after": 0}

    def recent_sessions(self, limit=5):
        self.calls["recent_limit"] = limit
        return []


@pytest.fixture
def recording(monkeypatch):
    rec = RecordingStore()
    monkeypatch.setattr(sessions_mod, "get_session_store", lambda: rec)
    agent = FakeAgent()
    agent_tools.register_session_search(agent)
    return rec, agent.tools["openpup_session_search"]


@pytest.mark.asyncio
async def test_window_clamped_to_1_20(recording):
    rec, tool = recording
    await tool(None, session_id="s", around_message_id=1, window=999)
    assert rec.calls["window"] == 20
    await tool(None, session_id="s", around_message_id=1, window=-3)
    assert rec.calls["window"] == 1


@pytest.mark.asyncio
async def test_limit_clamped_to_1_10(recording):
    rec, tool = recording
    await tool(None, query="x", limit=999)
    assert rec.calls["search_limit"] == 10
    await tool(None, limit=0)
    assert rec.calls["recent_limit"] == 1


# ---- owner gating ----------------------------------------------------------
@pytest.mark.asyncio
async def test_blocked_for_non_owner(store, tool):
    _seed(store)
    access.set_current_role(access.ALLOWED)  # non-owner
    out = await tool(None, query="picnic")
    assert out.results == []
    assert "owner" in out.error.lower()
