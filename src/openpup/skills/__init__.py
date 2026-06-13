"""agentskills.io-compatible skills: SKILL.md folders the agent loads on demand.

* ``store``  -- discovery, validation, lifecycle (create / archive / pin)
* ``loader`` -- the Level-1 index block injected into the system prompt
"""

from openpup.skills.loader import skill_index_block
from openpup.skills.store import Skill, SkillStore, get_skill_store

__all__ = ["Skill", "SkillStore", "get_skill_store", "skill_index_block"]
