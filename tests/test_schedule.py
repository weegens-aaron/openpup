"""Tests for the enhanced scheduler + agent scheduling tools."""

import pytest

from openpup import access, schedule_tools
from openpup.heartbeat import scheduler as sched_mod
from openpup.heartbeat.scheduler import Routine, Scheduler, make_routine


# ---- scheduler core ------------------------------------------------------
def test_make_routine_delay_sets_at():
    r = make_routine(message="hi", delay_seconds=60, deliver="telegram:1", now=1000.0)
    assert r.at == 1060.0
    assert r.is_one_shot


def test_make_routine_at_iso():
    r = make_routine(prompt="x", at_iso="2030-01-01T09:00", deliver="telegram:1")
    assert r.at is not None and r.is_one_shot


def test_one_shot_is_due_and_pruned(tmp_path):
    s = Scheduler(path=tmp_path / "r.json")
    s.add(make_routine(name="remind", message="hey", delay_seconds=10, deliver="t:1", now=0.0))
    assert s.due(now=5) == []  # not yet
    fired = s.due(now=20)
    assert [r.name for r in fired] == ["remind"]
    # one-shot pruned after firing
    assert s.routines == []


def test_recurring_not_pruned(tmp_path):
    s = Scheduler(path=tmp_path / "r.json")
    s.add(Routine(name="cron", prompt="p", deliver="t:1", every=100))
    s.due(now=1000)
    assert len(s.routines) == 1  # recurring stays


def test_describe_when():
    assert "every 60s" in Routine(name="a", every=60).describe_when()
    assert "daily 08:00" in Routine(name="b", daily="08:00").describe_when()


def test_next_run_every_uses_last_run():
    now = 10_000.0
    r = Routine(name="a", every=600, last_run=now - 200)
    assert r.next_run(now) == now + 400  # 600 - 200 left
    assert r.describe_next(now) == "in 6m 40s"


def test_next_run_recurring_never_run_is_due_now():
    now = 10_000.0
    r = Routine(name="a", every=600, last_run=0)
    assert r.next_run(now) == now
    assert r.describe_next(now) == "due now"


def test_next_run_one_shot_is_the_at_time():
    now = 10_000.0
    r = Routine(name="a", at=now + 7200)
    assert r.next_run(now) == now + 7200
    assert r.describe_next(now) == "in 2h"


def test_next_run_disabled_is_none():
    r = Routine(name="a", every=600, enabled=False)
    assert r.next_run(123.0) is None
    assert r.describe_next(123.0) == "never"


def test_describe_last():
    assert Routine(name="a", every=60).describe_last() == "never"
    fired = Routine(name="a", every=60, last_run=1_700_000_000.0)
    assert fired.describe_last() != "never" and "T" in fired.describe_last()


# ---- agent tools ---------------------------------------------------------
class FakeAgent:
    def __init__(self):
        self.tools = {}

    def tool(self, func):
        self.tools[func.__name__] = func
        return func


@pytest.fixture
def temp_sched(tmp_path, monkeypatch):
    s = Scheduler(path=tmp_path / "routines.json")
    monkeypatch.setattr(sched_mod, "get_scheduler", lambda: s)
    access.set_current_role(access.OWNER)
    yield s
    access.set_current_role(access.ALLOWED)


@pytest.mark.asyncio
async def test_schedule_creates_reminder(temp_sched):
    agent = FakeAgent()
    schedule_tools.register_schedule(agent)
    out = await agent.tools["openpup_schedule"](
        None, message="take a break", delay_seconds=120, deliver="telegram:1"
    )
    assert out.ok is True
    assert out.deliver == "telegram:1"
    assert len(temp_sched.routines) == 1
    assert temp_sched.routines[0].message == "take a break"


@pytest.mark.asyncio
async def test_schedule_requires_message_or_prompt(temp_sched):
    agent = FakeAgent()
    schedule_tools.register_schedule(agent)
    out = await agent.tools["openpup_schedule"](None, delay_seconds=60, deliver="t:1")
    assert out.ok is False
    assert "message" in out.error.lower()


@pytest.mark.asyncio
async def test_schedule_requires_exactly_one_timing(temp_sched):
    agent = FakeAgent()
    schedule_tools.register_schedule(agent)
    out = await agent.tools["openpup_schedule"](
        None, message="x", delay_seconds=60, daily="08:00", deliver="t:1"
    )
    assert out.ok is False
    assert "exactly one timing" in out.error.lower()


@pytest.mark.asyncio
async def test_schedule_blocked_for_non_owner(temp_sched):
    access.set_current_role(access.ALLOWED)
    agent = FakeAgent()
    schedule_tools.register_schedule(agent)
    out = await agent.tools["openpup_schedule"](None, message="x", delay_seconds=60, deliver="t:1")
    assert out.ok is False
    assert "owner" in out.error.lower()


@pytest.mark.asyncio
async def test_list_and_cancel(temp_sched):
    agent = FakeAgent()
    schedule_tools.register_schedule(agent)
    schedule_tools.register_list_schedules(agent)
    schedule_tools.register_cancel_schedule(agent)

    await agent.tools["openpup_schedule"](
        None, message="m", every_seconds=300, deliver="telegram:1", name="ping"
    )
    listed = await agent.tools["openpup_list_schedules"](None)
    assert listed.count == 1
    assert listed.jobs[0].kind == "reminder"
    assert listed.jobs[0].name == "ping"

    cancelled = await agent.tools["openpup_cancel_schedule"](None, "ping")
    assert cancelled.ok is True
    assert temp_sched.routines == []


@pytest.mark.asyncio
async def test_list_exposes_prompt_for_merge(temp_sched):
    """list_schedules must surface the prompt so topics can be merged in."""
    agent = FakeAgent()
    schedule_tools.register_schedule(agent)
    schedule_tools.register_list_schedules(agent)

    await agent.tools["openpup_schedule"](
        None,
        prompt="check email for: invoices",
        every_seconds=1800,
        deliver="telegram:1",
        name="email-watch",
    )
    listed = await agent.tools["openpup_list_schedules"](None)
    assert listed.jobs[0].kind == "task"
    assert listed.jobs[0].prompt == "check email for: invoices"


@pytest.mark.asyncio
async def test_list_exposes_next_and_last_run(temp_sched):
    """The pup must be able to answer 'when does it next fire' precisely."""
    agent = FakeAgent()
    schedule_tools.register_schedule(agent)
    schedule_tools.register_list_schedules(agent)

    await agent.tools["openpup_schedule"](
        None, prompt="check email", every_seconds=600, deliver="telegram:1", name="email-watch"
    )
    job = (await agent.tools["openpup_list_schedules"](None)).jobs[0]
    # Never fired yet -> due on the next tick, and last_run reads 'never'.
    assert job.next_run == "due now"
    assert job.last_run == "never"
    assert job.next_run_at  # absolute ISO timestamp is populated


@pytest.mark.asyncio
async def test_reschedule_same_name_updates_in_place(temp_sched):
    """Re-scheduling with an existing name merges/overwrites, no duplicate."""
    agent = FakeAgent()
    schedule_tools.register_schedule(agent)

    await agent.tools["openpup_schedule"](
        None, prompt="check email for: invoices", every_seconds=1800,
        deliver="telegram:1", name="email-watch",
    )
    await agent.tools["openpup_schedule"](
        None, prompt="check email for: invoices, flights", every_seconds=1800,
        deliver="telegram:1", name="email-watch",
    )
    assert len(temp_sched.routines) == 1
    assert temp_sched.routines[0].prompt == "check email for: invoices, flights"


def test_advertise():
    assert set(schedule_tools.advertise_tools()) == {
        "openpup_schedule",
        "openpup_list_schedules",
        "openpup_cancel_schedule",
    }
