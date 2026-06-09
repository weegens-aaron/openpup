"""The on-rails setup wizard.

For each platform: print numbered steps (with URLs you can auto-open), collect
each credential via the TUI prompt, run a LIVE validation, and only then write
to ``.env`` and flip the platform on. Telegram additionally auto-discovers your
chat id so proactive outreach works out of the box.
"""

from __future__ import annotations

import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel

from openpup.setup import validators
from openpup.tui.env_store import EnvStore, default_env_path
from openpup.tui.select import arrow_select_async, confirm, prompt_text

console = Console()


@dataclass
class Step:
    text: str
    url: Optional[str] = None


@dataclass
class CredField:
    key: str
    label: str
    secret: bool = False
    # Strip all internal whitespace (e.g. Gmail app passwords shown as 4x4 groups).
    strip_spaces: bool = False


@dataclass
class Flow:
    key: str
    title: str
    enable_key: str
    intro: str
    steps: List[Step]
    fields: List[CredField]
    # validator(values: dict) -> (ok, detail)
    validate: Callable[[dict], Awaitable[Tuple[bool, str]]]
    post_setup: Optional[Callable[[EnvStore, dict], Awaitable[None]]] = None
    needs_tunnel: bool = False
    extra: List[CredField] = field(default_factory=list)


# --------------------------------------------------------------------------
# Per-platform flows
# --------------------------------------------------------------------------
async def _telegram_post(store: EnvStore, values: dict) -> None:
    token = values["TELEGRAM_BOT_TOKEN"]
    console.print(
        "\n[bold]Let's grab your chat id so OpenPup can message you.[/bold]\n"
        "Open Telegram, find your new bot, and send it any message (e.g. 'hi').",
    )
    if not await confirm("Have you sent a message to the bot?", default_yes=True):
        return
    chat_id = await validators.telegram_discover_chat_id(token)
    if chat_id:
        store.set("OPENPUP_OWNER_ADDRESS", f"telegram:{chat_id}")
        console.print(f"[green]Found your chat id: {chat_id} -> owner address set.[/green]")
    else:
        console.print(
            "[yellow]Couldn't find a message yet. You can set OPENPUP_OWNER_ADDRESS "
            "later in 'openpup config'.[/yellow]"
        )


FLOWS: List[Flow] = [
    Flow(
        key="telegram",
        title="Telegram",
        enable_key="TELEGRAM_ENABLED",
        intro="A Telegram bot is the fastest way to talk to OpenPup. No public URL needed.",
        steps=[
            Step("Open Telegram and start a chat with @BotFather.", "https://t.me/BotFather"),
            Step("Send /newbot and follow the prompts (pick a name + username)."),
            Step("BotFather replies with a token like 123456:ABC-DEF... Copy it."),
        ],
        fields=[CredField("TELEGRAM_BOT_TOKEN", "Bot token", secret=True)],
        validate=lambda v: validators.validate_telegram(v["TELEGRAM_BOT_TOKEN"]),
        post_setup=_telegram_post,
    ),
    Flow(
        key="email",
        title="Email (IMAP/SMTP)",
        enable_key="EMAIL_ENABLED",
        intro="Connect a mailbox so OpenPup can read and reply to email. Use an app password.",
        steps=[
            Step(
                "For Gmail: enable 2FA, then create an App Password.",
                "https://myaccount.google.com/apppasswords",
            ),
            Step("Note your IMAP host (e.g. imap.gmail.com) and SMTP host (smtp.gmail.com)."),
            Step("Use the 16-char app password below (not your normal password)."),
        ],
        fields=[
            CredField("EMAIL_IMAP_HOST", "IMAP host"),
            CredField("EMAIL_IMAP_PORT", "IMAP port"),
            CredField("EMAIL_SMTP_HOST", "SMTP host"),
            CredField("EMAIL_SMTP_PORT", "SMTP port"),
            CredField("EMAIL_USERNAME", "Email address / username"),
            CredField("EMAIL_PASSWORD", "App password", secret=True, strip_spaces=True),
        ],
        validate=lambda v: validators.validate_email(
            v["EMAIL_IMAP_HOST"],
            int(v.get("EMAIL_IMAP_PORT") or 993),
            v["EMAIL_USERNAME"],
            v["EMAIL_PASSWORD"],
        ),
    ),
    Flow(
        key="discord",
        title="Discord",
        enable_key="DISCORD_ENABLED",
        intro="A Discord bot can DM you and respond to @mentions in servers.",
        steps=[
            Step(
                "Open the Discord Developer Portal and create a New Application.",
                "https://discord.com/developers/applications",
            ),
            Step("In the app: Bot -> Add Bot, then 'Reset Token' and copy it."),
            Step("Enable 'MESSAGE CONTENT INTENT' under Bot -> Privileged Gateway Intents."),
            Step("OAuth2 -> URL Generator: scope 'bot', then invite it to your server."),
        ],
        fields=[CredField("DISCORD_BOT_TOKEN", "Bot token", secret=True)],
        validate=lambda v: validators.validate_discord(v["DISCORD_BOT_TOKEN"]),
    ),
    Flow(
        key="sms",
        title="SMS (Twilio)",
        enable_key="SMS_ENABLED",
        intro="Twilio sends/receives SMS. Outbound works immediately; inbound needs a webhook.",
        steps=[
            Step("Create a Twilio account (trial is fine).", "https://www.twilio.com/try-twilio"),
            Step("Get a phone number with SMS capability under Phone Numbers."),
            Step(
                "Copy your Account SID and Auth Token from the Console dashboard.",
                "https://console.twilio.com",
            ),
        ],
        fields=[
            CredField("TWILIO_ACCOUNT_SID", "Account SID"),
            CredField("TWILIO_AUTH_TOKEN", "Auth token", secret=True),
            CredField("TWILIO_FROM_NUMBER", "Twilio phone number (+1...)"),
        ],
        validate=lambda v: validators.validate_twilio(
            v["TWILIO_ACCOUNT_SID"], v["TWILIO_AUTH_TOKEN"]
        ),
        needs_tunnel=True,
    ),
    Flow(
        key="whatsapp",
        title="WhatsApp (Meta Cloud API)",
        enable_key="WHATSAPP_ENABLED",
        intro="The most involved setup: a Meta app + a test number. Needs a public webhook.",
        steps=[
            Step(
                "Create a Meta app (type: Business) in the developer dashboard.",
                "https://developers.facebook.com/apps",
            ),
            Step("Add the 'WhatsApp' product; note the test Phone Number ID."),
            Step("Copy a temporary access token (or set up a permanent System User token)."),
            Step("Pick any string as your Verify Token (you'll reuse it in the webhook setup)."),
        ],
        fields=[
            CredField("WHATSAPP_PHONE_NUMBER_ID", "Phone Number ID"),
            CredField("WHATSAPP_ACCESS_TOKEN", "Access token", secret=True),
            CredField("WHATSAPP_VERIFY_TOKEN", "Verify token (any string)"),
        ],
        validate=lambda v: validators.validate_whatsapp(
            v["WHATSAPP_PHONE_NUMBER_ID"], v["WHATSAPP_ACCESS_TOKEN"]
        ),
        needs_tunnel=True,
    ),
]


def _flow_status(store: EnvStore, flow: Flow) -> str:
    if store.get_bool(flow.enable_key):
        return "[configured]"
    if any(store.get(f.key) for f in flow.fields):
        return "[partial]"
    return "[not set up]"


async def _open_url(url: str) -> None:
    if await confirm(f"Open {url} in your browser?", default_yes=True):
        try:
            webbrowser.open(url)
        except Exception:
            console.print(f"[yellow]Couldn't open a browser. Visit: {url}[/yellow]")


async def _run_flow(store: EnvStore, flow: Flow) -> None:
    console.print(Panel.fit(f"[bold]{flow.title}[/bold]\n{flow.intro}", border_style="cyan"))
    if flow.needs_tunnel:
        console.print(
            "[yellow]Note:[/yellow] inbound for this platform needs a public HTTPS URL. "
            "Outbound + validation work now; we'll wire the webhook tunnel separately.\n"
        )

    for i, step in enumerate(flow.steps, 1):
        console.print(f"  [cyan]{i}.[/cyan] {step.text}")
        if step.url:
            await _open_url(step.url)

    console.print()
    values: dict = {}
    for fld in flow.fields:
        current = store.get(fld.key)
        default = "" if fld.secret else current
        val = await prompt_text(f"{fld.label}:", default=default, is_password=fld.secret)
        if val is None:
            console.print("[yellow]Cancelled.[/yellow]")
            return
        val = val.strip()
        if fld.strip_spaces:
            val = val.replace(" ", "")
        # keep existing secret if blank entered
        if not val and fld.secret and current:
            val = current
        values[fld.key] = val

    console.print("\n[dim]Validating against the live service...[/dim]")
    ok, detail = await flow.validate(values)
    if not ok:
        console.print(f"[red]Validation failed:[/red] {detail}")
        if await confirm("Try again?", default_yes=True):
            await _run_flow(store, flow)
        return

    console.print(f"[green]Success:[/green] {detail}")
    for k, v in values.items():
        store.set(k, v)
    store.set_bool(flow.enable_key, True)
    if flow.post_setup:
        await flow.post_setup(store, values)
    store.save()
    console.print(f"[green]Saved {flow.title} to {store.path}.[/green]\n")


async def run_setup_wizard(env_path: Optional[Path] = None) -> None:
    path = default_env_path(env_path)
    store = EnvStore(path)

    console.print(
        Panel.fit(
            "[bold]Welcome to OpenPup setup[/bold]\n"
            "I'll walk you through each platform, open the right pages, and verify\n"
            "your credentials live before saving. Start with Telegram - it's the easiest.",
            border_style="green",
        )
    )

    while True:
        options = [f"{f.title:<26} {_flow_status(store, f)}" for f in FLOWS]
        options.append("Done")
        picked = await arrow_select_async("Pick a platform to set up (Esc/Done to finish)", options)
        if picked is None or picked == "Done":
            console.print("[green]Setup complete. Run 'openpup run' to start your pup.[/green]")
            return
        idx = options.index(picked)
        await _run_flow(store, FLOWS[idx])
