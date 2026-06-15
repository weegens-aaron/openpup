"""Tests for the read-only scheduled prompts/notifications viewer."""

from rich.console import Console

import openpup.tui.schedules as sched_view
from openpup.heartbeat.scheduler import Routine


def _patch(monkeypatch, routines):
    monkeypatch.setattr(sched_view, "_load_routines", lambda: routines)


def test_render_empty(monkeypatch):
    _patch(monkeypatch, [])
    out = Console(record=True, width=120)
    assert sched_view.render_schedules(out) == 0
    assert "No scheduled prompts or notifications" in out.export_text()


def test_render_splits_reminders_and_tasks(monkeypatch):
    _patch(
        monkeypatch,
        [
            Routine(name="standup", message="Time for standup!", every=86400),
            Routine(name="email-watch", prompt="check unread email and summarize", every=1800),
        ],
    )
    out = Console(record=True, width=200)
    count = sched_view.render_schedules(out, full=True)
    text = out.export_text()
    assert count == 2
    assert "Notifications (reminders)" in text
    assert "Scheduled prompts (tasks)" in text
    assert "Time for standup!" in text
    assert "check unread email and summarize" in text
    assert "1 notification(s), 1 scheduled prompt(s)" in text


def test_truncation_unless_full(monkeypatch):
    long_prompt = "x" * 200
    _patch(monkeypatch, [Routine(name="big", prompt=long_prompt, every=600)])

    short = Console(record=True, width=300)
    sched_view.render_schedules(short, full=False)
    assert "…" in short.export_text()  # truncated

    fullc = Console(record=True, width=400)
    sched_view.render_schedules(fullc, full=True)
    assert "…" not in fullc.export_text()  # full content shown
