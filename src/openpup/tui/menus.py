"""The OpenPup configuration menu tree (code-puppy style).

``openpup config`` opens the main menu. Sections cover Identity & Model, Owner
& Memory, Heartbeat, and one per platform plus the webhook server. Edits are
saved to the SQLite config store immediately and applied live (no .env, no
restart for plain settings).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from rich.console import Console

from openpup.config_store import ConfigStore, get_config_store
from openpup.tui.select import arrow_select_async, prompt_text

console = Console()

# Field types: text, secret, bool, number, choice, model, behaviors
HEARTBEAT_BEHAVIORS = ["reflect", "outreach", "routines", "inbound"]


@dataclass
class Field:
    key: str
    label: str
    kind: str = "text"
    choices: Optional[List[str]] = None
    help: str = ""


@dataclass
class Section:
    title: str
    fields: List[Field] = field(default_factory=list)


SCHEMA: List[Section] = [
    Section(
        "Identity & Model",
        [
            Field("OPENPUP_NAME", "Name", "text", help="Display name for your pup."),
            Field(
                "OPENPUP_AGENT",
                "Agent",
                "text",
                help="'auto' = agent named after your pup; or an explicit code-puppy agent name.",
            ),
            Field(
                "OPENPUP_MODEL", "Model", "model", help="Model name (blank = code-puppy default)."
            ),
            Field(
                "OPENPUP_REFLECTION_MODEL",
                "Reflection model",
                "model",
                help="Cheap model for heartbeat ticks.",
            ),
            Field(
                "OPENPUP_UNIVERSAL_CONSTRUCTOR",
                "Universal Constructor",
                "bool",
                help="Let the agent build its own tools at runtime.",
            ),
        ],
    ),
    Section(
        "Owner & Memory",
        [
            Field(
                "OPENPUP_OWNER_ADDRESS",
                "Owner address",
                "text",
                help="platform:channel for proactive messages.",
            ),
            Field(
                "PUPPY_KENNEL_ROOT", "Kennel (memory) root", "text", help="Where memory is stored."
            ),
            Field(
                "OPENPUP_SEND_POLICY",
                "Send policy",
                "choice",
                choices=["open", "contacts", "owner_only"],
                help="Who the agent may message.",
            ),
            Field(
                "OPENPUP_SEND_RATE_PER_MIN",
                "Send rate / min",
                "number",
                help="Per-platform outbound cap per minute.",
            ),
        ],
    ),
    Section(
        "Heartbeat (consciousness)",
        [
            Field("OPENPUP_HEARTBEAT_ENABLED", "Enabled", "bool"),
            Field("OPENPUP_HEARTBEAT_INTERVAL", "Interval (seconds)", "number"),
            Field("OPENPUP_HEARTBEAT_JITTER", "Jitter (+/- seconds)", "number"),
            Field("OPENPUP_HEARTBEAT_BEHAVIORS", "Behaviors", "behaviors"),
            Field("OPENPUP_QUIET_HOURS", "Quiet hours (e.g. 23-7)", "text"),
            Field("OPENPUP_OUTREACH_MAX_PER_DAY", "Max outreach / day", "number"),
        ],
    ),
    Section(
        "Discord",
        [
            Field("DISCORD_ENABLED", "Enabled", "bool"),
            Field("DISCORD_BOT_TOKEN", "Bot token", "secret"),
        ],
    ),
    Section(
        "Telegram",
        [
            Field("TELEGRAM_ENABLED", "Enabled", "bool"),
            Field("TELEGRAM_BOT_TOKEN", "Bot token", "secret"),
        ],
    ),
    Section(
        "WhatsApp",
        [
            Field("WHATSAPP_ENABLED", "Enabled", "bool"),
            Field("WHATSAPP_PHONE_NUMBER_ID", "Phone number ID", "text"),
            Field("WHATSAPP_ACCESS_TOKEN", "Access token", "secret"),
            Field("WHATSAPP_VERIFY_TOKEN", "Verify token", "secret"),
        ],
    ),
    Section(
        "Email",
        [
            Field("EMAIL_ENABLED", "Enabled", "bool"),
            Field("EMAIL_IMAP_HOST", "IMAP host", "text"),
            Field("EMAIL_IMAP_PORT", "IMAP port", "number"),
            Field("EMAIL_SMTP_HOST", "SMTP host", "text"),
            Field("EMAIL_SMTP_PORT", "SMTP port", "number"),
            Field("EMAIL_USERNAME", "Username", "text"),
            Field("EMAIL_PASSWORD", "Password", "secret"),
        ],
    ),
    Section(
        "iMessage (macOS)",
        [
            Field("IMESSAGE_ENABLED", "Enabled", "bool"),
            Field("IMESSAGE_POLL_SECONDS", "Poll interval (seconds)", "number"),
            Field("IMESSAGE_DB_PATH", "Messages DB path", "text"),
        ],
    ),
    Section(
        "SMS (Twilio)",
        [
            Field("SMS_ENABLED", "Enabled", "bool"),
            Field("TWILIO_ACCOUNT_SID", "Account SID", "text"),
            Field("TWILIO_AUTH_TOKEN", "Auth token", "secret"),
            Field("TWILIO_FROM_NUMBER", "From number", "text"),
        ],
    ),
    Section(
        "Webhook server",
        [
            Field("OPENPUP_WEB_ENABLED", "Enabled", "bool"),
            Field("OPENPUP_WEB_HOST", "Host", "text"),
            Field("OPENPUP_WEB_PORT", "Port", "number"),
            Field("OPENPUP_WEBHOOK_SECRET", "Webhook secret", "secret"),
        ],
    ),
]


def _display_value(store: ConfigStore, fld: Field) -> str:
    raw = store.get(fld.key)
    if fld.kind == "bool":
        return "on" if store.get_bool(fld.key) else "off"
    if fld.kind == "secret":
        return "********" if raw else "(unset)"
    return raw or "(unset)"


def _available_models() -> List[str]:
    try:
        from code_puppy.model_factory import ModelFactory

        config = ModelFactory.load_config()
        return sorted(config.keys())
    except Exception:
        return []


async def _edit_field(store: ConfigStore, fld: Field) -> None:
    if fld.kind == "bool":
        current = store.get_bool(fld.key)
        picked = await arrow_select_async(
            f"{fld.label}", ["on", "off"], start_index=0 if current else 1
        )
        if picked is not None:
            store.set_bool(fld.key, picked == "on")
        return

    if fld.kind == "behaviors":
        await _edit_behaviors(store, fld)
        return

    if fld.kind == "choice" and fld.choices:
        picked = await arrow_select_async(fld.label, fld.choices)
        if picked is not None:
            store.set(fld.key, picked)
        return

    if fld.kind == "model":
        models = _available_models()
        options = ["(blank / code-puppy default)", "(enter manually)"] + models
        picked = await arrow_select_async(
            f"{fld.label} -- current: {store.get(fld.key) or '(default)'}", options
        )
        if picked is None:
            return
        if picked == options[0]:
            store.set(fld.key, "")
        elif picked == options[1]:
            value = await prompt_text(f"{fld.label}:", default=store.get(fld.key))
            if value is not None:
                store.set(fld.key, value.strip())
        else:
            store.set(fld.key, picked)
        return

    # text / secret / number -> text prompt
    is_secret = fld.kind == "secret"
    hint = f"{fld.label}" + (f"  [{fld.help}]" if fld.help else "")
    value = await prompt_text(
        f"{hint}:", default="" if is_secret else store.get(fld.key), is_password=is_secret
    )
    if value is not None:
        store.set(fld.key, value.strip())


async def _edit_behaviors(store: ConfigStore, fld: Field) -> None:
    selected = set(b.strip() for b in store.get(fld.key).split(",") if b.strip())
    cursor = 0
    while True:
        rows = [f"[{'x' if b in selected else ' '}] {b}" for b in HEARTBEAT_BEHAVIORS]
        rows.append("Done")
        picked = await arrow_select_async(
            "Toggle heartbeat behaviors (Enter to toggle, choose Done to finish)",
            rows,
            start_index=cursor,
        )
        if picked is None or picked == "Done":
            break
        cursor = rows.index(picked)
        name = picked.split("] ", 1)[1]
        if name in selected:
            selected.discard(name)
        else:
            selected.add(name)
    ordered = [b for b in HEARTBEAT_BEHAVIORS if b in selected]
    store.set(fld.key, ",".join(ordered))


async def _section_menu(store: ConfigStore, section: Section) -> None:
    cursor = 0
    while True:
        labels = [f"{f.label:<24} {_display_value(store, f)}" for f in section.fields]
        labels.append("<- Back")

        def preview(idx: int) -> str:
            if idx < len(section.fields):
                f = section.fields[idx]
                return f.help or f"Set {f.key}"
            return "Return to the main menu"

        picked = await arrow_select_async(
            f"{section.title}", labels, preview_callback=preview, start_index=cursor
        )
        if picked is None or picked == "<- Back":
            return
        idx = labels.index(picked)
        cursor = idx
        await _edit_field(store, section.fields[idx])


async def run_config_menu(env_path: Optional[Path] = None) -> None:
    """Entry point: open the interactive configuration menu."""
    # env_path is accepted for back-compat; config now lives in the SQLite store.
    store = get_config_store()
    cursor = 0

    persona_label = "Persona / Identity (edit SOUL)..."
    roster_label = "Users / Roster (per platform)..."
    schedules_label = "View scheduled prompts & notifications..."
    while True:
        options = [s.title for s in SCHEMA]
        # Edits persist to the config store immediately (and apply live), so
        # there's no save/discard step -- just a single way out.
        options += [persona_label, roster_label, schedules_label, "Done"]

        def preview(idx: int) -> str:
            if idx < len(SCHEMA):
                section = SCHEMA[idx]
                enabled_field = next(
                    (f for f in section.fields if f.key.endswith("_ENABLED")), None
                )
                if enabled_field:
                    state = "on" if store.get_bool(enabled_field.key) else "off"
                    return f"{len(section.fields)} settings - currently {state}"
                return f"{len(section.fields)} settings"
            return ""

        picked = await arrow_select_async(
            f"OpenPup configuration  ({store.path})",
            options,
            preview_callback=preview,
            start_index=cursor,
        )
        if picked is None or picked == "Done":
            console.print(f"[green]Config saved live to {store.path}[/green]")
            return

        if picked == persona_label:
            from openpup.tui.persona import run_persona_menu

            await run_persona_menu()
            store.load()  # shared SQLite store; reload is a harmless no-op
            cursor = len(SCHEMA)
            continue

        if picked == roster_label:
            from openpup.tui.roster import run_roster_menu

            await run_roster_menu()
            cursor = len(SCHEMA) + 1
            continue

        if picked == schedules_label:
            from openpup.tui.schedules import run_schedules_view

            await run_schedules_view()
            cursor = len(SCHEMA) + 2
            continue

        cursor = options.index(picked)
        section = SCHEMA[cursor]
        await _section_menu(store, section)
