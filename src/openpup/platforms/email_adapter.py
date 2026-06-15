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
* ``fetch_unread(limit)`` -- the most recent UNREAD messages (the IMAP
  unseen flag), read-only. Used by the ``openpup_unread_email`` tool.
* ``search(query, from_addr, since_days, limit)`` -- read-only mailbox search
  (IMAP-side, never marks seen). Used by the ``openpup_search_email`` tool.
* ``delete(uids, permanent)`` -- owner-initiated cleanup. By default this MOVES
  the given messages to the configured trash folder (reversible);
  ``permanent=True`` expunges them. Only ever acts on explicit UIDs the owner
  asked about -- there is no "delete everything". Used by the
  ``openpup_delete_email`` tool.
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

        with MailBox(self.settings.email_imap_host, self.settings.email_imap_port).login(
            self.settings.email_username, self.settings.email_password
        ) as mailbox:
            out = [
                self._msg_to_item(msg)
                for msg in mailbox.fetch(reverse=True, limit=limit, mark_seen=False)
            ]
        if only_new:
            out = self._filter_new(out)
        return out

    @staticmethod
    def _msg_to_item(msg: object) -> dict:
        """Normalize an imap_tools message into our standard email dict."""
        body = getattr(msg, "text", "") or getattr(msg, "html", "") or ""
        return {
            "from_addr": getattr(msg, "from_", "") or "",
            "subject": getattr(msg, "subject", "") or "",
            "date": str(msg.date) if getattr(msg, "date", None) else "",
            "preview": body[:500],
            "uid": getattr(msg, "uid", "") or "",
        }

    # ---- unread (read-only) ---------------------------------------------
    async def fetch_unread(self, limit: int = 10) -> List[dict]:
        """Return the most recent UNREAD emails WITHOUT marking them seen.

        Uses the IMAP ``\\Seen`` flag (``seen=False``), newest first. Listing
        them never marks them read, so they stay unread in the owner's inbox.

        Returns the same dict shape as ``fetch_recent``.
        """
        return await asyncio.to_thread(self._fetch_unread_sync, limit)

    def _fetch_unread_sync(self, limit: int) -> List[dict]:
        from imap_tools import AND, MailBox

        with MailBox(self.settings.email_imap_host, self.settings.email_imap_port).login(
            self.settings.email_username, self.settings.email_password
        ) as mailbox:
            return [
                self._msg_to_item(msg)
                for msg in mailbox.fetch(
                    AND(seen=False), reverse=True, limit=limit, mark_seen=False
                )
            ]

    # ---- search (read-only) ---------------------------------------------
    async def search(
        self,
        query: Optional[str] = None,
        from_addr: Optional[str] = None,
        since_days: Optional[int] = None,
        limit: int = 10,
        unread: bool = False,
    ) -> List[dict]:
        """Search the mailbox WITHOUT marking anything seen (read-only).

        All provided filters are AND-ed together. With no filters at all this
        just returns the most recent messages (same as ``fetch_recent``).

        Args:
            query: free text matched against the whole message (IMAP TEXT:
                subject + headers + body).
            from_addr: restrict to a sender substring (e.g. ``"amazon.com"``).
            since_days: only messages from the last N days.
            limit: max messages to return (newest first).
            unread: when True, restrict to UNREAD messages (IMAP ``seen=False``).

        Returns the same dict shape as ``fetch_recent``.
        """
        return await asyncio.to_thread(
            self._search_sync, query, from_addr, since_days, limit, unread
        )

    def _search_sync(
        self,
        query: Optional[str],
        from_addr: Optional[str],
        since_days: Optional[int],
        limit: int,
        unread: bool = False,
    ) -> List[dict]:
        import datetime as _dt

        from imap_tools import AND, MailBox

        criteria: dict = {}
        if query and query.strip():
            criteria["text"] = query.strip()
        if from_addr and from_addr.strip():
            criteria["from_"] = from_addr.strip()
        if since_days and int(since_days) > 0:
            criteria["date_gte"] = _dt.date.today() - _dt.timedelta(days=int(since_days))
        if unread:
            criteria["seen"] = False
        # AND() with no kwargs == "ALL" -> falls back to most-recent messages.
        search_criteria = AND(**criteria) if criteria else AND(all=True)

        with MailBox(self.settings.email_imap_host, self.settings.email_imap_port).login(
            self.settings.email_username, self.settings.email_password
        ) as mailbox:
            return [
                self._msg_to_item(msg)
                for msg in mailbox.fetch(
                    search_criteria, reverse=True, limit=limit, mark_seen=False
                )
            ]

    # ---- delete (owner-initiated; reversible by default) -----------------
    async def delete(self, uids: List[str], permanent: bool = False) -> dict:
        """Delete specific emails by UID.

        By default messages are MOVED to ``settings.email_trash_folder``
        (reversible). When ``permanent`` is True -- or no trash folder is
        configured -- they are flagged ``\\Deleted`` and expunged for good.

        Only UIDs that actually exist in the mailbox are touched, so the
        returned ``deleted`` count and ``subjects`` reflect what really
        happened (no optimistic over-counting of stale UIDs).

        Returns a dict: ``deleted`` (int), ``uids`` (acted-on, str list),
        ``subjects`` (str list), ``mode`` (``"trash"`` | ``"permanent"`` |
        ``"none"``), ``missing`` (requested UIDs that weren't found).
        """
        return await asyncio.to_thread(self._delete_sync, list(uids or []), permanent)

    def _delete_sync(self, uids: List[str], permanent: bool) -> dict:
        from imap_tools import AND, MailBox

        requested = [str(u).strip() for u in uids if str(u).strip()]
        if not requested:
            return {"deleted": 0, "uids": [], "subjects": [], "mode": "none", "missing": []}

        trash = (self.settings.email_trash_folder or "").strip()
        use_trash = bool(trash) and not permanent

        with MailBox(self.settings.email_imap_host, self.settings.email_imap_port).login(
            self.settings.email_username, self.settings.email_password
        ) as mailbox:
            # Confirm which requested UIDs really exist before touching anything.
            found = list(mailbox.fetch(AND(uid=requested), mark_seen=False, headers_only=True))
            existing = [m.uid for m in found if m.uid]
            subjects = [m.subject or "" for m in found if m.uid]
            missing = [u for u in requested if u not in existing]
            if not existing:
                return {
                    "deleted": 0,
                    "uids": [],
                    "subjects": [],
                    "mode": "none",
                    "missing": missing,
                }
            if use_trash:
                target = self._resolve_trash_folder(mailbox, trash)
                mailbox.move(existing, target)
                mode = "trash"
            else:
                mailbox.delete(existing)
                mode = "permanent"
        return {
            "deleted": len(existing),
            "uids": existing,
            "subjects": subjects,
            "mode": mode,
            "missing": missing,
        }

    @staticmethod
    def _resolve_trash_folder(mailbox: object, configured: str) -> str:
        """Find the real trash folder name on this server.

        Folder names differ per provider (plain ``Trash``, Gmail's
        ``[Gmail]/Trash``, ``Deleted Items``, ...). We resolve in order:

        1. the configured name, if it actually exists on the server;
        2. the folder carrying the IMAP SPECIAL-USE ``\\Trash`` attribute
           (the provider-blessed trash, e.g. Gmail's ``[Gmail]/Trash``);
        3. a case-insensitive match on common trash names.

        Raises a clear, actionable error (listing the real folders) instead of
        letting a bad name surface as a cryptic ``MailboxCopyError`` on move.
        """
        folders = list(mailbox.folder.list())  # type: ignore[attr-defined]
        names = [f.name for f in folders]

        if configured and configured in names:
            return configured
        for f in folders:
            if "\\Trash" in (getattr(f, "flags", ()) or ()):
                return f.name
        common = {"trash", "bin", "deleted", "deleted items", "deleted messages"}
        for f in folders:
            if f.name.split("/")[-1].strip().lower() in common:
                return f.name
        raise ValueError(
            f"Trash folder {configured!r} not found and no \\Trash folder is "
            f"advertised by the server. Set EMAIL_TRASH_FOLDER to one of: "
            f"{', '.join(names)} -- or pass permanent=True to expunge instead."
        )

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
