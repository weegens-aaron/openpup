"""Discord adapter built on discord.py.

Listens for DMs and messages that mention the bot, converts them to Envelopes,
and sends outbound Envelopes back to the originating channel.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from openpup.config import Settings
from openpup.messaging.envelope import Envelope
from openpup.messaging.registry import PlatformRegistry
from openpup.platforms.base import PlatformAdapter

logger = logging.getLogger("openpup.discord")


class DiscordAdapter(PlatformAdapter):
    name = "discord"

    def __init__(self, settings: Settings, registry: PlatformRegistry) -> None:
        super().__init__(settings, registry)
        if not settings.discord_bot_token:
            raise ValueError("DISCORD_BOT_TOKEN is required when discord is enabled")
        import discord  # noqa: F401  (raises ImportError if extra not installed)

        self._discord = discord
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        self._client = discord.Client(intents=intents)
        self._task: Optional[asyncio.Task] = None
        self._register_events()

    def _register_events(self) -> None:
        client = self._client

        @client.event
        async def on_ready() -> None:  # pragma: no cover - needs live gateway
            logger.info("Discord connected as %s", client.user)

        @client.event
        async def on_message(message) -> None:  # pragma: no cover - needs live gateway
            await self._handle_message(message)

    def _message_to_envelope(self, message) -> Optional[Envelope]:
        """Convert a discord Message to an Envelope, or None if it's ignorable.

        Only DMs and messages that @mention the bot are accepted; the bot's
        own messages are skipped.
        """
        if message.author == self._client.user:
            return None
        is_dm = message.guild is None
        mentioned = self._client.user in getattr(message, "mentions", [])
        if not (is_dm or mentioned):
            return None
        author_id = getattr(message.author, "id", None)
        return Envelope(
            platform=self.name,
            channel=str(message.channel.id),
            sender=str(message.author),
            sender_id=str(author_id) if author_id is not None else None,
            text=message.content or "",
        )

    async def _handle_message(self, message) -> None:
        envelope = self._message_to_envelope(message)
        if envelope is not None:
            await self.registry.dispatch_inbound(envelope)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._client.start(self.settings.discord_bot_token))
        # The gateway handshake runs in the background task above. We must not
        # return until the client is actually ready, otherwise an immediate
        # outbound send (e.g. the `say` one-off command) races ahead of
        # discord.py's HTTP setup and explodes on an uninitialised internal
        # event. Bounded so a bad token / network stall fails fast instead of
        # hanging forever.
        ready = asyncio.ensure_future(self._client.wait_until_ready())
        done, _ = await asyncio.wait(
            {ready, self._task}, timeout=30, return_when=asyncio.FIRST_COMPLETED
        )
        if self._task in done:
            # start() finished/failed before we ever became ready -> surface it.
            ready.cancel()
            exc = self._task.exception()
            if exc is not None:
                raise exc
        elif ready not in done:
            ready.cancel()
            logger.warning("Discord client not ready within 30s; continuing anyway")
        logger.info("Discord adapter started")

    async def stop(self) -> None:
        try:
            await self._client.close()
        finally:
            if self._task:
                self._task.cancel()
        logger.info("Discord adapter stopped")

    async def send(self, envelope: Envelope) -> None:  # pragma: no cover - needs gateway
        target_id = int(envelope.channel)
        channel = self._client.get_channel(target_id)
        if channel is None:
            channel = await self._resolve_target(target_id)
        for chunk in _chunk(envelope.text, 1900):
            await channel.send(chunk)

    async def _resolve_target(self, target_id: int):
        """Resolve an id that may be a channel OR a user.

        Outbound addresses like ``discord:<user_id>`` (e.g. the owner) should
        Just Work, so if the id isn't a channel we can fetch, fall back to
        treating it as a user and opening a DM channel with them.
        """
        discord = self._discord
        try:
            return await self._client.fetch_channel(target_id)
        except (discord.NotFound, discord.Forbidden, discord.InvalidData):
            user = self._client.get_user(target_id) or await self._client.fetch_user(target_id)
            return user.dm_channel or await user.create_dm()


def _chunk(text: str, size: int):
    text = text or ""
    if not text:
        return
    for i in range(0, len(text), size):
        yield text[i : i + size]
