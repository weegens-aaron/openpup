"""Email adapter: a ONE-WAY, read-only inbox sensor (+ optional outbound).

Unlike the chat platforms (Telegram/SMS/iMessage/...), email is **not** a
two-way conversational channel. OpenPup does NOT auto-reply to incoming mail.
Instead the inbox is a sensor the pup reads on demand -- typically from a
recurring scheduled job ("check my email every 30m and tell me about new ones
on topic X"). Notifications go out on the owner's normal channel, not as email
replies.

What this adapter exposes:
* ``fetch_recent(limit, only_new)`` -- read recent mail WITHOUT marking it
  seen. ``only_new=True`` returns only messages newer than the last watched
  check (watermark-tracked), so a recurring check never re-reports the same
  email. Used by the ``openpup_check_email`` tool.
* ``send(envelope)`` -- still available so the owner can explicitly ask the pup
  to send an email; nothing fires automatically.

There is intentionally no inbound poll loop and no ``poll_once``: the heartbeat
won't crawl the inbox, and no email ever triggers an unsolicited agent run.
"""

from __future__ import annotations

import asyncio
import json
import logging
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

from openpup.config import Settings
from openpup.messaging.envelope import Envelope
from openpup.messaging.registry import PlatformRegistry
from openpup.platforms.base import PlatformAdapter

logger = logging.getLogger("openpup.email")


class EmailAdapter(PlatformAdapter):
    name = "email"

    def __init__(self, settings: Settings, registry: PlatformRegistry) -> None:
        super().__init__(settings, registry)
        required = [
            settings.email_imap_host,
            settings.email_smtp_host,
            settings.email_username,
            settings.email_password,
        ]
        if not all(required):
            raise ValueError("EMAIL_IMAP_HOST/SMTP_HOST/USERNAME/PASSWORD are required")
        # Import here so the dependency is only needed when email is enabled.
        import aiosmtplib  # noqa: F401
        from imap_tools import MailBox  # noqa: F401

    # ---- lifecycle -------------------------------------------------------
    # Email is read-on-demand: no background polling, no inbound dispatch.
    async def start(self) -> None:
        logger.info("Email adapter started (read-only inbox sensor; no auto-reply)")

    async def stop(self) -> None:
        logger.info("Email adapter stopped")

    # ---- read-only inbox -------------------------------------------------
    async def fetch_recent(self, limit: int = 5, only_new: bool = False) -> List[dict]:
        """Read recent emails WITHOUT marking them seen (read-only).

        Returns a list of dicts: ``from_addr``, ``subject``, ``date``,
        ``preview``, ``uid``.

        Args:
            limit: max messages to fetch (newest first).
            only_new: if True, return only messages newer than the last
                watched check and advance the watermark. The first-ever
                watched check just establishes the watermark and returns []
                ("start watching from now"), so you never get dumped the
                whole backlog.
        """
        return await asyncio.to_thread(self._fetch_recent_sync, limit, only_new)

    def _fetch_recent_sync(self, limit: int, only_new: bool) -> List[dict]:
        from imap_tools import MailBox

        out: List[dict] = []
        with MailBox(self.settings.email_imap_host, self.settings.email_imap_port).login(
            self.settings.email_username, self.settings.email_password
        ) as mailbox:
            for msg in mailbox.fetch(reverse=True, limit=limit, mark_seen=False):
                body = msg.text or msg.html or ""
                out.append(
                    {
                        "from_addr": msg.from_ or "",
                        "subject": msg.subject or "",
                        "date": str(msg.date) if msg.date else "",
                        "preview": body[:500],
                        "uid": msg.uid or "",
                    }
                )
        if only_new:
            out = self._filter_new(out)
        return out

    # ---- "new since last check" watermark --------------------------------
    def _watermark_path(self) -> Path:
        return self.settings.state_dir / "email_watermark.json"

    def _load_watermark(self) -> int:
        try:
            return int(json.loads(self._watermark_path().read_text()).get("last_uid", 0))
        except Exception:
            return 0

    def _save_watermark(self, last_uid: int) -> None:
        try:
            self._watermark_path().write_text(json.dumps({"last_uid": last_uid}))
        except Exception:
            logger.exception("Failed to persist email watermark")

    def _filter_new(self, items: List[dict]) -> List[dict]:
        """Keep only items with a UID above the stored watermark, then advance it.

        IMAP UIDs are monotonically increasing within a mailbox, so the max UID
        seen is a reliable high-water mark. On the very first watched check
        (no watermark yet) we record the current max and return nothing.
        """
        uids = [self._as_int_uid(i.get("uid")) for i in items]
        max_uid = max([u for u in uids if u is not None], default=0)

        first_run = not self._watermark_path().exists()
        last_uid = self._load_watermark()
        if max_uid > last_uid:
            self._save_watermark(max_uid)
        if first_run:
            return []
        return [
            item
            for item, uid in zip(items, uids)
            if uid is not None and uid > last_uid
        ]

    @staticmethod
    def _as_int_uid(uid: object) -> Optional[int]:
        try:
            return int(str(uid))
        except (TypeError, ValueError):
            return None

    # ---- outbound (explicit only) ----------------------------------------
    async def send(self, envelope: Envelope) -> None:
        import aiosmtplib

        message = EmailMessage()
        message["From"] = self.settings.email_username
        message["To"] = envelope.channel
        subject = envelope.meta.get("subject", "Message from OpenPup")
        if not str(subject).lower().startswith("re:"):
            subject = f"Re: {subject}"
        message["Subject"] = subject
        message.set_content(envelope.text)

        await aiosmtplib.send(
            message,
            hostname=self.settings.email_smtp_host,
            port=self.settings.email_smtp_port,
            username=self.settings.email_username,
            password=self.settings.email_password,
            start_tls=True,
        )
