"""Per-integration access control: owner + allowlists.

OpenPup needs to tell its owner apart from random people who message the bot,
and to keep non-owners from triggering privileged actions (reading the owner's
email, sending messages on their behalf).

Model:
* The **owner** is a single ``platform:channel`` address (``OPENPUP_OWNER_ADDRESS``).
  The owner is always allowed and always tagged ``owner``.
* Each platform has a **mode** and an **allowlist**, persisted to
  ``~/.openpup/access.json``:
    - ``open``       — anyone may interact (default; nothing breaks).
    - ``allowlist``  — only the owner + allow-listed senders may interact.
    - ``owner_only`` — only the owner may interact.

A sender is matched against several candidate identifiers (channel, sender,
sender_id) so it works across platforms (chat ids, phone numbers, emails,
Discord user ids).

The role of the sender currently being served is also exposed via a contextvar
so agent tools can refuse privileged operations for non-owners.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger("openpup.access")

# Roles
OWNER = "owner"
ALLOWED = "allowed"
DENIED = "denied"

# Modes
MODE_OPEN = "open"
MODE_ALLOWLIST = "allowlist"
MODE_OWNER_ONLY = "owner_only"
MODES = (MODE_OPEN, MODE_ALLOWLIST, MODE_OWNER_ONLY)

# Role of the sender whose message is currently being processed.
_current_role: ContextVar[str] = ContextVar("openpup_current_role", default=ALLOWED)


def set_current_role(role: str) -> "Token[str]":
    """Set the active sender role; returns a token for :func:`reset_current_role`."""
    return _current_role.set(role)


def reset_current_role(token: "Token[str]") -> None:
    """Restore the role saved by :func:`set_current_role`.

    Callers that serve one message inside a longer-lived task (heartbeat
    inbound polling) MUST restore, or the message's role leaks into
    whatever that task does next.
    """
    try:
        _current_role.reset(token)
    except Exception:  # token from another context — never worth crashing over
        logger.debug("could not reset role contextvar", exc_info=True)


def get_current_role() -> str:
    return _current_role.get()


def current_is_owner() -> bool:
    return _current_role.get() == OWNER


@dataclass
class Decision:
    allowed: bool
    role: str
    reason: str = ""


class AccessControl:
    """Owner + per-platform allowlist policy, persisted as JSON."""

    def __init__(
        self,
        path: Path,
        owner_address: Optional[str] = None,
        owner_addresses: Optional[List[str]] = None,
        directory=None,
    ) -> None:
        self.path = path
        self.directory = directory  # optional ContactDirectory for per-user roles
        self.owner_address = owner_address  # primary (for describe / outreach)
        addrs: List[str] = []
        if owner_address:
            addrs.append(owner_address)
        for a in owner_addresses or []:
            if a and a not in addrs:
                addrs.append(a)
        self.owner_addresses = addrs
        self.platforms: Dict[str, dict] = {}
        self.load()

    # ---- persistence -----------------------------------------------------
    def load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.platforms = data.get("platforms", {})
            except Exception:
                logger.exception("Failed to load access policy from %s", self.path)
                self.platforms = {}

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps({"platforms": self.platforms}, indent=2))
        except Exception:
            logger.exception("Failed to save access policy")

    # ---- config ----------------------------------------------------------
    def _cfg(self, platform: str) -> dict:
        return self.platforms.setdefault(platform, {"mode": MODE_OPEN, "allowed": []})

    def set_mode(self, platform: str, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self._cfg(platform)["mode"] = mode
        self.save()

    def allow(self, platform: str, identifier: str) -> None:
        cfg = self._cfg(platform)
        if identifier not in cfg["allowed"]:
            cfg["allowed"].append(identifier)
        # Adding someone to an allowlist implies you want the allowlist enforced.
        if cfg.get("mode", MODE_OPEN) == MODE_OPEN:
            cfg["mode"] = MODE_ALLOWLIST
        self.save()

    def deny(self, platform: str, identifier: str) -> bool:
        cfg = self._cfg(platform)
        if identifier in cfg["allowed"]:
            cfg["allowed"].remove(identifier)
            self.save()
            return True
        return False

    # ---- evaluation ------------------------------------------------------
    def _owner_parts(self):
        """Return [(platform, channel), ...] for every owner address."""
        out = []
        for a in self.owner_addresses:
            if ":" in a:
                platform, channel = a.split(":", 1)
                out.append((platform.strip(), channel.strip()))
        return out

    @staticmethod
    def _candidates(envelope) -> Set[str]:
        return {str(v) for v in (envelope.channel, envelope.sender, envelope.sender_id) if v}

    def _directory_role(self, envelope) -> str:
        """Role assigned to this sender in the editable roster, if any."""
        if self.directory is None:
            return ""
        for ident in (envelope.channel, envelope.sender_id):
            if ident:
                role = self.directory.role_of(envelope.platform, str(ident))
                if role:
                    return role
        return ""

    def is_owner(self, envelope) -> bool:
        candidates = self._candidates(envelope)
        for owner_platform, owner_channel in self._owner_parts():
            if envelope.platform == owner_platform and owner_channel in candidates:
                return True
        # A roster entry explicitly marked 'owner' also counts.
        return self._directory_role(envelope) == "owner"

    def check(self, envelope) -> Decision:
        if self.is_owner(envelope):
            return Decision(True, OWNER)
        # Roster role overrides: blocked -> denied, allowed -> allowed.
        role = self._directory_role(envelope)
        if role == "blocked":
            return Decision(False, DENIED, "blocked in the user roster")
        if role == "allowed":
            return Decision(True, ALLOWED)
        cfg = self._cfg(envelope.platform)
        mode = cfg.get("mode", MODE_OPEN)
        if mode == MODE_OWNER_ONLY:
            return Decision(False, DENIED, "owner-only mode")
        if mode == MODE_OPEN:
            return Decision(True, ALLOWED)
        # allowlist
        allowed: List[str] = cfg.get("allowed", [])
        if self._candidates(envelope) & set(allowed):
            return Decision(True, ALLOWED)
        return Decision(False, DENIED, "not on allowlist")

    def describe(self) -> str:
        owners = ", ".join(self.owner_addresses) or "(unset)"
        lines = [f"owner(s): {owners}"]
        if not self.platforms:
            lines.append("no per-platform policies (all platforms are open)")
        for platform, cfg in sorted(self.platforms.items()):
            allowed = ", ".join(cfg.get("allowed", [])) or "(none)"
            lines.append(f"{platform}: mode={cfg.get('mode', MODE_OPEN)} allowed=[{allowed}]")
        return "\n".join(lines)


def default_access_path(state_dir: Path) -> Path:
    return state_dir / "access.json"
