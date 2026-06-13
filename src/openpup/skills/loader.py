"""Level-1 skill disclosure: a compact index block for the system prompt.

Per the agentskills.io progressive-disclosure model, only ``name`` +
``description`` of each installed skill are always in context; the full
SKILL.md body is loaded on demand via the ``openpup_skill`` tool.
"""

from __future__ import annotations

import logging

from openpup.security.skills_guard import is_quarantined
from openpup.skills.store import get_skill_store

logger = logging.getLogger("openpup.skills")

SKILL_TOOL_NAME = "openpup_skill"
MAX_INDEX_SKILLS = 30


def skill_index_block(limit: int = MAX_INDEX_SKILLS) -> str:
    """One ``- name: description`` line per active/pinned skill.

    Returns "" when there are no skills (or the store is unavailable), so
    callers can append it unconditionally. Capped at *limit* entries with a
    trailing "...and N more" hint.
    """
    try:
        # Skills the guard would refuse to load aren't advertised either
        # (is_quarantined never raises and honors the pinned-user exemption).
        skills = [s for s in get_skill_store().list() if not is_quarantined(s)]
    except Exception:
        logger.debug("skill index unavailable", exc_info=True)
        return ""
    if not skills:
        return ""
    lines = [
        "# Skills",
        "Installed skills extend what you know how to do. When a request matches",
        f"a skill's description, load its full instructions with the {SKILL_TOOL_NAME} tool.",
    ]
    lines.extend(f"- {s.name}: {' '.join(s.description.split())}" for s in skills[:limit])
    hidden = len(skills) - limit
    if hidden > 0:
        lines.append(f"...and {hidden} more -- use {SKILL_TOOL_NAME} to list them all.")
    return "\n".join(lines)
