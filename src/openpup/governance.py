"""Outbound comms governance, ported in spirit from hermes-agent.

Where ``access.py`` governs who may talk TO OpenPup (inbound), this governs what
OpenPup may send OUT:

* **Rate limiting** — a per-platform sliding window caps how many messages the
  agent can fire, defusing runaway loops / spam.
* **Send policy** — ``open`` / ``contacts`` / ``owner_only`` restricts who the
  agent may message (defense in depth even though sends are owner-gated).
* **Secret redaction** — scrub tokens/keys from tool error text before it ever
  reaches the model or a chat.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional

# Policies
POLICY_OPEN = "open"
POLICY_CONTACTS = "contacts"
POLICY_OWNER_ONLY = "owner_only"
POLICIES = (POLICY_OPEN, POLICY_CONTACTS, POLICY_OWNER_ONLY)

# --- secret redaction ------------------------------------------------------
_URL_SECRET_RE = re.compile(
    r"([?&](?:access_token|api[_-]?key|auth[_-]?token|token|signature|sig)=)([^&#\s]+)",
    re.IGNORECASE,
)
_ASSIGN_SECRET_RE = re.compile(
    r"\b(access[_-]?token|api[_-]?key|auth[_-]?token|password|secret|signature|sig|token)"
    r"\s*[=:]\s*([^\s,;]+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]{8,})", re.IGNORECASE)
# Long opaque tokens (e.g. bot tokens like 123456:AAE...).
_BOT_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{20,}\b")


def redact(text: str) -> str:
    """Scrub likely secrets from a string before surfacing it."""
    if not text:
        return text
    text = _URL_SECRET_RE.sub(lambda m: f"{m.group(1)}***", text)
    text = _ASSIGN_SECRET_RE.sub(lambda m: f"{m.group(1)}=***", text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)}***", text)
    text = _BOT_TOKEN_RE.sub("***", text)
    return text


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
    ) -> None:
        self.policy = policy if policy in POLICIES else POLICY_OPEN
        self.owner_address = owner_address
        self.limiter = RateLimiter(per_minute=per_minute)

    def check(self, address: str, directory=None, now: Optional[float] = None) -> SendDecision:
        if ":" not in address:
            return SendDecision(False, "address must be 'platform:channel'")
        platform, channel = address.split(":", 1)
        platform = platform.strip()
        channel = channel.strip()

        # recipient policy
        if self.policy == POLICY_OWNER_ONLY:
            if address != self.owner_address:
                return SendDecision(False, "send policy is owner_only")
        elif self.policy == POLICY_CONTACTS:
            is_owner = address == self.owner_address
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
        )
    return _send_policy
