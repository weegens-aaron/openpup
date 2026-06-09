"""Headless harness around the code-puppy agent SDK.

This is the seam that lets OpenPup use code-puppy as a *library*: it boots the
plugin system (which brings in puppy_kennel memory), loads an agent, and runs
prompts programmatically — no TUI, no prompt-toolkit loop.

Per-conversation history is kept here, keyed by ``platform:channel`` address,
so each Discord channel / Telegram chat / email thread has its own context.
The underlying ``BaseAgent`` holds a single ``_message_history`` list, so we
swap histories in/out under a lock around each run.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openpup.agent_host")


class AgentHost:
    """Boots and drives a code-puppy agent for OpenPup."""

    def __init__(
        self,
        agent_name: str = "code-puppy",
        default_model: Optional[str] = None,
        universal_constructor: bool = True,
    ) -> None:
        self.agent_name = agent_name
        self.default_model = default_model
        self.universal_constructor = universal_constructor
        self._agent: Any = None
        self._lock = asyncio.Lock()
        # address -> message history list
        self._histories: Dict[str, List[Any]] = {}

    # ---- lifecycle -------------------------------------------------------
    async def boot(self) -> None:
        """Load plugins (kennel et al.), fire startup hooks, load the agent."""
        from code_puppy.agents.agent_manager import load_agent
        from code_puppy.callbacks import register_callback
        from code_puppy.plugins import load_plugin_callbacks

        load_plugin_callbacks()

        # Give the agent OpenPup's own tools + a hermes-style layered identity
        # so it actually uses its integrations and works agentically instead of
        # acting like plain code-puppy. Same hook pattern the kennel uses.
        from openpup import agent_tools, agentic, prompting

        prompting.ensure_templates()

        register_callback("register_tools", agent_tools.register_tools_callback)
        register_callback("register_agent_tools", agent_tools.advertise_tools)
        # Agentic task-list (todo) tool, ported from hermes.
        register_callback("register_tools", agentic.register_tools_callback)
        register_callback("register_agent_tools", agentic.advertise_tools)
        # Layered system prompt (SOUL + agentic guidance + user/memory snapshots).
        register_callback("load_prompt", prompting.build_system_prompt)

        # Universal Constructor: let the agent forge its own tools at runtime.
        # This is a core code-puppy capability gated by its own config flag;
        # we flip it to match the OpenPup setting, and advertise the tool below.
        try:
            from code_puppy.config import set_universal_constructor_enabled

            set_universal_constructor_enabled(self.universal_constructor)
        except Exception:
            logger.debug("could not set universal_constructor flag", exc_info=True)

        try:
            from code_puppy import callbacks

            await callbacks.on_startup()
        except Exception:  # startup hooks must never block boot
            logger.debug("startup hooks raised (non-fatal)", exc_info=True)

        self._agent = load_agent(self.agent_name)
        logger.info("AgentHost booted agent '%s'", self.agent_name)

    async def shutdown(self) -> None:
        try:
            from code_puppy import callbacks

            await callbacks.on_shutdown()
        except Exception:
            logger.debug("shutdown hooks raised (non-fatal)", exc_info=True)

    # ---- running ---------------------------------------------------------
    async def run(
        self,
        prompt: str,
        *,
        conversation: str = "default",
        model: Optional[str] = None,
        keep_history: bool = True,
    ) -> str:
        """Run ``prompt`` in a named conversation and return the response text.

        ``conversation`` is typically an envelope address (``platform:channel``)
        so separate chats keep separate context. ``model`` overrides the model
        for just this run (used by cheap reflection ticks).
        """
        if self._agent is None:
            raise RuntimeError("AgentHost.boot() must be called before run()")

        # Tell the agentic todo tool which conversation's task list is active.
        from openpup import agentic

        agentic.set_conversation(conversation)

        async with self._lock:
            history = self._histories.get(conversation, [])
            self._agent.set_message_history(list(history))

            effective_model = model or self.default_model
            try:
                if effective_model:
                    with self._agent.temporary_model_name_override(effective_model):
                        result = await self._agent.run_with_mcp(prompt)
                else:
                    result = await self._agent.run_with_mcp(prompt)
            except Exception:
                logger.exception("Agent run failed for conversation '%s'", conversation)
                raise

            if keep_history:
                self._histories[conversation] = list(self._agent.get_message_history())

            return _extract_text(result)

    def reset_conversation(self, conversation: str) -> None:
        self._histories.pop(conversation, None)


def _extract_text(result: Any) -> str:
    """Pull the response string out of a pydantic-ai run result."""
    for attr in ("output", "data"):
        value = getattr(result, attr, None)
        if isinstance(value, str):
            return value
        if value is not None:
            return str(value)
    return str(result)
