"""OpenPup agent tools + identity prompt.

These wire OpenPup's integrations into the code-puppy agent using the same
plugin hooks the kennel uses:

* ``register_tools``       -> defines the tool functions in TOOL_REGISTRY
* ``register_agent_tools`` -> advertises them to the agent's tool list
* ``load_prompt``          -> tells the agent it's OpenPup + what it can do

Result: when you message OpenPup "check my email" or "text my owner", the
agent has real tools to do it instead of replying "I'm just a code puppy".
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from openpup.messaging.envelope import Envelope
from openpup.messaging.registry import get_registry
from openpup.skills.loader import SKILL_TOOL_NAME
from openpup.skills.tool import register_skill_tool


def _is_owner() -> bool:
    """Whether the message currently being served is from the owner."""
    try:
        from openpup.access import current_is_owner

        return current_is_owner()
    except Exception:
        # Fail CLOSED: if the access module can't even be imported, nobody
        # gets owner powers. (Unit tests set the role contextvar explicitly.)
        return False


# --------------------------------------------------------------------------
# Output models
# --------------------------------------------------------------------------
class SendResult(BaseModel):
    ok: bool
    address: str
    error: Optional[str] = None


class EmailItem(BaseModel):
    from_addr: str
    subject: str
    date: str
    preview: str
    uid: str = ""


class EmailList(BaseModel):
    count: int
    emails: List[EmailItem] = Field(default_factory=list)
    error: Optional[str] = None


class DeleteEmailResult(BaseModel):
    ok: bool
    deleted: int = 0
    mode: str = ""  # "trash" | "permanent" | "none"
    uids: List[str] = Field(default_factory=list)
    subjects: List[str] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class PlatformList(BaseModel):
    platforms: List[str]
    owner: Optional[str] = None


class ConfigResult(BaseModel):
    ok: bool
    action: str = ""
    key: Optional[str] = None
    value: Optional[str] = None
    config: Dict[str, str] = Field(default_factory=dict)
    note: Optional[str] = None
    error: Optional[str] = None


class Contact(BaseModel):
    platform: str
    channel: str
    name: str
    address: str


class ContactList(BaseModel):
    contacts: List[Contact] = Field(default_factory=list)
    count: int = 0
    error: Optional[str] = None


class SessionSearchResult(BaseModel):
    mode: str = ""
    results: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""
    error: Optional[str] = None


# --------------------------------------------------------------------------
# Tool registration functions (called with the pydantic agent at build time)
# --------------------------------------------------------------------------
def register_send_message(agent: Any) -> None:
    @agent.tool
    async def openpup_send_message(context: RunContext, address: str, text: str) -> SendResult:
        """Send a message to someone on a connected platform.

        Args:
            address: A ``platform:channel`` target (``telegram:12345``,
                     ``sms:+15551234567``, ``email:friend@example.com``) OR a
                     known contact name (``Mike`` or ``telegram:Mike``). Use
                     ``openpup_contacts`` to see who's reachable.
            text:    The message body to send.

        Governed: owner-only, rate-limited per platform, and subject to the
        configured send policy.
        """
        from openpup.directory import get_directory
        from openpup.governance import get_send_policy, redact

        if not _is_owner():
            return SendResult(
                ok=False,
                address=address,
                error="Only the owner can send messages on OpenPup's behalf.",
            )
        # Resolve a friendly contact name to an address when possible.
        directory = get_directory()
        resolved = directory.resolve(address) or address
        if ":" not in resolved:
            return SendResult(
                ok=False,
                address=address,
                error=f"Couldn't resolve '{address}'. Use 'platform:channel' or a known "
                "contact name (see openpup_contacts).",
            )
        platform = resolved.split(":", 1)[0]
        reg = get_registry()
        if reg.get(platform) is None:
            return SendResult(
                ok=False, address=resolved, error=f"platform '{platform}' is not connected"
            )
        # Governance: send policy + rate limit.
        decision = get_send_policy().check(resolved, directory=directory)
        if not decision.allowed:
            return SendResult(ok=False, address=resolved, error=decision.reason)
        try:
            ok = await reg.send(Envelope.to(resolved, text))
        except Exception as exc:  # noqa: BLE001
            return SendResult(ok=False, address=resolved, error=redact(f"send failed: {exc!r}"))
        return SendResult(ok=ok, address=resolved, error=None if ok else "delivery failed")


def register_check_email(agent: Any) -> None:
    @agent.tool
    async def openpup_check_email(
        context: RunContext, limit: int = 5, only_new: bool = False
    ) -> EmailList:
        """Read recent emails from OpenPup's connected mailbox (read-only).

        Email is a one-way sensor: this NEVER marks anything read and NEVER
        replies. Returns sender, subject, date, and a short preview of each
        message, newest first.

        Args:
            limit: How many recent emails to fetch (1-20, default 5).
            only_new: For recurring 'watch my inbox' checks. When True, returns
                only emails that arrived since the last ``only_new`` check, so
                you never re-report the same message. The first such check just
                starts watching and returns nothing. Use this (not ``limit``)
                for scheduled inbox monitoring; filter the results by the
                owner's topics yourself before notifying them.
        """
        if not _is_owner():
            return EmailList(count=0, error="Only the owner can read the mailbox.")
        reg = get_registry()
        adapter = reg.get("email")
        if adapter is None:
            return EmailList(
                count=0, error="Email is not connected. Set it up with 'openpup setup'."
            )
        try:
            limit = max(1, min(int(limit), 20))
        except (TypeError, ValueError):
            limit = 5
        try:
            items = await adapter.fetch_recent(limit=limit, only_new=only_new)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — tools must never raise
            return EmailList(count=0, error=f"check_email failed: {exc!r}")
        return EmailList(count=len(items), emails=[EmailItem(**i) for i in items])


def register_unread_email(agent: Any) -> None:
    @agent.tool
    async def openpup_unread_email(context: RunContext, limit: int = 10) -> EmailList:
        """List the most recent UNREAD emails (read-only, owner-only).

        Returns only messages still marked unread, newest first, and NEVER
        marks them read just by listing them. Same shape as
        ``openpup_check_email`` (incl. ``uid``), so a result can feed straight
        into ``openpup_delete_email``.

        Args:
            limit: how many unread emails to return (1-50, default 10).
        """
        if not _is_owner():
            return EmailList(count=0, error="Only the owner can read the mailbox.")
        reg = get_registry()
        adapter = reg.get("email")
        if adapter is None:
            return EmailList(
                count=0, error="Email is not connected. Set it up with 'openpup setup'."
            )
        try:
            limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            limit = 10
        try:
            items = await adapter.fetch_unread(limit=limit)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — tools must never raise
            return EmailList(count=0, error=f"unread_email failed: {exc!r}")
        return EmailList(count=len(items), emails=[EmailItem(**i) for i in items])


def register_search_email(agent: Any) -> None:
    @agent.tool
    async def openpup_search_email(
        context: RunContext,
        query: Optional[str] = None,
        from_addr: Optional[str] = None,
        since_days: Optional[int] = None,
        limit: int = 10,
        unread: bool = False,
    ) -> EmailList:
        """Search OpenPup's mailbox (read-only, owner-only).

        Finds emails matching the given filters WITHOUT marking anything read.
        All filters are AND-ed; with none supplied it returns the most recent
        messages. Returns sender, subject, date, preview, and uid (newest
        first) -- the same shape as ``openpup_check_email``, so you can feed a
        result's uid straight into ``openpup_delete_email``.

        Args:
            query: free text matched against the whole message (subject +
                headers + body), e.g. ``"invoice"``.
            from_addr: restrict to a sender substring, e.g. ``"amazon.com"``.
            since_days: only emails from the last N days.
            limit: how many to return (1-50, default 10).
            unread: when True, only UNREAD emails. Combine with the other
                filters, e.g. unread invoices from amazon in the last week.
        """
        if not _is_owner():
            return EmailList(count=0, error="Only the owner can search the mailbox.")
        reg = get_registry()
        adapter = reg.get("email")
        if adapter is None:
            return EmailList(
                count=0, error="Email is not connected. Set it up with 'openpup setup'."
            )
        try:
            limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            limit = 10
        try:
            items = await adapter.search(  # type: ignore[attr-defined]
                query=query,
                from_addr=from_addr,
                since_days=since_days,
                limit=limit,
                unread=bool(unread),
            )
        except Exception as exc:  # noqa: BLE001 — tools must never raise
            return EmailList(count=0, error=f"search_email failed: {exc!r}")
        return EmailList(count=len(items), emails=[EmailItem(**i) for i in items])


def register_delete_email(agent: Any) -> None:
    @agent.tool
    async def openpup_delete_email(
        context: RunContext, uids: List[str], permanent: bool = False
    ) -> DeleteEmailResult:
        """Delete specific emails by UID (owner-only).

        SAFE BY DEFAULT: emails are MOVED TO TRASH (reversible). Only pass
        ``permanent=True`` when the owner explicitly wants them gone forever
        (irreversible expunge).

        You must pass explicit UIDs -- get them from ``openpup_check_email``
        (each email it returns includes a ``uid``). There is intentionally no
        "delete all": only the messages the owner actually identified get
        touched. UIDs that no longer exist are reported back in ``missing``
        rather than silently inflating the deleted count.

        Args:
            uids: the email UIDs to delete (e.g. ``["4123", "4124"]``).
            permanent: when True, expunge forever instead of moving to trash.
        """
        if not _is_owner():
            return DeleteEmailResult(ok=False, error="Only the owner can delete email.")
        # Be forgiving about shape: a lone UID string or ints are fine too.
        if isinstance(uids, (str, int)):
            uids = [uids]
        uid_list = [str(u).strip() for u in (uids or []) if str(u).strip()]
        if not uid_list:
            return DeleteEmailResult(
                ok=False, error="No UIDs given. Fetch emails first to get their uid values."
            )
        reg = get_registry()
        adapter = reg.get("email")
        if adapter is None:
            return DeleteEmailResult(
                ok=False, error="Email is not connected. Set it up with 'openpup setup'."
            )
        try:
            res = await adapter.delete(uid_list, permanent=bool(permanent))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — tools must never raise
            return DeleteEmailResult(ok=False, error=f"delete_email failed: {exc!r}")
        return DeleteEmailResult(
            ok=True,
            deleted=int(res.get("deleted", 0)),
            mode=str(res.get("mode", "")),
            uids=list(res.get("uids", [])),
            subjects=list(res.get("subjects", [])),
            missing=list(res.get("missing", [])),
        )


def register_list_platforms(agent: Any) -> None:
    @agent.tool
    async def openpup_list_platforms(context: RunContext) -> PlatformList:
        """List the messaging/email platforms OpenPup currently has connected,
        plus the owner's address (where proactive messages go). The owner
        address is only included when the owner is asking — it's private."""
        from openpup.config import get_settings

        reg = get_registry()
        owner = get_settings().owner_address if _is_owner() else None
        return PlatformList(platforms=reg.platforms(), owner=owner)


def register_config(agent: Any) -> None:
    @agent.tool
    async def openpup_config(
        context: RunContext,
        action: str = "list",
        key: Optional[str] = None,
        value: Optional[str] = None,
    ) -> ConfigResult:
        """Read or update OpenPup's own configuration (owner-only).

        Config lives in a SQLite store now -- NOT a .env file. Use this instead
        of editing files. Setting a value applies live (no file edits), though
        platform/credential changes still need a restart for the affected
        adapter to reconnect.

        Actions:
            - ``list`` (default): show all current config (secrets masked).
            - ``get``: read one ``key``.
            - ``set``: set ``key`` to ``value`` (key must be a known setting).

        Keys are the uppercase setting names, e.g. ``OPENPUP_OWNER_ADDRESSES``,
        ``OPENPUP_SEND_POLICY``, ``EMAIL_TRASH_FOLDER``. Secret values
        (passwords/tokens) are masked when displayed but can still be set.
        """
        if not _is_owner():
            return ConfigResult(ok=False, error="Only the owner can read or change config.")
        try:
            from openpup.config_store import (
                get_config_store,
                is_secret_key,
                known_config_keys,
            )
        except Exception as exc:  # noqa: BLE001
            return ConfigResult(ok=False, error=f"config store unavailable: {exc!r}")

        def _mask(k: str, v: str) -> str:
            if v and is_secret_key(k):
                return "***set***"
            return v

        store = get_config_store()
        act = (action or "list").strip().lower()

        if act == "list":
            data = {k: _mask(k, v) for k, v in sorted(store.as_dict().items())}
            return ConfigResult(ok=True, action="list", config=data)

        if act == "get":
            if not key:
                return ConfigResult(ok=False, action="get", error="get requires a key.")
            raw = store.get(key)
            return ConfigResult(
                ok=True, action="get", key=key, value=_mask(key, raw) if raw else None
            )

        if act == "set":
            if not key:
                return ConfigResult(ok=False, action="set", error="set requires a key.")
            if value is None:
                return ConfigResult(ok=False, action="set", error="set requires a value.")
            valid = known_config_keys()
            if key not in valid:
                return ConfigResult(
                    ok=False,
                    action="set",
                    key=key,
                    error=(
                        f"Unknown config key {key!r}. Known keys include: "
                        f"{', '.join(sorted(valid))}."
                    ),
                )
            store.set(key, str(value))
            return ConfigResult(
                ok=True,
                action="set",
                key=key,
                value=_mask(key, str(value)),
                note=(
                    "Saved to the config store and applied live. Platform or "
                    "credential changes may need a restart for that adapter to "
                    "reconnect."
                ),
            )

        return ConfigResult(
            ok=False, action=act, error=f"Unknown action {act!r}. Use list, get, or set."
        )


def register_contacts(agent: Any) -> None:
    @agent.tool
    async def openpup_contacts(context: RunContext, query: Optional[str] = None) -> ContactList:
        """List or search known contacts OpenPup can message.

        Contacts are learned automatically from people who message OpenPup. Use
        this to find the right ``platform:channel`` address (or a name you can
        pass straight to ``openpup_send_message``).

        Args:
            query: optional filter on name / channel / platform. Omit to list all.

        Owner-only: the contact book is the owner's social graph.
        """
        from openpup.directory import get_directory

        if not _is_owner():
            return ContactList(error="Only the owner can list contacts.")
        rows = get_directory().search(query)
        contacts = [
            Contact(
                platform=c["platform"],
                channel=c["channel"],
                name=c.get("name", c["channel"]),
                address=f"{c['platform']}:{c['channel']}",
            )
            for c in rows
        ]
        return ContactList(contacts=contacts, count=len(contacts))


def register_session_search(agent: Any) -> None:
    @agent.tool
    async def openpup_session_search(
        context: RunContext,
        query: Optional[str] = None,
        session_id: Optional[str] = None,
        around_message_id: Optional[int] = None,
        window: int = 5,
        limit: int = 3,
    ) -> SessionSearchResult:
        """Recall past conversations: search, read, or scroll session transcripts.

        The calling shape picks the mode -- there is no mode argument:

        - ``session_id`` + ``around_message_id`` -> SCROLL: +/-``window``
          messages around that anchor. To keep scrolling forward, call again
          anchored on the last message id you were shown.
        - ``session_id`` alone -> READ: dump the whole session (head/tail
          truncated for big sessions, with an ``omitted`` count).
        - ``query`` alone -> DISCOVER: full-text search, best hit per session;
          each result carries a ``snippet`` plus +/-``window`` messages of
          surrounding ``context``. "What did we talk about re: X?" -> this.
          FTS5 syntax: terms AND by default, ``OR``, "quoted phrases",
          ``prefix*``.
        - nothing -> BROWSE: the most recently active sessions.

        Args:
            query: full-text search terms (discover mode).
            session_id: which transcript to read or scroll.
            around_message_id: anchor message id (scroll mode).
            window: messages of context each side (clamped to 1-20, default 5).
            limit: max sessions in discover/browse (clamped to 1-10, default 3).

        Owner-only: transcripts may contain the owner's private conversations.
        """
        from openpup.sessions import get_session_store

        if not _is_owner():
            return SessionSearchResult(error="Only the owner can search past conversations.")
        try:
            window = max(1, min(int(window), 20))
        except (TypeError, ValueError):
            window = 5
        try:
            limit = max(1, min(int(limit), 10))
        except (TypeError, ValueError):
            limit = 3
        store = get_session_store()
        try:
            if around_message_id is not None:
                if not session_id:
                    return SessionSearchResult(
                        error="around_message_id requires session_id (scroll mode)."
                    )
                data = store.messages_around(session_id, around_message_id, window=window)
                if not data["messages"]:
                    return SessionSearchResult(
                        mode="scroll",
                        error=f"No message {around_message_id} in session {session_id}.",
                    )
                return SessionSearchResult(
                    mode="scroll",
                    results=data["messages"],
                    message=(
                        f"{len(data['messages'])} messages around #{around_message_id} "
                        f"({data['messages_before']} more before, "
                        f"{data['messages_after']} more after)."
                    ),
                )
            if session_id:
                data = store.read_session(session_id)
                if data["session"] is None:
                    return SessionSearchResult(
                        mode="read", error=f"Session {session_id} not found."
                    )
                note = (
                    f" ({data['omitted']} middle messages omitted; scroll with "
                    "around_message_id to see them)"
                    if data["truncated"]
                    else ""
                )
                return SessionSearchResult(
                    mode="read",
                    results=[{"session": data["session"], "messages": data["messages"]}],
                    message=f"Session {session_id}: {len(data['messages'])} messages{note}.",
                )
            if query:
                hits = store.search(query, limit=limit)
                results = []
                for hit in hits:
                    ctx = store.messages_around(hit["session_id"], hit["message_id"], window=window)
                    results.append({**hit, "context": ctx["messages"]})
                if not results:
                    return SessionSearchResult(
                        mode="discover", message=f"No transcripts match {query!r}."
                    )
                return SessionSearchResult(
                    mode="discover",
                    results=results,
                    message=f"{len(results)} session(s) match {query!r}, best hit each.",
                )
            sessions = store.recent_sessions(limit=limit)
            return SessionSearchResult(
                mode="browse",
                results=sessions,
                message=f"{len(sessions)} most recently active session(s).",
            )
        except Exception as exc:  # noqa: BLE001 — tools must never raise
            return SessionSearchResult(error=f"session_search failed: {exc!r}")


_TOOL_NAMES = (
    "openpup_send_message",
    "openpup_check_email",
    "openpup_unread_email",
    "openpup_search_email",
    "openpup_delete_email",
    "openpup_list_platforms",
    "openpup_config",
    "openpup_contacts",
    "openpup_session_search",
    SKILL_TOOL_NAME,
)


def register_tools_callback() -> List[dict]:
    """``register_tools`` hook — define OpenPup tools."""
    return [
        {"name": "openpup_send_message", "register_func": register_send_message},
        {"name": "openpup_check_email", "register_func": register_check_email},
        {"name": "openpup_unread_email", "register_func": register_unread_email},
        {"name": "openpup_search_email", "register_func": register_search_email},
        {"name": "openpup_delete_email", "register_func": register_delete_email},
        {"name": "openpup_list_platforms", "register_func": register_list_platforms},
        {"name": "openpup_config", "register_func": register_config},
        {"name": "openpup_contacts", "register_func": register_contacts},
        {"name": "openpup_session_search", "register_func": register_session_search},
        {"name": SKILL_TOOL_NAME, "register_func": register_skill_tool},
    ]


def advertise_tools(agent_name: Optional[str] = None) -> List[str]:
    """``register_agent_tools`` hook — advertise OpenPup tools to the agent.

    Also advertises code-puppy's ``universal_constructor`` meta-tool when it's
    enabled, so the OpenPup agent can build its own tools at runtime. The core
    silently skips it if the UC config flag is off, so advertising is safe.
    """
    names = list(_TOOL_NAMES)
    if _uc_enabled():
        names.append("universal_constructor")
    return names


def _uc_enabled() -> bool:
    try:
        from code_puppy.config import get_universal_constructor_enabled

        return bool(get_universal_constructor_enabled())
    except Exception:
        return False
