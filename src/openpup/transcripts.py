"""Fire-and-forget transcript recording on top of the SessionStore.

Session id convention (date-bucketed so sessions never grow unboundedly):

* conversations: ``{platform}:{channel}:{YYYYMMDD}`` — one session per
  conversation peer per day, with ``source`` = the ``platform:channel`` address;
* heartbeat-initiated agent turns: ``heartbeat:{behavior}:{YYYYMMDD}`` with
  ``source`` = ``"heartbeat"``.

Recording must never break message flow: every failure is swallowed and logged
at debug level (the SessionStore itself degrades gracefully too — belt *and*
suspenders).
"""

from __future__ import annotations

import logging
import time

from openpup import sessions

logger = logging.getLogger("openpup.transcripts")

HEARTBEAT_SOURCE = "heartbeat"


def day_bucket() -> str:
    """Local date as ``YYYYMMDD`` — the bucket that keeps sessions bounded."""
    return time.strftime("%Y%m%d")


def conversation_session_id(address: str) -> str:
    """Session id for a conversation peer: ``platform:channel:YYYYMMDD``."""
    return f"{address}:{day_bucket()}"


def heartbeat_session_id(behavior: str) -> str:
    """Session id for a heartbeat behavior: ``heartbeat:{behavior}:YYYYMMDD``."""
    return f"{HEARTBEAT_SOURCE}:{behavior}:{day_bucket()}"


def record_turn(session_id: str, source: str, role: str, content: str) -> None:
    """Append one transcript row. Never raises — flow beats bookkeeping."""
    try:
        sessions.get_session_store().append(session_id, source, role, content)
    except Exception:
        logger.debug("transcript recording failed for %s", session_id, exc_info=True)
