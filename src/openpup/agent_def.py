"""Generated, named agent definition (hermes-style identity).

OpenPup used to drive the stock ``code-puppy`` agent, so the pup's *agent*
was always named "code-puppy" regardless of ``OPENPUP_NAME``. Like hermes,
we now generate a first-class named agent: a JSON agent definition written
into code-puppy's user agents directory, carrying the pup's name and the
full code-puppy toolset.

The static system prompt here is deliberately thin — identity plus coding
operating rules. The rich layered persona (SOUL, capabilities, agentic
guidance, memory) is injected fresh at runtime by ``prompting.build_system_prompt``
via the ``load_prompt`` hook, so we never duplicate it here.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("openpup.agent_def")

#: Sentinel agent setting meaning "generate a named agent from persona".
AUTO_AGENT = "auto"

#: Built-in code-puppy agent names we must not shadow.
_RESERVED_NAMES = {
    "code-puppy",
    "agent-creator",
    "planning",
    "qa-kitten",
    "helios",
}

#: Fallback toolset mirroring CodePuppyAgent.get_available_tools(), used only
#: when the live base agent can't be loaded (e.g. stripped-down test envs).
_FALLBACK_TOOLS = [
    "list_agents",
    "invoke_agent",
    "list_files",
    "read_file",
    "grep",
    "create_file",
    "replace_in_file",
    "delete_snippet",
    "delete_file",
    "agent_run_shell_command",
    "ask_user_question",
    "activate_skill",
    "list_or_search_skills",
    "load_image_for_analysis",
]


def is_auto(agent_setting: Optional[str]) -> bool:
    """True when the agent setting asks us to generate a named agent."""
    return not agent_setting or agent_setting.strip().lower() == AUTO_AGENT


def slugify(name: str) -> str:
    """Kebab-case a display name into a valid agent name.

    "Rex the Pup!" -> "rex-the-pup". Falls back to "openpup" when nothing
    usable survives, and dodges code-puppy's built-in agent names.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    slug = slug or "openpup"
    if slug in _RESERVED_NAMES:
        slug += "-pup"
    return slug


def _base_tools() -> List[str]:
    """The toolset of the stock code-puppy agent, derived live so it never
    drifts from upstream. Falls back to a static mirror if unavailable."""
    try:
        from code_puppy.agents.agent_manager import load_agent

        return list(load_agent("code-puppy").get_available_tools())
    except Exception:
        logger.debug("could not derive base toolset; using fallback", exc_info=True)
        return list(_FALLBACK_TOOLS)


def build_agent_config(name: str, tools: Optional[List[str]] = None) -> Dict:
    """Build the JSON agent definition for a pup named ``name``."""
    slug = slugify(name)
    return {
        "name": slug,
        "display_name": name,
        "description": (
            f"{name} — an always-on companion agent (OpenPup) with full coding-agent tooling."
        ),
        "system_prompt": [
            f"You are {name}, an always-on AI companion and capable coding agent.",
            "",
            "Your persona, live capabilities, owner profile, and memory are",
            "injected at runtime — embody them fully.",
            "",
            "When doing hands-on coding work:",
            "- You MUST use tools to take action — never just describe code.",
            "- Explore directories before reading; read files before modifying.",
            "- Prefer replace_in_file over create_file; keep diffs small.",
            "- Keep files under 600 lines; split only when it helps cohesion.",
            "- Obey the Zen of Python, even outside Python.",
            "- Continue autonomously unless user input is definitively required.",
        ],
        "tools": tools if tools is not None else _base_tools(),
    }


def agent_file_path(name: str) -> Path:
    """Where the generated definition lives (namespaced to avoid stomping
    the user's own agent files)."""
    from code_puppy.config import get_user_agents_directory

    return Path(get_user_agents_directory()) / f"openpup-{slugify(name)}.json"


def ensure_agent(name: Optional[str] = None) -> str:
    """Write (or refresh) the generated agent definition; return its name.

    Regenerated on every boot so persona renames propagate. SOUL.md remains
    the hand-editable identity — this file is derived output.
    """
    if name is None:
        from openpup.config import get_settings

        name = get_settings().name
    config = build_agent_config(name)
    path = agent_file_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    logger.info("Generated agent '%s' at %s", config["name"], path)
    return config["name"]


def resolve_agent_name(agent_setting: Optional[str], name: Optional[str] = None) -> str:
    """Map the OPENPUP_AGENT setting to a loadable agent name.

    ``auto`` (or blank) generates the named agent; anything else is an
    explicit code-puppy agent name and passes straight through. Falls back
    to plain ``code-puppy`` if generation fails, so booting never breaks.
    """
    if not is_auto(agent_setting):
        return agent_setting.strip()  # type: ignore[union-attr]
    try:
        return ensure_agent(name)
    except Exception:
        logger.warning("Could not generate named agent; falling back to code-puppy", exc_info=True)
        return "code-puppy"
