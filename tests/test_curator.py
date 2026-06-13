"""Curator-lite: interval gating, agent-only archives, defensive LLM review."""

import json
import time
from datetime import date, timedelta

import pytest

from openpup.config import Settings
from openpup.heartbeat import curator
from openpup.skills.store import SkillStore, parse_frontmatter, render_frontmatter

DESC = "Does a thing well. Use when testing the curator."


class StubHost:
    """Fake AgentHost whose run() returns a canned reply (and counts calls)."""

    def __init__(self, response=""):
        self.response = response
        self.calls = []

    async def run(self, prompt, **kwargs):
        self.calls.append(prompt)
        return self.response


@pytest.fixture
def settings(tmp_path, monkeypatch):
    s = Settings(_env_file=None, PUPPY_KENNEL_ROOT=str(tmp_path / "kennel"))
    monkeypatch.setattr(type(s), "state_dir", property(lambda self: tmp_path))
    return s


@pytest.fixture
def store(tmp_path):
    return SkillStore(root=tmp_path / "skills")


@pytest.fixture
def notes(monkeypatch):
    """Capture memory.remember() calls instead of touching the kennel."""
    captured = []
    monkeypatch.setattr(
        curator.memory, "remember", lambda content, **kw: captured.append(content) or True
    )
    return captured


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _set_meta(skill_dir, **updates):
    """Hand-edit a skill's frontmatter metadata (simulating prior activity)."""
    skill_file = skill_dir / "SKILL.md"
    frontmatter, body = parse_frontmatter(skill_file.read_text())
    meta = dict(frontmatter.get("metadata") or {})
    meta.update(updates)
    frontmatter["metadata"] = meta
    skill_file.write_text(render_frontmatter(frontmatter) + "\n\n" + body)


def _write_user_skill(root, name, last_used=None):
    """A user-installed skill (no created_by: openpup)."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    meta = f"metadata:\n  last_used: {last_used}\n" if last_used else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {DESC}\n{meta}---\n\nBody\n"
    )
    return skill_dir


# ---- config defaults --------------------------------------------------------
def test_config_defaults():
    s = Settings(_env_file=None)
    assert s.curator_interval_hours == 168
    assert s.curator_stale_after_days == 30
    assert s.curator_archive_after_days == 90
    assert "curator" not in s.behaviors  # strictly opt-in


# ---- interval gating ---------------------------------------------------------
async def test_interval_gating(settings, monkeypatch, tmp_path):
    runs = []

    async def fake_curate(host, s, store=None):
        runs.append(time.time())

    monkeypatch.setattr(curator, "curate", fake_curate)
    state_path = tmp_path / "curator_state.json"

    await curator.maybe_curate(None, settings)  # no prior state -> runs
    assert len(runs) == 1
    state = json.loads(state_path.read_text())
    assert state["last_run"] > 0
    assert state["run_count"] == 1

    await curator.maybe_curate(None, settings)  # within the interval -> gated
    assert len(runs) == 1

    stale_run = time.time() - settings.curator_interval_hours * 3600 - 10
    state_path.write_text(json.dumps({"last_run": stale_run, "run_count": 1}))
    await curator.maybe_curate(None, settings)  # interval elapsed -> runs again
    assert len(runs) == 2
    assert json.loads(state_path.read_text())["run_count"] == 2


async def test_corrupt_state_file_still_runs(settings, monkeypatch, tmp_path):
    runs = []

    async def fake_curate(host, s, store=None):
        runs.append(1)

    monkeypatch.setattr(curator, "curate", fake_curate)
    (tmp_path / "curator_state.json").write_text("{not json")
    await curator.maybe_curate(None, settings)
    assert runs == [1]


# ---- mechanical pass -----------------------------------------------------------
def test_archives_idle_agent_skills_only(settings, store, notes):
    store.create("old-agent", DESC)
    _set_meta(store.root / "old-agent", last_used=_days_ago(100))
    _write_user_skill(store.root, "old-user", last_used=_days_ago(100))
    store.create("pinned-agent", DESC)
    _set_meta(store.root / "pinned-agent", last_used=_days_ago(100))
    store.pin("pinned-agent")

    curator.mechanical_pass(store, settings)

    # Agent-created + idle 100d -> archived (never deleted: folder moved).
    assert (store.root / ".archive" / "old-agent" / "SKILL.md").exists()
    assert not (store.root / "old-agent").exists()
    assert store.get("old-agent").state == "archived"
    # User skills are sacred; pinned skills bypass all auto-transitions.
    assert store.get("old-user").state == "active"
    assert store.get("pinned-agent").state == "pinned"
    assert (store.root / "pinned-agent").exists()
    assert any("archived stale skill old-agent" in n for n in notes)


def test_stale_skills_flagged_not_archived(settings, store, notes):
    store.create("dusty", DESC)
    _set_meta(store.root / "dusty", last_used=_days_ago(40))  # stale, not archive-worthy

    stale = curator.mechanical_pass(store, settings)

    assert [(s.name, days) for s, days in stale] == [("dusty", 40)]
    assert store.get("dusty").state == "active"
    assert notes == []


def test_stamps_created_at_from_mtime(settings, store):
    store.create("fresh", DESC)  # store.create does not stamp created_at
    curator.mechanical_pass(store, settings)
    meta = store.get("fresh").frontmatter["metadata"]
    assert meta["created_at"] == date.today().isoformat()
    assert store.get("fresh").state == "active"  # fresh skill untouched otherwise


# ---- LLM review pass --------------------------------------------------------------
async def test_review_applies_valid_archive(settings, store, notes):
    store.create("skill-a", DESC)
    store.create("skill-b", DESC)
    host = StubHost(response='Sure! {"action": "archive", "skill": "skill-a", "reason": "dupe"}')

    await curator.review_pass(host, settings, store, stale=[])

    assert len(host.calls) == 1
    assert store.get("skill-a").state == "archived"
    assert (store.root / ".archive" / "skill-a" / "SKILL.md").exists()  # never deleted
    assert store.get("skill-b").state == "active"


async def test_review_applies_description_update(settings, store, notes):
    store.create("skill-a", DESC)
    store.create("skill-b", DESC)
    new_desc = "Sharper words. Use when describing better."
    host = StubHost(
        response=json.dumps(
            {"action": "update_description", "skill": "skill-b", "description": new_desc}
        )
    )
    await curator.review_pass(host, settings, store, stale=[])
    assert store.get("skill-b").description == new_desc


@pytest.mark.parametrize("reply", ["total garbage", "{broken json", "[1, 2]", "", "[NOTHING]"])
async def test_review_ignores_malformed_output(settings, store, notes, reply):
    store.create("skill-a", DESC)
    store.create("skill-b", DESC)
    await curator.review_pass(StubHost(response=reply), settings, store, stale=[])
    assert store.get("skill-a").state == "active"
    assert store.get("skill-b").state == "active"
    assert notes == []


async def test_review_never_archives_user_or_pinned(settings, store, notes):
    store.create("skill-a", DESC)
    store.create("skill-b", DESC)
    _write_user_skill(store.root, "user-skill")
    host = StubHost(response='{"action": "archive", "skill": "user-skill"}')

    await curator.review_pass(host, settings, store, stale=[])

    # Off-list target -> demoted to a memory suggestion, never applied.
    assert store.get("user-skill").state == "active"
    assert any("suggestion" in n for n in notes)


async def test_review_skipped_below_two_skills(settings, store):
    store.create("loner", DESC)
    host = StubHost(response='{"action": "archive", "skill": "loner"}')
    await curator.review_pass(host, settings, store, stale=[])
    assert host.calls == []  # not even one prompt spent
    assert store.get("loner").state == "active"


async def test_review_other_actions_become_notes(settings, store, notes):
    store.create("skill-a", DESC)
    store.create("skill-b", DESC)
    host = StubHost(response='{"action": "consolidate", "skills": ["skill-a", "skill-b"]}')
    await curator.review_pass(host, settings, store, stale=[])
    assert store.get("skill-a").state == "active"
    assert store.get("skill-b").state == "active"
    assert any("consolidate" in n for n in notes)


# ---- full cycle ---------------------------------------------------------------------
async def test_curate_runs_both_passes(settings, store, notes):
    store.create("ancient", DESC)
    _set_meta(store.root / "ancient", last_used=_days_ago(365))
    store.create("skill-a", DESC)
    store.create("skill-b", DESC)
    host = StubHost(response="[NOTHING]")

    await curator.curate(host, settings, store=store)

    assert store.get("ancient").state == "archived"  # mechanical pass ran
    assert len(host.calls) == 1  # review pass ran (2 active agent skills left)
    assert "skill-a" in host.calls[0] and "ancient" not in host.calls[0]
