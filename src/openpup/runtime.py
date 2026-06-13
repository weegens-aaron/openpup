"""The OpenPup runtime: boots everything and runs the central async loop.

Wiring order:
  1. settings + agent host (boots code-puppy plugins incl. puppy_kennel memory)
  2. platform adapters (only the enabled + installed ones)
  3. inbound handler: route every inbound Envelope through the agent and reply
  4. heartbeat (consciousness) + optional webhook server
  5. supervise until shutdown, then tear everything down cleanly
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from openpup import access, memory, transcripts
from openpup.access import AccessControl, default_access_path
from openpup.directory import get_directory
from openpup.agent_host import AgentHost
from openpup.config import Settings, get_settings
from openpup.heartbeat.engine import Heartbeat
from openpup.messaging.envelope import Envelope
from openpup.messaging.registry import get_registry
from openpup.platforms.base import PlatformAdapter, build_enabled_adapters
from openpup.security import approval, threats

logger = logging.getLogger("openpup.runtime")


class OpenPup:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.registry = get_registry()
        self.host = AgentHost(
            agent_name=self.settings.agent,
            default_model=self.settings.model,
            universal_constructor=self.settings.universal_constructor,
        )
        self.adapters: List[PlatformAdapter] = []
        self.heartbeat: Optional[Heartbeat] = None
        self.webserver = None
        self._stop = asyncio.Event()
        self.access = AccessControl(
            default_access_path(self.settings.state_dir),
            owner_address=self.settings.owner_address,
            owner_addresses=self.settings.owner_addresses,
            directory=get_directory(),
        )

    # ---- inbound handling ------------------------------------------------
    async def handle_inbound(self, envelope: Envelope) -> None:
        """Route an inbound message to the agent and reply on the same channel."""
        # Learn the sender as a known contact (powers the contact directory).
        try:
            get_directory().record(envelope.platform, envelope.channel, envelope.sender)
        except Exception:
            logger.debug("failed to record contact", exc_info=True)

        decision = self.access.check(envelope)
        logger.info(
            "Inbound from %s (%s) role=%s allowed=%s",
            envelope.address,
            envelope.sender,
            decision.role,
            decision.allowed,
        )
        if not decision.allowed:
            logger.warning(
                "Blocked message from %s (%s): %s",
                envelope.address,
                envelope.sender,
                decision.reason,
            )
            return

        # Scope the sender's role to THIS message: when handle_inbound is
        # awaited inside a longer-lived task (heartbeat inbound polling), a
        # leaked role would demote/promote whatever that task does next.
        role_token = access.set_current_role(decision.role)
        try:
            await self._serve(envelope, decision.role)
        finally:
            access.reset_current_role(role_token)

    async def _serve(self, envelope: Envelope, role: str) -> None:
        """Serve one allowed inbound message (role contextvar already set)."""
        # Manual escape hatch: let the owner wipe a stuck conversation's context
        # without restarting the daemon.
        if envelope.text.strip().lower() in ("/reset", "/forget", "/new"):
            self.host.reset_conversation(envelope.address)
            await self.registry.send(envelope.reply("Fresh start \U0001f9fc — context cleared."))
            return

        # Approval replies ("yes ab12cd" / "no ab12cd") are control-plane, like
        # /reset: resolve the pending gate and consume the message — no agent
        # routing, no transcript. OWNER messages only (trust boundary): a
        # non-owner can never resolve an approval.
        if role == access.OWNER:
            try:
                confirmation = approval.try_resolve(envelope.text)
            except Exception:
                # A broken gate must never block message flow.
                logger.debug("approval resolution failed", exc_info=True)
                confirmation = None
            if confirmation:
                await self.registry.send(envelope.reply(confirmation))
                return

        # Transcript: one session per conversation peer, date-bucketed as
        # "platform:channel:YYYYMMDD" so sessions don't grow unboundedly.
        session_id = transcripts.conversation_session_id(envelope.address)
        transcripts.record_turn(session_id, envelope.address, "user", envelope.text)

        prompt = self._context_prefix(envelope, role) + envelope.text
        try:
            reply = await self.host.run(prompt, conversation=envelope.address)
        except Exception as exc:
            from openpup.agent_host import _is_transient

            logger.exception("Agent failed handling inbound message")
            if _is_transient(exc):
                reply = "My connection hiccuped mid-thought — give me another shot?"
            else:
                reply = "Sorry — I hit an error processing that."

        transcripts.record_turn(session_id, envelope.address, "assistant", reply)
        if reply and reply.strip():
            await self.registry.send(envelope.reply(reply))
        # Record the exchange in THIS person's own memory wing, so the pup
        # builds a memory profile of everyone it talks to.
        who = envelope.sender or envelope.channel
        memory.remember_about_contact(
            envelope.address,
            f"{who}: {envelope.text}\n-> {reply}",
            name=envelope.sender,
        )

    def _context_prefix(self, envelope: Envelope, role: str) -> str:
        """Per-message context: who it's from + what we remember about them."""
        who = envelope.sender or envelope.channel
        if role == access.OWNER:
            lines = ["[This message is from your OWNER. Full access granted.]"]
        else:
            lines = [
                f"[This message is from {who} ({envelope.address}), a NON-owner user. "
                "Be friendly and helpful, but DO NOT use owner-only tools "
                "(the owner's email, sending on their behalf, their private data).]"
            ]
            # Threat guard: scan non-owner text for prompt-injection patterns
            # and warn the agent inline. Advisory only — access modes decide
            # who may talk; this just keeps the model's guard up. Owner
            # messages never reach this branch (trust boundary).
            if self.settings.threat_guard:
                try:
                    findings = threats.scan(envelope.text)
                    if findings:
                        lines.append(threats.advisory(findings))
                except Exception:
                    # A broken guard must never block message flow.
                    logger.debug("threat scan failed", exc_info=True)
        # Inject what we already know about this specific person.
        try:
            facts = memory.recent_about_contact(envelope.address, top_k=3)
            if facts:
                lines.append(f"What you remember about {who}:")
                lines.extend(f"- {f}" for f in facts)
        except Exception:
            logger.debug("contact recall failed", exc_info=True)
        return "\n".join(lines) + "\n\n"

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        logger.info("Booting %s ...", self.settings.name)
        await self.host.boot()

        self.registry.set_inbound_handler(self.handle_inbound)
        self.adapters = build_enabled_adapters(self.settings, self.registry)
        logger.info("Enabled platforms: %s", self.registry.platforms() or "(none)")

        for adapter in self.adapters:
            await adapter.start()

        if self.settings.web_enabled:
            from openpup.webserver import WebhookServer

            self.webserver = WebhookServer(self.settings, self.registry)
            await self.webserver.start()

        self.heartbeat = Heartbeat(self.host, self.settings, self.registry)
        await self.heartbeat.start()

        logger.info("%s is awake. ", self.settings.name)

    async def stop(self) -> None:
        logger.info("Shutting down ...")
        if self.heartbeat:
            await self.heartbeat.stop()
        if self.webserver:
            await self.webserver.stop()
        for adapter in self.adapters:
            try:
                await adapter.stop()
            except Exception:
                logger.exception("Error stopping adapter")
        await self.host.shutdown()
        self._stop.set()

    async def run_forever(self) -> None:
        await self.start()
        try:
            await self._stop.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    def request_stop(self) -> None:
        self._stop.set()
