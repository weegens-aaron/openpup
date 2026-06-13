"""Owner approval gate: ask the human before doing something irreversible.

Inspired by hermes-agent's approval concept, rebuilt OpenPup-sized: one
pending-future table, one message to the owner, one reply pattern. No
policy engine, no persistence — an approval that outlives the process was
never going to be honored by it anyway.

Flow:
1. A consumer calls :func:`request_approval` with a one-line summary.
2. The owner gets "[approval] <summary> — reply 'yes <id>' or 'no <id>'"
   on their primary address (same delivery path as proactive outreach).
3. The runtime's inbound path feeds OWNER messages to :func:`try_resolve`
   BEFORE agent routing; a matching reply resolves the pending future and
   is consumed (short confirmation, no agent invocation).

Default-deny everywhere: no owner configured, delivery failure, timeout,
or any unexpected error all return False. Non-owner replies never resolve
an approval — the runtime only feeds owner text to ``try_resolve``, and
that trust boundary lives there on purpose (the runtime already knows the
sender's role; this module never re-derives it).
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from typing import Dict, Optional

logger = logging.getLogger("openpup.approval")

# Pending approvals: id -> future resolved with the owner's yes/no.
_PENDING: Dict[str, "asyncio.Future[bool]"] = {}

# Owner reply shapes: "yes ab12cd" / "no ab12cd" (and y/n/approve/deny).
_REPLY = re.compile(r"^\s*(yes|y|approve|no|n|deny)\s+([0-9a-f]{6})\s*$", re.IGNORECASE)
_AFFIRMATIVE = frozenset({"yes", "y", "approve"})


async def request_approval(summary: str, timeout_s: Optional[int] = None) -> bool:
    """Ask the owner to approve ``summary``; True only on an explicit yes.

    Args:
        summary: One line describing the action awaiting approval.
        timeout_s: Seconds to wait for a reply. Defaults to
            ``OPENPUP_APPROVAL_TIMEOUT_S`` (300).

    Default-deny: returns False when no owner is configured, delivery
    fails, the owner says no, the wait times out, or anything raises.
    """
    try:
        return await _request(summary, timeout_s)
    except Exception:  # noqa: BLE001 — a broken gate must deny, not crash the caller
        logger.exception("Approval request failed; denying by default")
        return False


async def _request(summary: str, timeout_s: Optional[int]) -> bool:
    from openpup.config import get_settings
    from openpup.messaging.envelope import Envelope
    from openpup.messaging.registry import get_registry

    settings = get_settings()
    if timeout_s is None:
        timeout_s = settings.approval_timeout_s
    if not settings.owner_address:
        logger.warning("Approval requested but no owner address configured; denying")
        return False

    approval_id = secrets.token_hex(3)
    while approval_id in _PENDING:  # 24-bit ids: collisions are unlikely, not impossible
        approval_id = secrets.token_hex(3)
    future: "asyncio.Future[bool]" = asyncio.get_running_loop().create_future()
    _PENDING[approval_id] = future
    try:
        text = (
            f"[approval] {summary} — reply 'yes {approval_id}' or 'no {approval_id}' "
            f"(expires in {timeout_s}s)"
        )
        delivered = await get_registry().send(Envelope.to(settings.owner_address, text))
        if not delivered:
            logger.warning("Approval message to owner failed to deliver; denying")
            return False
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.info("Approval %s timed out after %ss; denying", approval_id, timeout_s)
            return False
    finally:
        _PENDING.pop(approval_id, None)


def try_resolve(text: str) -> Optional[str]:
    """Resolve a pending approval from an OWNER reply; return the confirmation.

    Returns a short confirmation string when ``text`` is an approval reply
    that matched a pending id (the runtime sends it back and skips agent
    routing), or None when the text is not an approval reply — including
    replies citing unknown/expired ids, which fall through to normal
    routing so the agent can explain what happened.

    Trust boundary: callers must pass OWNER text only. The runtime enforces
    that; this function never sees non-owner messages.
    """
    match = _REPLY.match(text or "")
    if not match:
        return None
    verdict_word, approval_id = match.group(1).lower(), match.group(2).lower()
    future = _PENDING.get(approval_id)
    if future is None or future.done():
        return None
    approved = verdict_word in _AFFIRMATIVE
    future.set_result(approved)
    logger.info("Approval %s resolved: %s", approval_id, "approved" if approved else "denied")
    return "Approved \u2705 — proceeding." if approved else "Denied — standing down."


def pending_count() -> int:
    """How many approvals are currently awaiting the owner (for tests/ops)."""
    return len(_PENDING)


__all__ = ["request_approval", "try_resolve", "pending_count"]
