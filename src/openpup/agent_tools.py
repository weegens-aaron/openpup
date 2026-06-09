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


# --------------------------------------------------------------------------
# Tool registration functions (called with the pydantic agent at build time)
# --------------------------------------------------------------------------
def register_send_message(agent: Any) -> None:
    @agent.tool
    async def openpup_send_message(context: RunContext, address: str, text: str) -> SendResult:
        """Send a message to someone on a connected platform.

        Args:
            address: ``platform:channel`` target, e.g. ``telegram:12345``,
                     ``discord:998877``, ``sms:+15551234567``,
                     ``email:friend@example.com``.
            text:    The message body to send.

        Use ``openpup_list_platforms`` first if you're unsure which platforms
        are connected or what the owner's address is.
        """
        reg = get_registry()
        if ":" not in address:
            return SendResult(ok=False, address=address, error="address must be 'platform:channel'")
        platform = address.split(":", 1)[0]
        if reg.get(platform) is None:
            return SendResult(
                ok=False, address=address, error=f"platform '{platform}' is not connected"
            )
        ok = await reg.send(Envelope.to(address, text))
        return SendResult(ok=ok, address=address, error=None if ok else "delivery failed")


def register_check_email(agent: Any) -> None:
    @agent.tool
    async def openpup_check_email(context: RunContext, limit: int = 5) -> EmailList:
        """Read the most recent emails from OpenPup's connected mailbox (read-only).

        Does NOT mark anything as read. Returns sender, subject, date, and a
        short preview of each message, newest first.

        Args:
            limit: How many recent emails to fetch (1-20, default 5).
        """
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


_TOOL_NAMES = (
    "openpup_send_message",
    "openpup_check_email",
    "openpup_list_platforms",
)


def register_tools_callback() -> List[dict]:
    """``register_tools`` hook — define OpenPup tools."""
    return [
        {"name": "openpup_send_message", "register_func": register_send_message},
        {"name": "openpup_check_email", "register_func": register_check_email},
        {"name": "openpup_list_platforms", "register_func": register_list_platforms},
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


def openpup_identity_prompt() -> Optional[str]:
    """``load_prompt`` hook — tell the agent it's OpenPup and what it can do."""
    try:
        from openpup.config import get_settings

        reg = get_registry()
        settings = get_settings()
        platforms = reg.platforms()
        platform_str = ", ".join(platforms) if platforms else "none yet"
        owner = settings.owner_address or "unknown"
        return (
            f"\n\n# You are {settings.name}, an always-on AI companion (OpenPup).\n"
            "You run continuously and can reach your human through real messaging "
            "platforms. You are NOT limited to coding — you are a helpful companion.\n"
            f"Connected platforms: {platform_str}. Owner address: {owner}.\n"
            "You have these OpenPup tools:\n"
            "- openpup_list_platforms(): see what's connected and the owner's address.\n"
            "- openpup_check_email(limit): read recent emails (only if email is connected).\n"
            "- openpup_send_message(address, text): message someone at 'platform:channel'.\n"
            + (
                "- universal_constructor(action, ...): BUILD YOUR OWN TOOLS in Python at "
                "runtime (action=create/call/list/update/info). If you lack a capability, "
                "construct it instead of refusing.\n"
                if _uc_enabled()
                else ""
            )
            + "When the user asks you to check email or message someone, USE these tools "
            "instead of saying you can't. If a platform isn't connected, say so plainly "
            "and suggest running 'openpup setup'."
        )
    except Exception:
        return None
