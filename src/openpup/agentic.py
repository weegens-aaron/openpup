"""Agentic task list (todo) tool, ported from hermes-agent's todo_tool.

A per-conversation task list the agent maintains to decompose complex requests,
track progress, and stay focused across long multi-step work. This is the
loop-discipline primitive that makes the agent plan -> execute -> verify rather
than stopping after a stub.

State is kept per conversation (keyed by the OpenPup envelope address) via a
contextvar the AgentHost sets before each run, so separate chats don't share a
task list.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import RunContext

VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
MAX_TODO_CONTENT_CHARS = 4000
MAX_TODO_ITEMS = 256
_TRUNCATION_MARKER = "... [truncated]"

# Which conversation's task list is active for the run in progress.
_current_conversation: ContextVar[str] = ContextVar("openpup_conversation", default="default")


def set_conversation(conversation: str) -> None:
    _current_conversation.set(conversation)


class TodoStore:
    """In-memory ordered task list (list position == priority)."""

    def __init__(self) -> None:
        self._items: List[Dict[str, str]] = []

    def write(self, todos: List[Dict[str, Any]], merge: bool = False) -> List[Dict[str, str]]:
        if not merge:
            self._items = [self._validate(t) for t in self._dedupe_by_id(todos)]
        else:
            existing = {item["id"]: item for item in self._items}
            for t in self._dedupe_by_id(todos):
                item_id = str(t.get("id", "")).strip()
                if not item_id:
                    continue
                if item_id in existing:
                    if t.get("content"):
                        existing[item_id]["content"] = self._cap(str(t["content"]).strip())
                    if t.get("status"):
                        status = str(t["status"]).strip().lower()
                        if status in VALID_STATUSES:
                            existing[item_id]["status"] = status
                else:
                    validated = self._validate(t)
                    existing[validated["id"]] = validated
                    self._items.append(validated)
            seen: set = set()
            rebuilt: List[Dict[str, str]] = []
            for item in self._items:
                current = existing.get(item["id"], item)
                if current["id"] not in seen:
                    rebuilt.append(current)
                    seen.add(current["id"])
            self._items = rebuilt
        if len(self._items) > MAX_TODO_ITEMS:
            self._items = self._items[:MAX_TODO_ITEMS]
        return self.read()

    def read(self) -> List[Dict[str, str]]:
        return [item.copy() for item in self._items]

    @staticmethod
    def _cap(content: str) -> str:
        if len(content) > MAX_TODO_CONTENT_CHARS:
            keep = MAX_TODO_CONTENT_CHARS - len(_TRUNCATION_MARKER)
            return content[:keep] + _TRUNCATION_MARKER
        return content

    @staticmethod
    def _validate(item: Dict[str, Any]) -> Dict[str, str]:
        item_id = str(item.get("id", "")).strip() or "?"
        content = str(item.get("content", "")).strip() or "(no description)"
        content = TodoStore._cap(content)
        status = str(item.get("status", "pending")).strip().lower()
        if status not in VALID_STATUSES:
            status = "pending"
        return {"id": item_id, "content": content, "status": status}

    @staticmethod
    def _dedupe_by_id(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        last_index: Dict[str, int] = {}
        for i, item in enumerate(todos):
            item_id = str(item.get("id", "")).strip() or "?"
            last_index[item_id] = i
        return [todos[i] for i in sorted(last_index.values())]


# One store per conversation.
_stores: Dict[str, TodoStore] = {}


def _store_for_current() -> TodoStore:
    key = _current_conversation.get()
    return _stores.setdefault(key, TodoStore())


def reset_conversation_todos(conversation: str) -> None:
    _stores.pop(conversation, None)


# --------------------------------------------------------------------------
# Tool surface
# --------------------------------------------------------------------------
class TodoItem(BaseModel):
    id: str
    content: str
    status: str = "pending"


class TodoOutput(BaseModel):
    todos: List[TodoItem] = Field(default_factory=list)
    total: int = 0
    pending: int = 0
    in_progress: int = 0
    completed: int = 0
    cancelled: int = 0


def _to_output(items: List[Dict[str, str]]) -> TodoOutput:
    return TodoOutput(
        todos=[TodoItem(**i) for i in items],
        total=len(items),
        pending=sum(1 for i in items if i["status"] == "pending"),
        in_progress=sum(1 for i in items if i["status"] == "in_progress"),
        completed=sum(1 for i in items if i["status"] == "completed"),
        cancelled=sum(1 for i in items if i["status"] == "cancelled"),
    )


def register_todo(agent: Any) -> None:
    @agent.tool
    async def openpup_todo(
        context: RunContext,
        todos: Optional[List[TodoItem]] = None,
        merge: bool = False,
    ) -> TodoOutput:
        """Manage your task list for the current request.

        Use this for any task with 3+ steps, or when the user gives you several
        things to do. Plan first, then work the list top-to-bottom.

        - Call with no ``todos`` to READ the current list.
        - Provide ``todos`` to WRITE. Each item: {id, content, status} where
          status is pending | in_progress | completed | cancelled.
        - ``merge=false`` (default) replaces the whole list with a fresh plan.
        - ``merge=true`` updates existing items by id and appends new ones.

        List order is priority. Keep exactly ONE item ``in_progress`` at a time.
        Mark items ``completed`` the moment they're done. If something fails,
        ``cancelled`` it and add a revised item. Always returns the full list.
        """
        store = _store_for_current()
        if todos is not None:
            items = store.write([t.model_dump() for t in todos], merge=merge)
        else:
            items = store.read()
        return _to_output(items)


def register_tools_callback() -> List[dict]:
    return [{"name": "openpup_todo", "register_func": register_todo}]


def advertise_tools(agent_name: Optional[str] = None) -> List[str]:
    return ["openpup_todo"]
