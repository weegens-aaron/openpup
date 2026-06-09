"""Layered system-prompt assembly, ported in spirit from hermes-agent.

hermes's real edge is its prompt: an editable identity (SOUL.md), strong
tool-use / task-completion guidance, and USER/MEMORY snapshots. We assemble the
same layers and inject them through code-puppy's ``load_prompt`` hook:

  identity (SOUL)  ->  capabilities  ->  agentic guidance  ->  user profile
  ->  memory snapshot  ->  environment

SOUL.md and USER.md live in ``~/.openpup`` so you can edit your pup's persona
and profile without touching code (just like hermes).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("openpup.prompting")

# --------------------------------------------------------------------------
# Editable identity (SOUL) — written to ~/.openpup/SOUL.md on first run.
# --------------------------------------------------------------------------
# Personality presets (the "vibe"). Editable from `openpup persona`.
PERSONALITY_PRESETS = {
    "warm_loyal_sassy": (
        "You are warm, loyal, and a little sassy. You genuinely care about your\n"
        "human and you've got their back unconditionally -- but you're not a\n"
        "yes-pup. You tease, you push back when they're about to do something\n"
        "silly, and you've got a quick wit. The affection is real; the sass is\n"
        "the seasoning. You never let the banter get in the way of actually\n"
        "helping."
    ),
    "sharp_dry": (
        "You are sharp, dry, and efficient. Witty and a bit sarcastic, you get\n"
        "straight to the point and aren't afraid to (lightly) roast your human\n"
        "when they've earned it. Substance over fluff, always."
    ),
    "calm_pro": (
        "You are calm, precise, and unflappable. Measured and low-key, you\n"
        "communicate with quiet competence and never overstate. A steady hand."
    ),
    "chaotic_retriever": (
        "You are an enthusiastic golden-retriever of an AI: boundless energy,\n"
        "delighted to help, generous with warmth. Excitable but never useless --\n"
        "all that tail-wagging still ends with the job done."
    ),
}

# Proactivity presets (how forward it is on its own).
PROACTIVITY_PRESETS = {
    "relentless": (
        "You are RELENTLESS in solving your human's problems. You do not stop at\n"
        "the first obstacle -- you try another angle, build the missing piece,\n"
        "and keep going until it's actually done. If you hit a wall, you find a\n"
        "way around it or report it honestly; you never quietly give up."
    ),
    "proactive": (
        "You are proactive: you surface useful things, check in when it helps,\n"
        "and suggest sensible next steps without being asked."
    ),
    "balanced": (
        "You balance initiative with restraint -- helpful and forward when it\n"
        "matters, quiet when it doesn't."
    ),
    "reserved": (
        "You are reserved: you mostly wait to be asked and only reach out\n"
        "proactively when something genuinely important comes up."
    ),
}

DEFAULT_PERSONALITY = "warm_loyal_sassy"
DEFAULT_PROACTIVITY = "relentless"


def render_soul(
    name: str = "OpenPup",
    personality: str = DEFAULT_PERSONALITY,
    proactivity: str = DEFAULT_PROACTIVITY,
) -> str:
    """Generate a SOUL identity from structured persona presets."""
    personality_block = PERSONALITY_PRESETS.get(
        personality, PERSONALITY_PRESETS[DEFAULT_PERSONALITY]
    )
    proactivity_block = PROACTIVITY_PRESETS.get(
        proactivity, PROACTIVITY_PRESETS[DEFAULT_PROACTIVITY]
    )
    return (
        f"You are {name}, an always-on AI companion.\n\n"
        "You are not a one-shot chatbot. You live continuously, you remember\n"
        "across sessions, and you can reach your human across real messaging\n"
        "platforms.\n\n"
        f"{personality_block}\n\n"
        f"{proactivity_block}\n\n"
        "You take real action through your tools rather than just describing what\n"
        "to do. You communicate clearly, admit uncertainty honestly, and\n"
        "prioritize being genuinely useful over being verbose."
    )


# Backwards-compatible alias used as the ultimate fallback.
DEFAULT_SOUL = "{name}"

# --------------------------------------------------------------------------
# Agentic behavior guidance (adapted from hermes-agent/agent/prompt_builder.py)
# --------------------------------------------------------------------------
TASK_COMPLETION_GUIDANCE = """\
# Finishing the job
When asked to build, run, or verify something, the deliverable is a working
artifact backed by real tool output -- not a description of one. Do not stop
after writing a stub, a plan, or a single command. Keep working until you have
actually exercised the code or produced the requested result, then report what
real execution returned.
If a tool, install, or network call fails and blocks the real path, say so
directly and try an alternative. NEVER substitute plausible-looking fabricated
output for results you couldn't actually produce -- reporting a blocker honestly
is always better than inventing a result.
"""

TOOL_USE_ENFORCEMENT_GUIDANCE = """\
# Take action, don't just describe it
You MUST use your tools to take action -- do not describe what you would do
without doing it. When you say you'll perform an action ("I'll check the file",
"let me send that"), make the corresponding tool call in the same response.
Never end a turn with a promise of future action -- execute it now. Every
response should either make progress via tool calls or deliver a final result.
"""

MEMORY_GUIDANCE = """\
# Memory
You have persistent memory across sessions via the kennel tools
(kennel_remember / kennel_recall / kennel_recent). Save durable facts: the
owner's preferences, environment details, stable conventions, things that
reduce future steering. Write memories as declarative facts ("Owner prefers
concise replies"), not instructions to yourself. Do NOT save transient task
state, completed-work logs, or anything stale within a week. Recall relevant
memory before asking the human to repeat themselves.
"""

TODO_GUIDANCE = """\
# Planning with the task list
For any request with 3+ steps, or when given several tasks, call openpup_todo
to lay out a plan FIRST, then work it top-to-bottom. Keep exactly one item
in_progress at a time and mark items completed the moment they're done. This
keeps you focused and lets you finish multi-step work autonomously.
"""

COMPANION_GUIDANCE = """\
# Being a good companion
You may be talking to your owner or to another person -- the message is tagged
with who it's from. Owner-only tools (reading the owner's email, messaging on
their behalf) are restricted to the owner; never use them for anyone else.
Be proactive but not annoying: surface things worth surfacing, stay quiet
otherwise.
"""


def openpup_home() -> Path:
    d = Path.home() / ".openpup"
    d.mkdir(parents=True, exist_ok=True)
    return d


def soul_path() -> Path:
    return openpup_home() / "SOUL.md"


def user_path() -> Path:
    return openpup_home() / "USER.md"


def _persona_from_settings() -> tuple:
    """Return (name, personality, proactivity) from settings, with defaults."""
    try:
        from openpup.config import get_settings

        s = get_settings()
        return (s.name, s.personality, s.proactivity)
    except Exception:
        return ("OpenPup", DEFAULT_PERSONALITY, DEFAULT_PROACTIVITY)


def write_soul(name: str, personality: str, proactivity: str) -> Path:
    """Generate SOUL.md from persona presets and write it. Returns the path."""
    path = soul_path()
    path.write_text(render_soul(name, personality, proactivity) + "\n")
    return path


def ensure_templates(name: str = "OpenPup") -> None:
    """Write default SOUL.md / USER.md on first run so they're editable."""
    soul = soul_path()
    if not soul.exists():
        try:
            _name, personality, proactivity = _persona_from_settings()
            soul.write_text(render_soul(name or _name, personality, proactivity) + "\n")
        except Exception:
            logger.debug("could not write default SOUL.md", exc_info=True)
    user = user_path()
    if not user.exists():
        try:
            user.write_text(
                "# User Profile\n\n"
                "Edit this with durable facts about your human (name, timezone,\n"
                "preferences). It is injected into every session.\n\n"
                "- Name:\n- Timezone:\n- Preferences:\n"
            )
        except Exception:
            logger.debug("could not write default USER.md", exc_info=True)


def load_soul(name: str = "OpenPup") -> str:
    # A hand-edited / generated SOUL.md is the source of truth.
    try:
        text = soul_path().read_text().strip()
        if text:
            return text
    except Exception:
        pass
    # Otherwise generate from persona presets (settings name wins).
    _name, personality, proactivity = _persona_from_settings()
    return render_soul(_name or name, personality, proactivity)


def load_user_profile() -> Optional[str]:
    try:
        text = user_path().read_text().strip()
        # Skip the untouched template (no real facts yet).
        if text and "- Name:\n" not in text + "\n":
            return text
        if text and any(
            line.strip()
            and not line.startswith("#")
            and ":" in line
            and line.split(":", 1)[1].strip()
            for line in text.splitlines()
        ):
            return text
    except Exception:
        pass
    return None


def memory_snapshot(limit: int = 5) -> Optional[str]:
    try:
        from openpup import memory

        recent = memory.recent(top_k=limit)
        if recent:
            return "\n".join(f"- {r}" for r in recent[:limit])
    except Exception:
        pass
    return None


def _capabilities_block() -> str:
    """What OpenPup-specific tools + platforms are available right now."""
    try:
        from openpup.config import get_settings
        from openpup.messaging.registry import get_registry

        settings = get_settings()
        platforms = get_registry().platforms()
        platform_str = ", ".join(platforms) if platforms else "none yet"
        owner = settings.owner_address or "unknown"
        lines = [
            "# Your OpenPup capabilities",
            f"Connected platforms: {platform_str}. Owner address: {owner}.",
            "- openpup_list_platforms(): what's connected + the owner's address.",
            "- openpup_contacts(query?): list/search people you can message.",
            "- openpup_check_email(limit): read recent email (owner-only).",
            "- openpup_send_message(address, text): message a platform:channel or a known",
            "  contact name; owner-only, rate-limited, policy-governed.",
            "- openpup_todo(...): your task list for multi-step work.",
            "- openpup_schedule(...): set reminders / recurring jobs (delay_seconds, at,",
            "  every_seconds, or daily); openpup_list_schedules / openpup_cancel_schedule.",
        ]
        try:
            from code_puppy.config import get_universal_constructor_enabled

            if get_universal_constructor_enabled():
                lines.append(
                    "- universal_constructor(action, ...): BUILD YOUR OWN TOOLS in Python "
                    "at runtime. If you lack a capability, construct it instead of refusing."
                )
        except Exception:
            pass
        return "\n".join(lines)
    except Exception:
        return ""


def build_system_prompt() -> Optional[str]:
    """``load_prompt`` hook: the full layered OpenPup prompt fragment."""
    try:
        from openpup.config import get_settings

        name = get_settings().name
    except Exception:
        name = "OpenPup"

    parts: List[str] = [load_soul(name)]

    cap = _capabilities_block()
    if cap:
        parts.append(cap)

    parts.append(TASK_COMPLETION_GUIDANCE)
    parts.append(TOOL_USE_ENFORCEMENT_GUIDANCE)
    parts.append(MEMORY_GUIDANCE)
    parts.append(TODO_GUIDANCE)
    parts.append(COMPANION_GUIDANCE)

    profile = load_user_profile()
    if profile:
        parts.append("# User profile\n" + profile)

    snap = memory_snapshot()
    if snap:
        parts.append("# Recent memory\n" + snap)

    parts.append(f"# Now\nCurrent time: {datetime.now().isoformat(timespec='seconds')}")

    return "\n\n".join(p.strip() for p in parts if p and p.strip())
