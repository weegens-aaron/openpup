"""Skill store: agentskills.io-compatible skill folders.

A *skill* is a directory containing a ``SKILL.md`` (YAML frontmatter +
markdown instructions), optionally bundling ``scripts/``, ``references/``,
and ``assets/``. Skills live under ``~/.openpup/skills/<name>/`` with one
optional level of category nesting (``skills/<category>/<name>/``), like
hermes-agent.

Design notes (inspired by hermes-agent's skill_utils / skills_tool):

* frontmatter is parsed with a minimal hand-rolled YAML-subset parser
  (key: value, folded scalars, ``- item`` lists, one-level ``metadata:``
  maps) -- no new dependency;
* validation follows the agentskills.io spec (see
  plans/agentskills-spec-notes.md): required ``name`` (lowercase/digits/
  hyphens, <=64 chars, must match the folder) and ``description``
  (non-empty, <=1024 chars); unknown fields are tolerated;
* invalid skills are skipped with a warning -- discovery never crashes;
* nothing is ever deleted: archiving moves the folder to
  ``skills/.archive/<name>/`` (the hermes invariant);
* lifecycle state (``active`` | ``pinned`` | ``archived``) and provenance
  (``created_by``) live in frontmatter ``metadata``.

Mutating methods (create/update/archive/...) raise ``ValueError`` on bad
input so the tool layer can report precisely; read paths degrade gracefully.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("openpup.skills")

SKILL_FILE = "SKILL.md"
ARCHIVE_DIR = ".archive"
AGENT_AUTHOR = "openpup"  # metadata.created_by value for agent-created skills

MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
STATES = ("active", "pinned", "archived")

# --------------------------------------------------------------------------
# Frontmatter (minimal YAML subset: enough for the agentskills.io schema)
# --------------------------------------------------------------------------
_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")


def _scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def _parse_block(lines: List[str]) -> Dict[str, Any]:
    """Parse frontmatter lines: scalars (with folded continuations), lists,
    and one level of nested maps (the conventional ``metadata:`` shape)."""
    out: Dict[str, Any] = {}
    last_scalar_key: Optional[str] = None
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0] in " \t":
            # Indented continuation of the previous folded scalar.
            if last_scalar_key is not None:
                out[last_scalar_key] = f"{out[last_scalar_key]} {line.strip()}".strip()
            i += 1
            continue
        match = _KEY_RE.match(line)
        if not match:
            i += 1
            continue
        key, raw = match.group(1), match.group(2)
        if raw:
            out[key] = _scalar(raw)
            last_scalar_key = key
            i += 1
            continue
        # Empty value: gather the indented block that follows.
        last_scalar_key = None
        block: List[str] = []
        j = i + 1
        while j < n and (not lines[j].strip() or lines[j][0] in " \t"):
            if lines[j].strip():
                block.append(lines[j].strip())
            j += 1
        if block and all(item.startswith("- ") for item in block):
            out[key] = [_scalar(item[2:]) for item in block]
        elif block:
            sub: Dict[str, str] = {}
            for item in block:
                sub_match = _KEY_RE.match(item)
                if sub_match:
                    sub[sub_match.group(1)] = _scalar(sub_match.group(2))
            out[key] = sub
        else:
            out[key] = ""
        i = j
    return out


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Split a SKILL.md into (frontmatter dict, markdown body)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = next((i for i, ln in enumerate(lines[1:], 1) if ln.strip() in ("---", "...")), None)
    if end is None:
        return {}, text
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return _parse_block(lines[1:end]), body


def _plain(value: Any) -> str:
    """Single-line plain scalar (newlines folded to spaces)."""
    return " ".join(str(value).split())


def render_frontmatter(frontmatter: Dict[str, Any]) -> str:
    """Serialize a frontmatter dict; round-trips through parse_frontmatter."""
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            lines.extend(f"  {k}: {_plain(v)}" for k, v in value.items())
        elif isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            lines.extend(f"  - {_plain(v)}" for v in value)
        else:
            lines.append(f"{key}: {_plain(value)}")
    lines.append("---")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Validation (agentskills.io required fields)
# --------------------------------------------------------------------------
def validate_name(name: str) -> Optional[str]:
    """Return an error string, or None when the name is spec-compliant."""
    if not name:
        return "missing required field: name"
    if len(name) > MAX_NAME_LEN:
        return f"name exceeds {MAX_NAME_LEN} characters"
    if not NAME_RE.match(name):
        return "name must be lowercase letters/digits/hyphens (no leading/trailing/double hyphens)"
    return None


def validate_description(description: Any) -> Optional[str]:
    """Return an error string, or None when the description is valid."""
    text = str(description or "").strip()
    if not text:
        return "missing required field: description"
    if len(text) > MAX_DESCRIPTION_LEN:
        return f"description exceeds {MAX_DESCRIPTION_LEN} characters"
    return None


def _metadata(frontmatter: Dict[str, Any]) -> Dict[str, Any]:
    meta = frontmatter.get("metadata")
    return meta if isinstance(meta, dict) else {}


# --------------------------------------------------------------------------
# Skill
# --------------------------------------------------------------------------
@dataclass
class Skill:
    """One discovered skill. ``body`` is lazy-loaded from disk on access."""

    name: str
    description: str
    path: Path  # the skill directory (SKILL.md lives inside)
    category: Optional[str] = None
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    created_by: str = "user"  # "user" | "agent"
    state: str = "active"  # active | pinned | archived
    _body: Optional[str] = field(default=None, repr=False, compare=False)

    @property
    def skill_file(self) -> Path:
        return self.path / SKILL_FILE

    @property
    def body(self) -> str:
        if self._body is None:
            try:
                _, self._body = parse_frontmatter(self.skill_file.read_text(encoding="utf-8"))
            except Exception:
                logger.debug("could not read body of %s", self.skill_file, exc_info=True)
                self._body = ""
        return self._body


def _parse_skill(skill_file: Path, category: Optional[str], archived: bool) -> Optional[Skill]:
    """Parse + validate one SKILL.md. Returns None (with a warning) if invalid."""
    try:
        frontmatter, _ = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("unreadable skill at %s; skipping", skill_file)
        return None
    name = str(frontmatter.get("name") or "").strip()
    folder = skill_file.parent.name
    error = validate_name(name) or validate_description(frontmatter.get("description"))
    if not error and name != folder:
        error = f"name {name!r} does not match folder name {folder!r}"
    if error:
        logger.warning("invalid skill at %s: %s; skipping", skill_file, error)
        return None
    meta = _metadata(frontmatter)
    state = str(meta.get("state") or "active").strip().lower()
    if state not in STATES:
        state = "active"
    if archived:
        state = "archived"  # location is the source of truth for archival
    created_by = (
        "agent"
        if str(meta.get("created_by") or "").strip().lower() in (AGENT_AUTHOR, "agent")
        else "user"
    )
    return Skill(
        name=name,
        description=str(frontmatter["description"]).strip(),
        path=skill_file.parent,
        category=category,
        frontmatter=frontmatter,
        created_by=created_by,
        state=state,
    )


def _write_skill_file(skill_dir: Path, frontmatter: Dict[str, Any], body: str) -> None:
    text = render_frontmatter(frontmatter) + "\n\n" + (body or "").strip() + "\n"
    (skill_dir / SKILL_FILE).write_text(text, encoding="utf-8")


def update_metadata(skill_file: Path, updates: Dict[str, str]) -> bool:
    """Merge keys into a SKILL.md's frontmatter ``metadata``, preserving the body.

    Used by the curator (``created_at`` stamping) and the skill tool
    (``last_used`` stamping). The minimal YAML-subset parser can lossily
    round-trip exotic (user-authored) frontmatter, so the rewrite is
    re-validated BEFORE touching disk: the rendered text must re-parse to
    the same name and a valid description, or nothing is written.

    Never raises; returns True only when the file was safely rewritten.
    """
    try:
        frontmatter, body = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        meta = dict(_metadata(frontmatter))
        meta.update(updates)
        frontmatter["metadata"] = meta
        text = render_frontmatter(frontmatter) + "\n\n" + (body or "").strip() + "\n"
        reparsed, _ = parse_frontmatter(text)
        if str(reparsed.get("name") or "") != str(frontmatter.get("name") or "") or (
            validate_description(reparsed.get("description")) is not None
        ):
            logger.warning("metadata update would corrupt %s; leaving it untouched", skill_file)
            return False
        skill_file.write_text(text, encoding="utf-8")
        return True
    except Exception:
        logger.debug("metadata update failed for %s", skill_file, exc_info=True)
        return False


# --------------------------------------------------------------------------
# SkillStore
# --------------------------------------------------------------------------
class SkillStore:
    """Filesystem-backed skill store with mtime-invalidated parse caching."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root is not None else default_skills_root()
        # SKILL.md path -> (mtime_ns, parsed Skill or None when invalid)
        self._cache: Dict[Path, Tuple[int, Optional[Skill]]] = {}

    # ---- discovery -------------------------------------------------------
    def _scan(self) -> List[Tuple[Path, Optional[str], bool]]:
        """Yield (skill_file, category, archived); active skills first."""
        active: List[Tuple[Path, Optional[str], bool]] = []
        archived: List[Tuple[Path, Optional[str], bool]] = []
        try:
            if not self.root.is_dir():
                return []
            for entry in sorted(self.root.iterdir()):
                if not entry.is_dir():
                    continue
                if entry.name == ARCHIVE_DIR:
                    for sub in sorted(entry.iterdir()):
                        if sub.is_dir() and (sub / SKILL_FILE).is_file():
                            archived.append((sub / SKILL_FILE, None, True))
                    continue
                if entry.name.startswith("."):
                    continue
                if (entry / SKILL_FILE).is_file():
                    active.append((entry / SKILL_FILE, None, False))
                    continue
                # No SKILL.md => treat as a category dir, one level deep.
                for sub in sorted(entry.iterdir()):
                    if (
                        sub.is_dir()
                        and not sub.name.startswith(".")
                        and (sub / SKILL_FILE).is_file()
                    ):
                        active.append((sub / SKILL_FILE, entry.name, False))
        except Exception:
            logger.debug("skill scan failed under %s", self.root, exc_info=True)
        return active + archived

    def discover(self) -> List[Skill]:
        """Scan + parse every skill, reusing cached parses when mtime is unchanged."""
        skills: List[Skill] = []
        fresh: Dict[Path, Tuple[int, Optional[Skill]]] = {}
        seen: Dict[str, Path] = {}
        for skill_file, category, archived in self._scan():
            try:
                mtime = skill_file.stat().st_mtime_ns
            except OSError:
                continue
            cached = self._cache.get(skill_file)
            if cached is not None and cached[0] == mtime:
                skill = cached[1]
            else:
                skill = _parse_skill(skill_file, category, archived)
            fresh[skill_file] = (mtime, skill)
            if skill is None:
                continue
            if skill.name in seen:
                logger.warning(
                    "duplicate skill name %r at %s (keeping %s)",
                    skill.name,
                    skill.path,
                    seen[skill.name],
                )
                continue
            seen[skill.name] = skill.path
            skills.append(skill)
        self._cache = fresh
        return skills

    def get(self, name: str) -> Optional[Skill]:
        """Look up a skill by name (archived skills included)."""
        return next((s for s in self.discover() if s.name == name), None)

    def list(self, include_archived: bool = False) -> List[Skill]:
        """Active + pinned skills (sorted by name); archived only on request."""
        return sorted(
            (s for s in self.discover() if include_archived or s.state != "archived"),
            key=lambda s: s.name,
        )

    # ---- mutations (never delete -- archive only) --------------------------
    def create(
        self,
        name: str,
        description: str,
        body: str = "",
        category: Optional[str] = None,
    ) -> Skill:
        """Write a new agent-created skill (metadata.created_by: openpup)."""
        error = validate_name(name) or validate_description(description)
        if error:
            raise ValueError(error)
        if category is not None:
            category_error = validate_name(category)
            if category_error:
                raise ValueError(f"invalid category: {category_error}")
        if self.get(name) is not None:
            raise ValueError(
                f"skill {name!r} already exists (archived skills count -- unarchive instead)"
            )
        skill_dir = self.root / category / name if category else self.root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        frontmatter: Dict[str, Any] = {
            "name": name,
            "description": _plain(description),
            "metadata": {"created_by": AGENT_AUTHOR, "state": "active"},
        }
        _write_skill_file(skill_dir, frontmatter, body)
        logger.info("created skill %r at %s", name, skill_dir)
        return self.get(name)

    def update(
        self,
        name: str,
        body: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Skill:
        """Rewrite a skill's body and/or description, preserving other frontmatter."""
        if body is None and description is None:
            raise ValueError("update needs a new body and/or description")
        skill = self.get(name)
        if skill is None:
            raise ValueError(f"no skill named {name!r}")
        frontmatter = dict(skill.frontmatter)
        if description is not None:
            error = validate_description(description)
            if error:
                raise ValueError(error)
            frontmatter["description"] = _plain(description)
        _write_skill_file(skill.path, frontmatter, skill.body if body is None else body)
        return self.get(name)

    def _rewrite(self, skill: Skill, state: str, category: Optional[str] = None) -> None:
        """Persist a lifecycle state (and optional remembered category) in metadata."""
        frontmatter = dict(skill.frontmatter)
        meta = dict(_metadata(frontmatter))
        meta["state"] = state
        if category:
            meta["category"] = category
        else:
            meta.pop("category", None)
        frontmatter["metadata"] = meta
        _write_skill_file(skill.path, frontmatter, skill.body)

    def archive(self, name: str) -> Skill:
        """Move a skill to ``.archive/`` (the only retirement path -- no deletes)."""
        skill = self.get(name)
        if skill is None:
            raise ValueError(f"no skill named {name!r}")
        if skill.state == "archived":
            return skill
        destination = self.root / ARCHIVE_DIR / name
        if destination.exists():
            raise ValueError(f"the archive already contains a skill named {name!r}")
        self._rewrite(skill, "archived", category=skill.category)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(skill.path), str(destination))
        if skill.category:  # tidy a now-empty category dir (best-effort)
            try:
                skill.path.parent.rmdir()
            except OSError:
                pass
        logger.info("archived skill %r", name)
        return self.get(name)

    def unarchive(self, name: str) -> Skill:
        """Restore an archived skill to its original (possibly categorized) home."""
        skill = self.get(name)
        if skill is None:
            raise ValueError(f"no skill named {name!r}")
        if skill.state != "archived":
            return skill
        category = str(_metadata(skill.frontmatter).get("category") or "").strip() or None
        if category is not None and validate_name(category) is not None:
            # Frontmatter is attacker-influenceable; never let an invalid
            # category become a path segment (e.g. "../.." traversal).
            logger.warning("ignoring invalid category %r on archived skill %r", category, name)
            category = None
        destination = self.root / category / name if category else self.root / name
        if destination.exists():
            raise ValueError(f"a folder already exists at {destination}")
        self._rewrite(skill, "active")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(skill.path), str(destination))
        logger.info("unarchived skill %r", name)
        return self.get(name)

    def _set_state(self, name: str, state: str) -> Skill:
        skill = self.get(name)
        if skill is None:
            raise ValueError(f"no skill named {name!r}")
        if skill.state == "archived":
            raise ValueError(f"skill {name!r} is archived; unarchive it first")
        if skill.state != state:
            self._rewrite(skill, state)
        return self.get(name)

    def pin(self, name: str) -> Skill:
        """Pin a skill (always surfaced; survives rediscovery via metadata.state)."""
        return self._set_state(name, "pinned")

    def unpin(self, name: str) -> Skill:
        return self._set_state(name, "active")


# --------------------------------------------------------------------------
# Process-wide singleton
# --------------------------------------------------------------------------
_store: Optional[SkillStore] = None


def default_skills_root() -> Path:
    from openpup.config import get_settings

    return get_settings().state_dir / "skills"


def get_skill_store() -> SkillStore:
    """Shared store so the prompt loader + skill tools see the same skills."""
    global _store
    if _store is None:
        _store = SkillStore()
    return _store
