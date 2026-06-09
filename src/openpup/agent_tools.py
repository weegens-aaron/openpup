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

from typing import Any, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from openpup.messaging.envelope import Envelope
from openpup.messaging.registry import get_registry


def _is_owner() -> bool:
    """Whether the message currently being served is from the owner."""
    try:
        from openpup.access import current_is_owner

        return current_is_owner()
    except Exception:
        return True  # fail open in non-OpenPup contexts (e.g. unit tests)


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


class EmailList(BaseModel):
    count: int
    emails: List[EmailItem] = Field(default_factory=list)
    error: Optional[str] = None


class PlatformList(BaseModel):
    platforms: List[str]
    owner: Optional[str] = None


class Contact(BaseModel):
    platform: str
    channel: str
    name: str
    address: str


class ContactList(BaseModel):
    contacts: List[Contact] = Field(default_factory=list)
    count: int = 0


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
    async def openpup_check_email(context: RunContext, limit: int = 5) -> EmailList:
        """Read the most recent emails from OpenPup's connected mailbox (read-only).

        Does NOT mark anything as read. Returns sender, subject, date, and a
        short preview of each message, newest first.

        Args:
            limit: How many recent emails to fetch (1-20, default 5).
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
            items = await adapter.fetch_recent(limit=limit)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — tools must never raise
            return EmailList(count=0, error=f"check_email failed: {exc!r}")
        return EmailList(count=len(items), emails=[EmailItem(**i) for i in items])


def register_list_platforms(agent: Any) -> None:
    @agent.tool
    async def openpup_list_platforms(context: RunContext) -> PlatformList:
        """List the messaging/email platforms OpenPup currently has connected,
        plus the owner's address (where proactive messages go)."""
        from openpup.config import get_settings

        reg = get_registry()
        return PlatformList(platforms=reg.platforms(), owner=get_settings().owner_address)


def register_contacts(agent: Any) -> None:
    @agent.tool
    async def openpup_contacts(context: RunContext, query: Optional[str] = None) -> ContactList:
        """List or search known contacts OpenPup can message.

        Contacts are learned automatically from people who message OpenPup. Use
        this to find the right ``platform:channel`` address (or a name you can
        pass straight to ``openpup_send_message``).

        Args:
            query: optional filter on name / channel / platform. Omit to list all.
        """
        from openpup.directory import get_directory

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


_TOOL_NAMES = (
    "openpup_send_message",
    "openpup_check_email",
    "openpup_list_platforms",
    "openpup_contacts",
)


def register_tools_callback() -> List[dict]:
    """``register_tools`` hook — define OpenPup tools."""
    return [
        {"name": "openpup_send_message", "register_func": register_send_message},
        {"name": "openpup_check_email", "register_func": register_check_email},
        {"name": "openpup_list_platforms", "register_func": register_list_platforms},
        {"name": "openpup_contacts", "register_func": register_contacts},
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
