"""Curator-lite — low-frequency maintenance of the agent-created skill shelf.

Ported in spirit from hermes-agent's curator. Each eligible run does a cheap
mechanical pass (stamp ``metadata.created_at``, archive long-idle skills) and,
when there are enough skills to be worth a look, ONE cheap LLM review prompt
(the same mechanism reflect.py uses) that may suggest a single maintenance
action. Runs are gated by a JSON state file so the curator fires roughly
weekly, not every heartbeat tick.

Strict invariants (hermes, verbatim):

* Only ever touches AGENT-created skills (``skill.created_by == "agent"``).
  User skills are sacred.
* Never deletes — archive only (recoverable).
* Pinned skills bypass ALL auto-transitions.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openpup import memory
from openpup.agent_host import AgentHost
from openpup.config import Settings
from openpup.skills import get_skill_store
from openpup.skills.store import Skill, SkillStore, update_metadata

logger = logging.getLogger("openpup.curator")

STATE_FILE = "curator_state.json"
MEMORY_ROOM = "curator"

_REVIEW_PROMPT = """You are {name}'s skill curator. These are the skills the \
agent created for itself (name: description; [stale] = unused for a while):
---
{listing}
---

Suggest AT MOST ONE maintenance action, as a single JSON object on one line:
  {{"action": "archive", "skill": "<name>", "reason": "..."}}
  {{"action": "update_description", "skill": "<name>", "description": "..."}}
  {{"action": "suggest", "note": "<e.g. consolidate two near-duplicates>"}}

Only suggest something genuinely useful. If the collection is fine as-is, \
reply with exactly: [NOTHING]."""


# --------------------------------------------------------------------------
# State file (JSON in the state dir, scheduler-style: tolerate corruption)
# --------------------------------------------------------------------------
def _state_path(settings: Settings) -> Path:
    return settings.state_dir / STATE_FILE


def _load_state(settings: Settings) -> Dict[str, Any]:
    path = _state_path(settings)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            logger.exception("Failed to load curator state from %s", path)
    return {"last_run": 0.0, "run_count": 0}


def _save_state(settings: Settings, state: Dict[str, Any]) -> None:
    path = _state_path(settings)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("Failed to save curator state to %s", path)


# --------------------------------------------------------------------------
# Frontmatter metadata (curator-side; the store stays read-only to us)
# --------------------------------------------------------------------------
def _meta(skill: Skill) -> Dict[str, Any]:
    meta = skill.frontmatter.get("metadata")
    return dict(meta) if isinstance(meta, dict) else {}


def _parse_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def _mtime_date(skill: Skill) -> date:
    try:
        return datetime.fromtimestamp(skill.skill_file.stat().st_mtime).date()
    except OSError:
        return date.today()


# --------------------------------------------------------------------------
# Pass 1: mechanical (no LLM)
# --------------------------------------------------------------------------
def mechanical_pass(store: SkillStore, settings: Settings) -> List[Tuple[Skill, int]]:
    """Stamp ``created_at``, archive long-idle agent skills; return stale ones.

    Last activity is ``metadata.last_used`` (stamped by whoever loads the
    skill) falling back to ``metadata.created_at`` (stamped here, from file
    mtime when missing). Returns ``[(skill, idle_days), ...]`` for skills past
    the stale threshold but not yet archive-worthy — flagged for review only.
    """
    today = date.today()
    stale: List[Tuple[Skill, int]] = []
    for skill in store.list():
        if skill.created_by != "agent":
            continue  # user skills are sacred
        meta = _meta(skill)
        created = _parse_date(meta.get("created_at"))
        if created is None:
            created = _mtime_date(skill)
            # Validating + non-raising: a weird SKILL.md can't crash the pass.
            update_metadata(skill.skill_file, {"created_at": created.isoformat()})
        if skill.state == "pinned":
            continue  # pinned bypasses ALL auto-transitions
        last_used = _parse_date(meta.get("last_used")) or created
        idle_days = (today - last_used).days
        if idle_days >= settings.curator_archive_after_days:
            try:
                store.archive(skill.name)
                memory.remember(
                    f"[curator] archived stale skill {skill.name} — unarchive with "
                    "openpup_skill if needed",
                    room=MEMORY_ROOM,
                )
                logger.info("Curator archived stale skill %r (idle %dd)", skill.name, idle_days)
            except ValueError:
                logger.warning("Curator could not archive %r", skill.name, exc_info=True)
        elif idle_days >= settings.curator_stale_after_days:
            stale.append((skill, idle_days))
    return stale


# --------------------------------------------------------------------------
# Pass 2: optional LLM review (one cheap prompt, reflect.py-style)
# --------------------------------------------------------------------------
def _parse_suggestion(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Defensively extract one JSON object from the model's reply, or None."""
    text = (text or "").strip()
    if not text or "[NOTHING]" in text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _apply_suggestion(store: SkillStore, suggestion: Dict[str, Any], eligible: set) -> None:
    """Apply archive / description updates only; anything else becomes a note."""
    action = str(suggestion.get("action") or "").strip().lower()
    name = str(suggestion.get("skill") or "").strip()
    if action == "archive" and name in eligible:
        try:
            store.archive(name)
            memory.remember(
                f"[curator] review archived skill {name} — unarchive with openpup_skill if needed",
                room=MEMORY_ROOM,
            )
        except ValueError:
            logger.warning("Curator review archive of %r failed", name, exc_info=True)
        return
    if action == "update_description" and name in eligible:
        description = str(suggestion.get("description") or "").strip()
        if not description:
            return
        try:
            store.update(name, description=description)
            memory.remember(
                f"[curator] review improved the description of skill {name}",
                room=MEMORY_ROOM,
            )
        except ValueError:
            logger.warning("Curator review update of %r failed", name, exc_info=True)
        return
    # Anything else (consolidations, unknown actions, off-list skills) is
    # recorded as a suggestion for the agent/owner — never auto-applied.
    memory.remember(f"[curator] suggestion: {json.dumps(suggestion)}", room=MEMORY_ROOM)


async def review_pass(
    host: AgentHost,
    settings: Settings,
    store: SkillStore,
    stale: List[Tuple[Skill, int]],
) -> None:
    """Run ONE cheap review prompt over the active agent-created skills."""
    skills = [s for s in store.list() if s.created_by == "agent" and s.state == "active"]
    if len(skills) < 2:
        return
    stale_names = {skill.name for skill, _ in stale}
    listing = "\n".join(
        f"- {s.name}: {s.description}" + (" [stale]" if s.name in stale_names else "")
        for s in skills
    )
    prompt = _REVIEW_PROMPT.format(name=settings.name, listing=listing)
    try:
        text = await host.run(
            prompt,
            conversation="__curator__",
            model=settings.reflection_model,
            keep_history=False,
        )
    except Exception:
        logger.exception("Curator review run failed")
        return
    suggestion = _parse_suggestion(text)
    if suggestion is None:
        logger.debug("Curator review suggested nothing (or was unparseable)")
        return
    _apply_suggestion(store, suggestion, {s.name for s in skills})


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------
async def curate(host: AgentHost, settings: Settings, store: Optional[SkillStore] = None) -> None:
    """Run one full curation cycle: mechanical pass, then the LLM review."""
    store = store or get_skill_store()
    stale = mechanical_pass(store, settings)
    await review_pass(host, settings, store, stale)


async def maybe_curate(host: AgentHost, settings: Settings) -> None:
    """Interval-gated entry point for the heartbeat: at most one run per interval."""
    state = _load_state(settings)
    now = time.time()
    try:
        last_run = float(state.get("last_run") or 0.0)
    except (TypeError, ValueError):
        last_run = 0.0
    if now - last_run <= settings.curator_interval_hours * 3600:
        return
    await curate(host, settings)
    state["last_run"] = now
    state["run_count"] = int(state.get("run_count") or 0) + 1
    _save_state(settings, state)
