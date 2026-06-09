"""Contact / channel directory, ported in spirit from hermes-agent.

hermes keeps a cached directory of reachable channels/contacts so the agent can
``send_message(action='list')`` and resolve human-friendly names to IDs. OpenPup
learns its directory passively: every inbound message records the sender as a
known contact (platform + channel + display name), persisted to
``~/.openpup/contacts.json``. The agent can then list/search contacts and address
them by name ("telegram:Mike" or just "Mike") instead of raw chat ids.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("openpup.directory")


def _norm(value: str) -> str:
    return (value or "").lstrip("#").strip().lower()


def _is_id_like(tail: str) -> bool:
    """Whether a target tail is a concrete id (vs a friendly name)."""
    t = tail.strip()
    if not t:
        return False
    if "@" in t:  # email address
        return True
    if t.startswith("+") and t[1:].isdigit():  # E.164 phone
        return True
    if t.replace("-", "").isdigit():  # numeric chat/user id
        return True
    return False


class ContactDirectory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.contacts: List[Dict] = []
        self.load()

    # ---- persistence -----------------------------------------------------
    def load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.contacts = data.get("contacts", [])
            except Exception:
                logger.exception("Failed to load contacts from %s", self.path)
                self.contacts = []

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps({"contacts": self.contacts}, indent=2))
        except Exception:
            logger.exception("Failed to save contacts")

    # ---- recording -------------------------------------------------------
    def record(self, platform: str, channel: str, name: Optional[str] = None) -> None:
        """Upsert a known contact (called for every inbound sender)."""
        if not platform or not channel:
            return
        for c in self.contacts:
            if c["platform"] == platform and c["channel"] == channel:
                if name and name != c.get("name"):
                    c["name"] = name
                c["last_seen"] = time.time()
                c["count"] = int(c.get("count", 0)) + 1
                self.save()
                return
        self.contacts.append(
            {
                "platform": platform,
                "channel": channel,
                "name": name or channel,
                "last_seen": time.time(),
                "count": 1,
            }
        )
        self.save()

    # ---- lookup ----------------------------------------------------------
    def resolve(self, query: str) -> Optional[str]:
        """Resolve a query to a ``platform:channel`` address, or None.

        Accepts:
        * ``platform:channel`` — returned as-is (explicit address).
        * ``platform:name``    — name resolved within that platform.
        * ``name``             — name resolved across all platforms (unambiguous).
        """
        if not query:
            return None
        query = query.strip()

        platform_scope: Optional[str] = None
        name_part = query
        if ":" in query:
            head, tail = query.split(":", 1)
            head_n = _norm(head)
            tail = tail.strip()
            known_platform = head_n in _KNOWN_PLATFORMS or any(
                c["platform"] == head_n for c in self.contacts
            )
            if known_platform:
                # Explicit address if the tail is id-like or a known channel.
                if _is_id_like(tail) or any(
                    c["platform"] == head_n and c["channel"] == tail for c in self.contacts
                ):
                    return f"{head_n}:{tail}"
                # Otherwise it's a platform-scoped friendly name.
                platform_scope = head_n
                name_part = tail
            else:
                # Unknown platform prefix -> treat the whole thing as explicit.
                return query

        candidates = self.contacts
        if platform_scope:
            candidates = [c for c in candidates if c["platform"] == platform_scope]

        q = _norm(name_part)
        exact = [c for c in candidates if _norm(c.get("name", "")) == q or _norm(c["channel"]) == q]
        if len(exact) == 1:
            return f"{exact[0]['platform']}:{exact[0]['channel']}"
        if len(exact) > 1:
            return None  # ambiguous name across platforms
        prefix = [c for c in candidates if _norm(c.get("name", "")).startswith(q)]
        if len(prefix) == 1:
            return f"{prefix[0]['platform']}:{prefix[0]['channel']}"
        return None

    def search(self, query: Optional[str] = None) -> List[Dict]:
        if not query:
            return sorted(self.contacts, key=lambda c: c.get("last_seen", 0), reverse=True)
        q = _norm(query)
        return [
            c
            for c in self.contacts
            if q in _norm(c.get("name", "")) or q in _norm(c["channel"]) or q in c["platform"]
        ]

    def is_known(self, platform: str, channel: str) -> bool:
        return any(c["platform"] == platform and c["channel"] == channel for c in self.contacts)


_KNOWN_PLATFORMS = frozenset({"telegram", "discord", "whatsapp", "sms", "email"})


def default_contacts_path(state_dir: Path) -> Path:
    return state_dir / "contacts.json"


_directory: Optional[ContactDirectory] = None


def get_directory() -> ContactDirectory:
    """Process-wide directory singleton (so agent tools and runtime share it)."""
    global _directory
    if _directory is None:
        from openpup.config import get_settings

        _directory = ContactDirectory(default_contacts_path(get_settings().state_dir))
    return _directory
