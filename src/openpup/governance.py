"""Outbound comms governance, ported in spirit from hermes-agent.

Where ``access.py`` governs who may talk TO OpenPup (inbound), this governs what
OpenPup may send OUT:

* **Rate limiting** — a per-platform sliding window caps how many messages the
  agent can fire, defusing runaway loops / spam.
* **Send policy** — ``open`` / ``contacts`` / ``owner_only`` restricts who the
  agent may message (defense in depth even though sends are owner-gated).
* **Secret redaction** — scrub tokens/keys from tool error text before it ever
  reaches the model or a chat (implemented in :mod:`openpup.security.redact`;
  re-exported here so existing call sites keep working).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Sequence

# Policies
POLICY_OPEN = "open"
POLICY_CONTACTS = "contacts"
POLICY_OWNER_ONLY = "owner_only"
POLICIES = (POLICY_OPEN, POLICY_CONTACTS, POLICY_OWNER_ONLY)

# --- secret redaction ------------------------------------------------------
# The deep pattern library lives in openpup.security.redact; re-export keeps
# every existing ``from openpup.governance import redact`` call site working.
from openpup.security.redact import redact  # noqa: E402,F401


# --- rate limiting ---------------------------------------------------------
@dataclass
class RateLimiter:
    """Per-platform sliding-window limiter."""

    per_minute: int = 10
    window: float = 60.0
    _hits: Dict[str, Deque[float]] = field(default_factory=dict)

    def allow(self, platform: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        dq = self._hits.setdefault(platform, deque())
        while dq and now - dq[0] > self.window:
            dq.popleft()
        if len(dq) >= self.per_minute:
            return False
        dq.append(now)
        return True

    def remaining(self, platform: str, now: Optional[float] = None) -> int:
        now = now if now is not None else time.time()
        dq = self._hits.get(platform, deque())
        while dq and now - dq[0] > self.window:
            dq.popleft()
        return max(0, self.per_minute - len(dq))


# --- send policy -----------------------------------------------------------
@dataclass
class SendDecision:
    allowed: bool
    reason: str = ""


class SendPolicy:
    def __init__(
        self,
        policy: str = POLICY_OPEN,
        per_minute: int = 10,
        owner_address: Optional[str] = None,
        owner_addresses: Optional[Sequence[str]] = None,
    ) -> None:
        self.policy = policy if policy in POLICIES else POLICY_OPEN
        self.owner_address = owner_address
        # Build the full set of owner addresses for membership checks.
        _addrs: List[str] = list(owner_addresses or [])
        if owner_address and owner_address not in _addrs:
            _addrs.append(owner_address)
        self._owner_set: frozenset[str] = frozenset(_addrs)
        self.limiter = RateLimiter(per_minute=per_minute)

    def _is_owner(self, address: str) -> bool:
        return address in self._owner_set

    def check(self, address: str, directory=None, now: Optional[float] = None) -> SendDecision:
        if ":" not in address:
            return SendDecision(False, "address must be 'platform:channel'")
        platform, channel = address.split(":", 1)
        platform = platform.strip()
        channel = channel.strip()

        # recipient policy
        if self.policy == POLICY_OWNER_ONLY:
            if not self._is_owner(address):
                return SendDecision(False, "send policy is owner_only")
        elif self.policy == POLICY_CONTACTS:
            is_owner = self._is_owner(address)
            is_known = bool(directory and directory.is_known(platform, channel))
            if not (is_owner or is_known):
                return SendDecision(
                    False,
                    "send policy is 'contacts' — recipient is not the owner or a known "
                    "contact. Ask the owner to add them, or use a known contact.",
                )

        # rate limit
        if not self.limiter.allow(platform, now=now):
            return SendDecision(
                False, f"rate limit hit for {platform} (max {self.limiter.per_minute}/min)"
            )
        return SendDecision(True)


_send_policy: Optional[SendPolicy] = None


def get_send_policy() -> SendPolicy:
    """Process-wide SendPolicy built from settings (shares rate-limit state)."""
    global _send_policy
    if _send_policy is None:
        from openpup.config import get_settings

        s = get_settings()
        _send_policy = SendPolicy(
            policy=s.send_policy,
            per_minute=s.send_rate_per_min,
            owner_address=s.owner_address,
            owner_addresses=s.owner_addresses,
        )
    return _send_policy
