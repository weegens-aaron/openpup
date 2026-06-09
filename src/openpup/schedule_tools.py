"""Agent-facing scheduling tools.

Let the pup set reminders and cron-style jobs for itself, bound to the shared
scheduler the heartbeat drives. Owner-only (only the owner may schedule things);
delivery defaults to the owner and resolves friendly contact names.

Tools:
* ``openpup_schedule``      — create a reminder (message) or task (prompt)
* ``openpup_list_schedules``— list pending jobs
* ``openpup_cancel_schedule``— cancel a job by name
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import RunContext


def _is_owner() -> bool:
    try:
        from openpup.access import current_is_owner

        return current_is_owner()
    except Exception:
        return True


class ScheduleResult(BaseModel):
    ok: bool
    name: str = ""
    when: str = ""
    deliver: str = ""
    error: Optional[str] = None


class ScheduledJob(BaseModel):
    name: str
    kind: str  # "reminder" | "task"
    when: str
    deliver: str
    enabled: bool


class ScheduleList(BaseModel):
    jobs: List[ScheduledJob] = Field(default_factory=list)
    count: int = 0


class CancelResult(BaseModel):
    ok: bool
    name: str
    error: Optional[str] = None


def register_schedule(agent: Any) -> None:
    @agent.tool
    async def openpup_schedule(
        context: RunContext,
        message: str = "",
        prompt: str = "",
        deliver: str = "",
        delay_seconds: Optional[int] = None,
        at: Optional[str] = None,
        every_seconds: Optional[int] = None,
        daily: Optional[str] = None,
        name: str = "",
    ) -> ScheduleResult:
        """Schedule a reminder or a recurring task. Owner-only.

        Provide EITHER:
        * ``message`` — plain text delivered verbatim at the time (a reminder), OR
        * ``prompt``  — a task you run yourself, whose output is delivered.

        And EXACTLY ONE timing:
        * ``delay_seconds`` — fire once, this many seconds from now
          (e.g. "remind me in 2 hours" -> 7200).
        * ``at`` — fire once, at an ISO datetime (e.g. "2026-06-09T09:00").
        * ``every_seconds`` — recurring, this many seconds apart.
        * ``daily`` — recurring at a wall-clock "HH:MM" (local time).

        ``deliver`` is a ``platform:channel`` address or a known contact name;
        defaults to the owner. ``name`` is optional (auto-generated if omitted).
        """
        from openpup.config import get_settings
        from openpup.directory import get_directory
        from openpup.heartbeat.scheduler import get_scheduler, make_routine

        if not _is_owner():
            return ScheduleResult(ok=False, error="Only the owner can schedule jobs.")
        if not (message or prompt):
            return ScheduleResult(ok=False, error="Provide a 'message' or a 'prompt'.")
        timings = [t for t in (delay_seconds, at, every_seconds, daily) if t not in (None, "")]
        if len(timings) != 1:
            return ScheduleResult(
                ok=False,
                error="Provide exactly one timing: delay_seconds, at, every_seconds, or daily.",
            )

        # Resolve the delivery target (default: owner).
        target = deliver or get_settings().owner_address or ""
        if target:
            resolved = get_directory().resolve(target)
            if resolved:
                target = resolved
        if not target:
            return ScheduleResult(
                ok=False, error="No delivery target and no owner address configured."
            )

        try:
            routine = make_routine(
                name=name or None,
                message=message,
                prompt=prompt,
                deliver=target,
                delay_seconds=delay_seconds,
                at_iso=at,
                every_seconds=every_seconds,
                daily=daily,
            )
        except Exception as exc:  # noqa: BLE001
            return ScheduleResult(ok=False, error=f"could not build job: {exc}")

        get_scheduler().add(routine)
        return ScheduleResult(
            ok=True, name=routine.name, when=routine.describe_when(), deliver=target
        )


def register_list_schedules(agent: Any) -> None:
    @agent.tool
    async def openpup_list_schedules(context: RunContext) -> ScheduleList:
        """List pending scheduled reminders and tasks."""
        from openpup.heartbeat.scheduler import get_scheduler

        sched = get_scheduler()
        sched.reload()
        jobs = [
            ScheduledJob(
                name=r.name,
                kind="reminder" if r.message else "task",
                when=r.describe_when(),
                deliver=r.deliver,
                enabled=r.enabled,
            )
            for r in sched.routines
        ]
        return ScheduleList(jobs=jobs, count=len(jobs))


def register_cancel_schedule(agent: Any) -> None:
    @agent.tool
    async def openpup_cancel_schedule(context: RunContext, name: str) -> CancelResult:
        """Cancel a scheduled job by name. Owner-only."""
        from openpup.heartbeat.scheduler import get_scheduler

        if not _is_owner():
            return CancelResult(ok=False, name=name, error="Only the owner can cancel jobs.")
        ok = get_scheduler().remove(name)
        return CancelResult(ok=ok, name=name, error=None if ok else "no such job")


_TOOL_NAMES = (
    "openpup_schedule",
    "openpup_list_schedules",
    "openpup_cancel_schedule",
)


def register_tools_callback() -> List[dict]:
    return [
        {"name": "openpup_schedule", "register_func": register_schedule},
        {"name": "openpup_list_schedules", "register_func": register_list_schedules},
        {"name": "openpup_cancel_schedule", "register_func": register_cancel_schedule},
    ]


def advertise_tools(agent_name: Optional[str] = None) -> List[str]:
    return list(_TOOL_NAMES)
