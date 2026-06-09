"""The scheduler must fire on the fast loop, not only the slow heartbeat."""

import pytest

from openpup.agent_host import AgentHost
from openpup.config import Settings
from openpup.heartbeat import engine as engine_mod
from openpup.heartbeat.engine import Heartbeat
from openpup.heartbeat.scheduler import Scheduler
from openpup.messaging.registry import PlatformRegistry


def _heartbeat(behaviors="reflect,outreach,routines,inbound", tmp_path=None):
    s = Settings(_env_file=None, OPENPUP_HEARTBEAT_BEHAVIORS=behaviors)
    sched = Scheduler(path=(tmp_path / "sched.json") if tmp_path else None)
    return Heartbeat(AgentHost(), s, PlatformRegistry(), scheduler=sched)


@pytest.mark.asyncio
async def test_fast_tick_runs_routines_not_reflect(monkeypatch, tmp_path):
    calls = {"routines": 0, "reflect": 0}

    async def fake_routines(*a, **k):
        calls["routines"] += 1

    async def fake_reflect(*a, **k):
        calls["reflect"] += 1

    monkeypatch.setattr(engine_mod.routines, "run_due_routines", fake_routines)
    monkeypatch.setattr(engine_mod.reflect, "reflect", fake_reflect)

    hb = _heartbeat(tmp_path=tmp_path)
    await hb.fast_tick()

    # The fast loop fires the scheduler...
    assert calls["routines"] == 1
    # ...but does NOT run the expensive reflect behavior.
    assert calls["reflect"] == 0
    assert hb.fast_tick_count == 1


@pytest.mark.asyncio
async def test_fast_tick_skips_routines_when_disabled(monkeypatch, tmp_path):
    calls = {"routines": 0}

    async def fake_routines(*a, **k):
        calls["routines"] += 1

    monkeypatch.setattr(engine_mod.routines, "run_due_routines", fake_routines)
    hb = _heartbeat(behaviors="reflect,outreach", tmp_path=tmp_path)
    await hb.fast_tick()
    assert calls["routines"] == 0


@pytest.mark.asyncio
async def test_slow_tick_runs_reflect(monkeypatch, tmp_path):
    calls = {"reflect": 0, "outreach": 0}

    async def fake_reflect(*a, **k):
        calls["reflect"] += 1

    async def fake_outreach(*a, **k):
        calls["outreach"] += 1

    async def fake_routines(*a, **k):
        pass

    monkeypatch.setattr(engine_mod.reflect, "reflect", fake_reflect)
    monkeypatch.setattr(engine_mod.outreach, "maybe_reach_out", fake_outreach)
    monkeypatch.setattr(engine_mod.routines, "run_due_routines", fake_routines)

    hb = _heartbeat(tmp_path=tmp_path)
    await hb.tick()
    assert calls["reflect"] == 1
    assert calls["outreach"] == 1


def test_scheduler_interval_default():
    s = Settings(_env_file=None)
    assert s.scheduler_interval == 30
