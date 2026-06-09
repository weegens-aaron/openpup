"""Native macOS iMessage adapter -- no external service, no Twilio.

* Outbound: AppleScript drives the Messages app (``osascript``).
* Inbound: polls the Messages SQLite database (``~/Library/Messages/chat.db``,
  read-only) for new incoming messages.

Two macOS permissions are required (granted once, in System Settings):
* **Full Disk Access** for the process running OpenPup -> to read chat.db.
* **Automation** control of Messages -> to send (first send pops a dialog).

Modern macOS often stores message text in an ``attributedBody`` blob rather
than the ``text`` column, so we decode that as a fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import sqlite3
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from openpup.config import Settings
from openpup.messaging.envelope import Envelope
from openpup.messaging.registry import PlatformRegistry
from openpup.platforms.base import PlatformAdapter

logger = logging.getLogger("openpup.imessage")


def is_macos() -> bool:
    return platform.system() == "Darwin"


# --------------------------------------------------------------------------
# attributedBody decoding (NSAttributedString streamtyped blob)
# --------------------------------------------------------------------------
def decode_attributed_body(blob: Optional[bytes]) -> str:
    """Best-effort extraction of message text from an attributedBody blob."""
    if not blob:
        return ""
    try:
        if b"NSString" not in blob:
            return ""
        data = blob.split(b"NSString", 1)[1]
        # Skip the class header bytes that precede the length-prefixed string.
        data = data[5:]
        if not data:
            return ""
        first = data[0]
        if first == 0x81:  # 2-byte little-endian length follows
            length = int.from_bytes(data[1:3], "little")
            text = data[3 : 3 + length]
        else:
            length = first
            text = data[1 : 1 + length]
        return text.decode("utf-8", errors="replace")
    except Exception:
        return ""


def applescript_send(handle: str, message: str) -> List[str]:
    """Build the osascript command to send an iMessage to ``handle``."""
    script = (
        "on run argv\n"
        "  set targetBuddy to item 1 of argv\n"
        "  set targetMessage to item 2 of argv\n"
        '  tell application "Messages"\n'
        "    set targetService to 1st service whose service type = iMessage\n"
        "    send targetMessage to buddy targetBuddy of targetService\n"
        "  end tell\n"
        "end run"
    )
    return ["osascript", "-e", script, handle, message]


class IMessageAdapter(PlatformAdapter):
    name = "imessage"

    def __init__(self, settings: Settings, registry: PlatformRegistry) -> None:
        super().__init__(settings, registry)
        if not is_macos():
            raise RuntimeError("iMessage is only available on macOS")
        self._db_path = Path(settings.imessage_db_path).expanduser()
        self._state_path = settings.state_dir / "imessage_state.json"
        self._last_rowid = self._load_last_rowid()
        self._poll_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    # ---- state -----------------------------------------------------------
    def _load_last_rowid(self) -> int:
        try:
            return int(json.loads(self._state_path.read_text()).get("last_rowid", 0))
        except Exception:
            return 0

    def _save_last_rowid(self) -> None:
        try:
            self._state_path.write_text(json.dumps({"last_rowid": self._last_rowid}))
        except Exception:
            logger.debug("could not save imessage state", exc_info=True)

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        # On first run, baseline to the current max ROWID so we don't replay
        # the entire message history.
        if self._last_rowid == 0:
            self._last_rowid = await asyncio.to_thread(self._current_max_rowid)
            self._save_last_rowid()
        self._stop.clear()
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "iMessage adapter started (poll every %ss)", self.settings.imessage_poll_seconds
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._poll_task:
            self._poll_task.cancel()
        logger.info("iMessage adapter stopped")

    async def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception:
                logger.exception("iMessage poll failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.imessage_poll_seconds
                )
            except asyncio.TimeoutError:
                pass

    # ---- db --------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        # Read-only URI connection; respects the WAL sidecar files.
        return sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True, timeout=5)

    def _current_max_rowid(self) -> int:
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT MAX(ROWID) FROM message").fetchone()
                return int(row[0]) if row and row[0] else 0
        except Exception:
            logger.warning("Could not read chat.db (Full Disk Access granted?)")
            return 0

    def _fetch_new(self) -> List[Tuple[int, str, str]]:
        """Return [(rowid, handle, text), ...] for new incoming messages."""
        sql = (
            "SELECT m.ROWID, m.text, m.attributedBody, h.id "
            "FROM message m LEFT JOIN handle h ON m.handle_id = h.ROWID "
            "WHERE m.ROWID > ? AND m.is_from_me = 0 "
            "ORDER BY m.ROWID ASC LIMIT 50"
        )
        out: List[Tuple[int, str, str]] = []
        with self._connect() as conn:
            for rowid, text, attributed, handle in conn.execute(sql, (self._last_rowid,)):
                body = text or decode_attributed_body(attributed)
                out.append((int(rowid), handle or "", body or ""))
        return out

    async def poll_once(self) -> List[Envelope]:
        """Fetch new incoming iMessages, dispatch as Envelopes. Tickable."""
        rows = await asyncio.to_thread(self._fetch_new)
        envelopes: List[Envelope] = []
        for rowid, handle, body in rows:
            self._last_rowid = max(self._last_rowid, rowid)
            if not handle or not body.strip():
                continue
            envelopes.append(Envelope(platform=self.name, channel=handle, sender=handle, text=body))
        if rows:
            self._save_last_rowid()
        for env in envelopes:
            await self.registry.dispatch_inbound(env)
        return envelopes

    # ---- send ------------------------------------------------------------
    async def send(self, envelope: Envelope) -> None:
        def _do_send() -> None:
            result = subprocess.run(
                applescript_send(envelope.channel, envelope.text),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "osascript failed")

        await asyncio.to_thread(_do_send)
