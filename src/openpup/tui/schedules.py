"""Read-only viewer for scheduled prompts (tasks) and notifications (reminders).

Surfaced both from the CLI (``openpup routine list``) and the ``openpup config``
menu ("View scheduled prompts & notifications"). It splits the scheduler's
routines into the two things you actually think about:

* **Notifications** -- reminders: a fixed ``message`` delivered verbatim.
* **Scheduled prompts** -- tasks: a ``prompt`` the pup runs, whose output is
  delivered.

For each it shows the timing rule, when it next/last fired, where it's
delivered, and the actual content (truncated unless ``full``).
"""

from __future__ import annotations

import time
from typing import List

from rich.console import Console
from rich.table import Table

from openpup.heartbeat.scheduler import Routine, Scheduler, default_routines_path

console = Console()

_PREVIEW = 70


def _load_routines() -> List[Routine]:
    sched = Scheduler.load(default_routines_path())
    return sched.routines


def _truncate(text: str, full: bool) -> str:
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return "[dim](none)[/dim]"
    if full or len(text) <= _PREVIEW:
        return text
    return text[: _PREVIEW - 1] + "…"


def _table(title: str, routines: List[Routine], content_col: str, full: bool) -> Table:
    now = time.time()
    table = Table(title=title, title_style="bold", expand=True)
    table.add_column("name", style="cyan", no_wrap=True)
    table.add_column("when")
    table.add_column("next")
    table.add_column("last")
    table.add_column("deliver")
    table.add_column("on", justify="center")
    table.add_column(content_col, overflow="fold")
    for r in routines:
        content = r.message if content_col == "message" else r.prompt
        table.add_row(
            r.name,
            r.describe_when(),
            r.describe_next(now),
            r.describe_last(),
            r.deliver or "(owner)",
            "[green]yes[/green]" if r.enabled else "[red]no[/red]",
            _truncate(content, full),
        )
    return table


def render_schedules(out: Console | None = None, full: bool = False) -> int:
    """Print the schedules view. Returns the number of jobs shown."""
    out = out or console
    routines = _load_routines()
    if not routines:
        out.print("[dim]No scheduled prompts or notifications.[/dim]")
        return 0

    reminders = [r for r in routines if r.message]
    tasks = [r for r in routines if not r.message]

    if reminders:
        out.print(_table("Notifications (reminders)", reminders, "message", full))
    if tasks:
        out.print(_table("Scheduled prompts (tasks)", tasks, "prompt", full))
    out.print(
        f"[dim]{len(reminders)} notification(s), {len(tasks)} scheduled prompt(s). "
        "Manage with: openpup routine add | openpup routine rm <name>[/dim]"
    )
    return len(routines)


async def run_schedules_view() -> None:
    """Config-menu entry: render the schedules, then wait to go back."""
    from openpup.tui.select import arrow_select_async

    console.print()
    shown = render_schedules(full=True)
    console.print()
    if shown:
        await arrow_select_async("Scheduled prompts & notifications", ["Back"])
