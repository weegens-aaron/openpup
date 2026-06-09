"""Tests for the agentic todo tool (ported from hermes)."""

import pytest

from openpup import agentic
from openpup.agentic import TodoItem, TodoStore


class FakeAgent:
    def __init__(self):
        self.tools = {}

    def tool(self, func):
        self.tools[func.__name__] = func
        return func


def test_store_replace_and_read():
    s = TodoStore()
    s.write([{"id": "1", "content": "do a", "status": "pending"}])
    items = s.read()
    assert len(items) == 1 and items[0]["content"] == "do a"


def test_store_merge_updates_status():
    s = TodoStore()
    s.write([{"id": "1", "content": "a", "status": "pending"}])
    s.write([{"id": "1", "status": "completed"}], merge=True)
    assert s.read()[0]["status"] == "completed"


def test_store_invalid_status_defaults_pending():
    s = TodoStore()
    s.write([{"id": "1", "content": "a", "status": "bogus"}])
    assert s.read()[0]["status"] == "pending"


def test_item_cap():
    s = TodoStore()
    s.write([{"id": "1", "content": "x" * 5000, "status": "pending"}])
    assert len(s.read()[0]["content"]) <= agentic.MAX_TODO_CONTENT_CHARS


@pytest.mark.asyncio
async def test_tool_read_write_per_conversation():
    agent = FakeAgent()
    agentic.register_todo(agent)
    tool = agent.tools["openpup_todo"]

    agentic.set_conversation("telegram:1")
    out = await tool(None, todos=[TodoItem(id="1", content="task one", status="in_progress")])
    assert out.total == 1 and out.in_progress == 1

    # different conversation has its own empty list
    agentic.set_conversation("telegram:2")
    out2 = await tool(None)
    assert out2.total == 0

    # back to first conversation, list persists
    agentic.set_conversation("telegram:1")
    out3 = await tool(None)
    assert out3.total == 1 and out3.todos[0].content == "task one"

    agentic.reset_conversation_todos("telegram:1")


def test_advertise_and_registry():
    assert agentic.advertise_tools() == ["openpup_todo"]
    defs = agentic.register_tools_callback()
    assert defs[0]["name"] == "openpup_todo"
    assert callable(defs[0]["register_func"])
