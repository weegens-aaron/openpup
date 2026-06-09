"""OpenPup command-line interface (Typer).

Commands:
  openpup run                 start the always-on companion
  openpup status              show config + enabled platforms
  openpup say <addr> <text>   send a one-off message to a platform address
  openpup memory recall <q>   search the kennel
  openpup memory recent       show recent memories
  openpup routine add/list/rm manage scheduled routines
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from openpup.config import get_settings

app = typer.Typer(help="OpenPup - an always-on AI companion.", no_args_is_help=True)
memory_app = typer.Typer(help="Inspect OpenPup's memory (puppy_kennel).")
routine_app = typer.Typer(help="Manage scheduled routines.")
access_app = typer.Typer(help="Manage who can talk to OpenPup (owner + allowlists).")
app.add_typer(memory_app, name="memory")
app.add_typer(routine_app, name="routine")
app.add_typer(access_app, name="access")

console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


@app.command()
def run(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Start the always-on companion (blocks until Ctrl-C)."""
    _setup_logging(verbose)
    from openpup.runtime import OpenPup

    pup = OpenPup()

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, pup.request_stop)
            except NotImplementedError:  # pragma: no cover - windows
                pass
        await pup.run_forever()

    asyncio.run(_main())


@app.command()
def persona(
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Path to the .env to write."),
) -> None:
    """Edit your pup's identity (name, personality, proactivity -> SOUL.md)."""
    from pathlib import Path

    from openpup.tui.persona import run_persona_menu

    path = Path(env_file) if env_file else None
    asyncio.run(run_persona_menu(path))


@app.command()
def setup(
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Path to the .env to write."),
) -> None:
    """On-rails guided setup: get + validate credentials for each platform."""
    from pathlib import Path

    from openpup.setup import run_setup_wizard

    path = Path(env_file) if env_file else None
    asyncio.run(run_setup_wizard(path))


@app.command()
def config(
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Path to the .env to edit."),
) -> None:
    """Open the interactive TUI to configure everything (writes .env)."""
    from pathlib import Path

    from openpup.tui import run_config_menu

    path = Path(env_file) if env_file else None
    asyncio.run(run_config_menu(path))


@app.command()
def status() -> None:
    """Show configuration and which platforms are enabled."""
    s = get_settings()
    table = Table(title=f"{s.name} status")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Agent", s.agent)
    table.add_row("Model", s.model or "(code-puppy default)")
    table.add_row("Reflection model", s.reflection_model or "(same as agent)")
    table.add_row("Universal Constructor", "on" if s.universal_constructor else "off")
    table.add_row("Owner", s.owner_address or "(unset)")
    extra_owners = [a for a in s.owner_addresses if a != s.owner_address]
    if extra_owners:
        table.add_row("  also owner at", ", ".join(extra_owners))
    table.add_row("Send policy", f"{s.send_policy} ({s.send_rate_per_min}/min)")
    table.add_row("Kennel root", str(s.kennel_path))
    table.add_row("Heartbeat", "on" if s.heartbeat_enabled else "off")
    table.add_row("  interval", f"{s.heartbeat_interval}s +/-{s.heartbeat_jitter}s")
    table.add_row("  behaviors", ", ".join(s.behaviors))
    table.add_row("  quiet hours", s.quiet_hours or "(none)")
    enabled = [
        name
        for name, on in [
            ("discord", s.discord_enabled),
            ("telegram", s.telegram_enabled),
            ("whatsapp", s.whatsapp_enabled),
            ("email", s.email_enabled),
            ("sms", s.sms_enabled),
        ]
        if on
    ]
    table.add_row("Platforms", ", ".join(enabled) or "(none)")
    table.add_row("Webhook server", "on" if s.web_enabled else "off")
    console.print(table)


@app.command()
def say(address: str, text: str, verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Send a one-off message to ``platform:channel`` (boots adapters briefly)."""
    _setup_logging(verbose)
    from openpup.messaging.envelope import Envelope
    from openpup.messaging.registry import get_registry
    from openpup.platforms.base import build_enabled_adapters

    s = get_settings()

    async def _send() -> None:
        registry = get_registry()
        adapters = build_enabled_adapters(s, registry)
        for a in adapters:
            await a.start()
        ok = await registry.send(Envelope.to(address, text))
        for a in adapters:
            await a.stop()
        console.print("[green]sent[/green]" if ok else "[red]failed[/red]")

    asyncio.run(_send())


@memory_app.command("recall")
def memory_recall(query: str, top_k: int = 5) -> None:
    """Search the kennel for memories matching a query."""
    from openpup import memory

    get_settings()  # ensure kennel root env is set
    results = memory.recall(query, top_k=top_k)
    if not results:
        console.print("[dim]no matches[/dim]")
        return
    for i, r in enumerate(results, 1):
        console.print(f"[cyan]{i}.[/cyan] {r}")


@memory_app.command("recent")
def memory_recent(top_k: int = 5) -> None:
    """Show the most recent memories."""
    from openpup import memory

    get_settings()
    results = memory.recent(top_k=top_k)
    if not results:
        console.print("[dim]empty[/dim]")
        return
    for i, r in enumerate(results, 1):
        console.print(f"[cyan]{i}.[/cyan] {r}")


@routine_app.command("list")
def routine_list() -> None:
    """List scheduled jobs (reminders + tasks)."""
    from openpup.heartbeat.scheduler import Scheduler

    s = get_settings()
    sched = Scheduler.load(s.state_dir / "routines.json")
    if not sched.routines:
        console.print("[dim]no scheduled jobs[/dim]")
        return
    table = Table(title="Scheduled jobs")
    for col in ("name", "kind", "when", "deliver", "enabled"):
        table.add_column(col)
    for r in sched.routines:
        kind = "reminder" if r.message else "task"
        table.add_row(r.name, kind, r.describe_when(), r.deliver or "(owner)", str(r.enabled))
    console.print(table)


@routine_app.command("add")
def routine_add(
    name: str,
    deliver: str = typer.Option("", help="platform:channel address (default: owner)"),
    message: str = typer.Option("", help="plain reminder text to deliver"),
    prompt: str = typer.Option("", help="agent task prompt to run"),
    every: Optional[int] = typer.Option(None, help="recurring: seconds between runs"),
    daily: Optional[str] = typer.Option(None, help="recurring: HH:MM local time"),
    in_seconds: Optional[int] = typer.Option(None, "--in", help="one-shot: fire in N seconds"),
    at: Optional[str] = typer.Option(None, help="one-shot: ISO datetime (2026-06-09T09:00)"),
) -> None:
    """Add or replace a scheduled job (reminder or task)."""
    from openpup.heartbeat.scheduler import Scheduler, make_routine

    if not (message or prompt):
        console.print("[red]Provide --message or --prompt[/red]")
        raise typer.Exit(1)
    timings = [t for t in (every, daily, in_seconds, at) if t not in (None, "")]
    if len(timings) != 1:
        console.print("[red]Provide exactly one of --every, --daily, --in, --at[/red]")
        raise typer.Exit(1)
    s = get_settings()
    sched = Scheduler.load(s.state_dir / "routines.json")
    sched.add(
        make_routine(
            name=name,
            message=message,
            prompt=prompt,
            deliver=deliver,
            delay_seconds=in_seconds,
            at_iso=at,
            every_seconds=every,
            daily=daily,
        )
    )
    console.print(f"[green]added job '{name}'[/green]")


@access_app.command("list")
def access_list() -> None:
    """Show the owner and per-platform access policies."""
    from openpup.access import AccessControl, default_access_path

    s = get_settings()
    ac = AccessControl(
        default_access_path(s.state_dir),
        owner_address=s.owner_address,
        owner_addresses=s.owner_addresses,
    )
    console.print(ac.describe())


@access_app.command("owner")
def access_owner(
    address: str,
    primary: bool = typer.Option(
        True, "--primary/--add", help="--primary sets the default outreach target; --add only adds."
    ),
) -> None:
    """Add/set an owner address (platform:channel), written to .env.

    The owner can be reachable on several platforms (telegram + sms + ...). Use
    ``--add`` to register an extra one without changing your primary address.
    """
    from pathlib import Path

    from openpup.tui.env_store import EnvStore

    if ":" not in address:
        console.print("[red]Address must be 'platform:channel', e.g. sms:+15559876543[/red]")
        raise typer.Exit(1)
    store = EnvStore(Path.cwd() / ".env")
    if primary or not store.get("OPENPUP_OWNER_ADDRESS"):
        store.set("OPENPUP_OWNER_ADDRESS", address)
    existing = [a.strip() for a in store.get("OPENPUP_OWNER_ADDRESSES").split(",") if a.strip()]
    if address not in existing:
        existing.append(address)
    store.set("OPENPUP_OWNER_ADDRESSES", ",".join(existing))
    store.save()
    console.print(f"[green]Owner address registered: {address}[/green]")


@access_app.command("allow")
def access_allow(platform: str, identifier: str) -> None:
    """Allow a sender on a platform (chat id / phone / email / user id)."""
    from openpup.access import AccessControl, default_access_path

    s = get_settings()
    ac = AccessControl(
        default_access_path(s.state_dir),
        owner_address=s.owner_address,
        owner_addresses=s.owner_addresses,
    )
    ac.allow(platform, identifier)
    console.print(f"[green]Allowed {identifier} on {platform} (mode now allowlist).[/green]")


@access_app.command("deny")
def access_deny(platform: str, identifier: str) -> None:
    """Remove a sender from a platform's allowlist."""
    from openpup.access import AccessControl, default_access_path

    s = get_settings()
    ac = AccessControl(
        default_access_path(s.state_dir),
        owner_address=s.owner_address,
        owner_addresses=s.owner_addresses,
    )
    ok = ac.deny(platform, identifier)
    console.print(
        f"[green]Removed {identifier} from {platform}.[/green]"
        if ok
        else f"[yellow]{identifier} was not on {platform}'s allowlist.[/yellow]"
    )


@access_app.command("mode")
def access_mode(platform: str, mode: str) -> None:
    """Set a platform's access mode: open | allowlist | owner_only."""
    from openpup.access import MODES, AccessControl, default_access_path

    if mode not in MODES:
        console.print(f"[red]mode must be one of: {', '.join(MODES)}[/red]")
        raise typer.Exit(1)
    s = get_settings()
    ac = AccessControl(
        default_access_path(s.state_dir),
        owner_address=s.owner_address,
        owner_addresses=s.owner_addresses,
    )
    ac.set_mode(platform, mode)
    console.print(f"[green]{platform} access mode set to {mode}.[/green]")


@routine_app.command("rm")
def routine_rm(name: str) -> None:
    """Remove a routine by name."""
    from openpup.heartbeat.scheduler import Scheduler

    s = get_settings()
    sched = Scheduler.load(s.state_dir / "routines.json")
    ok = sched.remove(name)
    console.print("[green]removed[/green]" if ok else "[yellow]not found[/yellow]")


if __name__ == "__main__":
    app()
