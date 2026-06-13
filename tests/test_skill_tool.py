"""openpup_skill tool: every action, owner gating, error surfacing."""

import pytest

from openpup import access
from openpup.skills import store as store_mod
from openpup.skills import tool as tool_mod
from openpup.skills.loader import SKILL_TOOL_NAME
from openpup.skills.store import SkillStore

DESC = "Does a thing well. Use when testing the skill tool."
BODY = "# Steps\n1. Do the thing.\n2. Mind the gotcha."

MUTATIONS = ["create", "update", "archive", "unarchive", "pin", "unpin"]


@pytest.fixture(autouse=True)
def _as_owner():
    """Default tests to owner privileges; individual tests can override."""
    access.set_current_role(access.OWNER)
    yield
    access.set_current_role(access.ALLOWED)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Point the process-wide skill store at a tmp-path root."""
    fresh = SkillStore(root=tmp_path / "skills")
    monkeypatch.setattr(store_mod, "_store", fresh)
    return fresh


class FakeAgent:
    """Captures @agent.tool-decorated functions by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, func):
        self.tools[func.__name__] = func
        return func


@pytest.fixture
def skill_tool():
    agent = FakeAgent()
    tool_mod.register_skill_tool(agent)
    return agent.tools[SKILL_TOOL_NAME]


# ---- registration ------------------------------------------------------------
def test_tool_name_matches_loader_constant(skill_tool):
    assert skill_tool.__name__ == SKILL_TOOL_NAME == "openpup_skill"


# ---- list --------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_empty(skill_tool):
    result = await skill_tool(None, "list")
    assert result.ok is True
    assert result.skills == []
    assert "0 skill(s)" in result.message


@pytest.mark.asyncio
async def test_list_shows_active_and_pinned_not_archived(skill_tool, store):
    store.create("plain-skill", DESC, body=BODY)
    store.create("pinned-skill", DESC, body=BODY)
    store.create("gone-skill", DESC, body=BODY)
    store.pin("pinned-skill")
    store.archive("gone-skill")
    result = await skill_tool(None, "list")
    by_name = {s.name: s for s in result.skills}
    assert set(by_name) == {"plain-skill", "pinned-skill"}
    assert by_name["pinned-skill"].state == "pinned"
    assert by_name["plain-skill"].created_by == "agent"
    assert by_name["plain-skill"].description == DESC


@pytest.mark.asyncio
async def test_list_query_filters_name_and_description(skill_tool, store):
    store.create("deploy-docs", "Publishes the docs site. Use when releasing.", body=BODY)
    store.create("fix-ci", "Repairs flaky pipelines. Use on red builds.", body=BODY)
    by_name = await skill_tool(None, "list", query="deploy")
    assert [s.name for s in by_name.skills] == ["deploy-docs"]
    by_desc = await skill_tool(None, "list", query="FLAKY")  # case-insensitive
    assert [s.name for s in by_desc.skills] == ["fix-ci"]
    none = await skill_tool(None, "list", query="zebra")
    assert none.ok is True
    assert none.skills == []


# ---- load --------------------------------------------------------------------
@pytest.mark.asyncio
async def test_load_returns_body_and_path(skill_tool, store):
    store.create("load-me", DESC, body=BODY)
    result = await skill_tool(None, "load", name="load-me")
    assert result.ok is True
    assert result.skill is not None
    assert "Mind the gotcha." in result.skill.body
    assert result.skill.path == str(store.root / "load-me")
    assert result.skill.path in result.message  # so the agent can read references/


@pytest.mark.asyncio
async def test_load_missing_or_archived(skill_tool, store):
    result = await skill_tool(None, "load", name="ghost")
    assert result.ok is False
    assert "ghost" in result.error
    store.create("retired", DESC, body=BODY)
    store.archive("retired")
    result = await skill_tool(None, "load", name="retired")
    assert result.ok is False


@pytest.mark.asyncio
async def test_load_requires_name(skill_tool):
    result = await skill_tool(None, "load")
    assert result.ok is False
    assert "name" in result.error


# ---- create ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_writes_skill(skill_tool, store):
    result = await skill_tool(
        None, "create", name="new-skill", description=DESC, body=BODY, category="ops"
    )
    assert result.ok is True
    assert result.skill.created_by == "agent"
    assert (store.root / "ops" / "new-skill" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_create_surfaces_store_value_errors(skill_tool, store):
    bad_name = await skill_tool(None, "create", name="Not Valid!", description=DESC, body=BODY)
    assert bad_name.ok is False
    assert "lowercase" in bad_name.error
    store.create("dupe", DESC, body=BODY)
    dupe = await skill_tool(None, "create", name="dupe", description=DESC, body=BODY)
    assert dupe.ok is False
    assert "already exists" in dupe.error


@pytest.mark.asyncio
async def test_create_requires_all_fields(skill_tool):
    result = await skill_tool(None, "create", name="half-baked", description=DESC)
    assert result.ok is False
    assert "body" in result.error


# ---- update ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_body_and_description(skill_tool, store):
    store.create("up-skill", DESC, body="old body")
    result = await skill_tool(
        None, "update", name="up-skill", body="new body", description="New desc. Use anew."
    )
    assert result.ok is True
    assert result.skill.description == "New desc. Use anew."
    assert "new body" in result.skill.body


@pytest.mark.asyncio
async def test_update_errors_surfaced(skill_tool, store):
    ghost = await skill_tool(None, "update", name="ghost", body="x")
    assert ghost.ok is False
    assert "ghost" in ghost.error
    store.create("noop", DESC, body=BODY)
    nothing = await skill_tool(None, "update", name="noop")
    assert nothing.ok is False
    assert "body" in nothing.error


# ---- lifecycle ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_archive_unarchive_cycle(skill_tool, store):
    store.create("cycle", DESC, body=BODY)
    archived = await skill_tool(None, "archive", name="cycle")
    assert archived.ok is True
    assert archived.skill.state == "archived"
    assert (store.root / ".archive" / "cycle").exists()
    restored = await skill_tool(None, "unarchive", name="cycle")
    assert restored.ok is True
    assert restored.skill.state == "active"


@pytest.mark.asyncio
async def test_pin_unpin(skill_tool, store):
    store.create("pinny", DESC, body=BODY)
    pinned = await skill_tool(None, "pin", name="pinny")
    assert pinned.ok is True
    assert pinned.skill.state == "pinned"
    unpinned = await skill_tool(None, "unpin", name="pinny")
    assert unpinned.skill.state == "active"


@pytest.mark.asyncio
async def test_lifecycle_value_errors_surfaced(skill_tool, store):
    store.create("frozen", DESC, body=BODY)
    store.archive("frozen")
    result = await skill_tool(None, "pin", name="frozen")
    assert result.ok is False
    assert "archived" in result.error


# ---- owner gating ------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("action", MUTATIONS)
async def test_mutations_blocked_for_non_owner(skill_tool, store, action):
    store.create("guarded", DESC, body=BODY)
    access.set_current_role(access.ALLOWED)  # non-owner
    result = await skill_tool(None, action, name="guarded", description=DESC, body=BODY)
    assert result.ok is False
    assert "owner" in result.error.lower()
    assert store.get("guarded").state == "active"  # untouched


@pytest.mark.asyncio
async def test_reads_open_to_non_owner(skill_tool, store):
    store.create("public-read", DESC, body=BODY)
    access.set_current_role(access.ALLOWED)
    listing = await skill_tool(None, "list")
    assert listing.ok is True
    loaded = await skill_tool(None, "load", name="public-read")
    assert loaded.ok is True


# ---- unknown action -----------------------------------------------------------
@pytest.mark.asyncio
async def test_unknown_action_never_raises(skill_tool):
    result = await skill_tool(None, "obliterate")
    assert result.ok is False
    assert "unknown action" in result.error
    assert "list" in result.error  # tells the model what IS valid
