"""SessionStore: transcripts in SQLite, searched via FTS5 (or LIKE fallback)."""

import itertools

import pytest

from openpup import sessions as sessions_mod
from openpup.sessions import SessionStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A store on a temp DB with a deterministic, strictly increasing clock."""
    clock = itertools.count(start=1000)
    monkeypatch.setattr(sessions_mod, "_now", lambda: float(next(clock)))
    return SessionStore(path=tmp_path / "sessions.db")


def _fill(store, session_id, n, source="telegram:1", role="user", prefix="msg"):
    return [store.append(session_id, source, role, f"{prefix} {i}") for i in range(n)]


def test_append_read_round_trip(store):
    mid = store.append("s1", "telegram:12345", "user", "hello pup", title="Greetings")
    assert isinstance(mid, int)
    store.append("s1", "telegram:12345", "assistant", "woof back")

    dump = store.read_session("s1")
    assert dump["session"]["id"] == "s1"
    assert dump["session"]["source"] == "telegram:12345"
    assert dump["session"]["title"] == "Greetings"
    assert dump["truncated"] is False
    assert [m["content"] for m in dump["messages"]] == ["hello pup", "woof back"]
    assert [m["role"] for m in dump["messages"]] == ["user", "assistant"]


def test_fts_search_hit_with_snippet(store):
    if not store.fts_enabled:
        pytest.skip("sqlite build lacks FTS5")
    store.append("s1", "telegram:1", "user", "let's plan the zanzibar trip in detail")
    store.append("s2", "heartbeat", "assistant", "totally unrelated musings")

    hits = store.search("zanzibar")
    assert len(hits) == 1
    hit = hits[0]
    assert hit["session_id"] == "s1"
    assert hit["source"] == "telegram:1"
    assert isinstance(hit["message_id"], int)
    assert "zanzibar" in hit["snippet"]


def test_search_dedupes_by_session(store):
    for i in range(4):
        store.append("chatty", "telegram:1", "user", f"banana smoothie recipe {i}")
    store.append("other", "discord:9", "user", "banana bread instead")

    hits = store.search("banana", limit=5)
    assert len(hits) == 2
    assert {h["session_id"] for h in hits} == {"chatty", "other"}


def test_search_respects_role_filter(store):
    store.append("s1", "telegram:1", "system", "secret xylophone directive")
    assert store.search("xylophone") == []
    assert len(store.search("xylophone", role_filter=("system",))) == 1


def test_messages_around_middle(store):
    ids = _fill(store, "s1", 20)
    anchor = ids[10]

    out = store.messages_around("s1", anchor, window=5)
    assert len(out["messages"]) == 11
    assert out["messages"][5]["id"] == anchor
    assert out["messages_before"] == 5  # 10 before anchor, 5 shown
    assert out["messages_after"] == 4  # 9 after anchor, 5 shown


def test_messages_around_boundaries(store):
    ids = _fill(store, "s1", 6)

    head = store.messages_around("s1", ids[0], window=5)
    assert head["messages"][0]["id"] == ids[0]
    assert head["messages_before"] == 0
    assert head["messages_after"] == 0  # 5 after, all shown

    tail = store.messages_around("s1", ids[-1], window=5)
    assert tail["messages"][-1]["id"] == ids[-1]
    assert tail["messages_after"] == 0
    assert tail["messages_before"] == 0  # 5 before, all shown

    missing = store.messages_around("s1", 99999)
    assert missing == {"messages": [], "messages_before": 0, "messages_after": 0}


def test_read_session_truncation(store):
    _fill(store, "big", 50)

    dump = store.read_session("big", head=20, tail=10)
    assert dump["truncated"] is True
    assert dump["omitted"] == 20
    assert len(dump["messages"]) == 30
    assert dump["messages"][0]["content"] == "msg 0"
    assert dump["messages"][19]["content"] == "msg 19"  # end of head
    assert dump["messages"][20]["content"] == "msg 40"  # start of tail
    assert dump["messages"][-1]["content"] == "msg 49"


def test_read_session_missing(store):
    assert store.read_session("ghost") == {
        "session": None,
        "messages": [],
        "truncated": False,
        "omitted": 0,
    }


def test_recent_sessions_ordering_and_preview(store):
    store.append("a", "telegram:1", "user", "first in a")
    store.append("b", "discord:2", "user", "only in b")
    store.append("a", "telegram:1", "assistant", "x" * 100)  # bumps a to most recent

    recents = store.recent_sessions()
    assert [r["session_id"] for r in recents] == ["a", "b"]
    assert recents[0]["message_count"] == 2
    assert recents[0]["preview"] == "x" * 80 + "…"
    assert recents[1]["preview"] == "only in b"


def test_like_fallback_search(store):
    store.fts_enabled = False  # force the fallback path
    store.append("s1", "telegram:1", "user", "the quick brown fox jumps " * 10)

    hits = store.search("brown fox")
    assert len(hits) == 1
    assert "brown fox" in hits[0]["snippet"]
    assert "…" in hits[0]["snippet"]  # windowed, not the whole message

    # LIKE wildcards in the query must be treated literally.
    assert store.search("100% wool") == []


def test_graceful_degradation_on_broken_db(store):
    store._conn.close()
    assert store.append("s1", "telegram:1", "user", "hello") is None
    assert store.search("hello") == []
    assert store.recent_sessions() == []
    assert store.read_session("s1")["messages"] == []
    assert store.messages_around("s1", 1)["messages"] == []


def test_singleton(monkeypatch, tmp_path):
    monkeypatch.setattr(sessions_mod, "_store", None)
    monkeypatch.setattr(sessions_mod, "default_sessions_path", lambda: tmp_path / "s.db")
    a = sessions_mod.get_session_store()
    b = sessions_mod.get_session_store()
    assert a is b
    assert a.path == tmp_path / "s.db"
