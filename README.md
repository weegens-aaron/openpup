# OpenPup

**An always-on AI companion built on the [code-puppy](https://github.com/mpfaffenberger/code_puppy) SDK.**

OpenPup wraps code-puppy as a *library* and adds three things to turn a coding
agent into a persistent, reachable companion:

1. **Memory** - persistent local memory via code-puppy's `puppy_kennel`
   (SQLite + FTS5, no daemon, no cloud).
2. **A consciousness heartbeat** - a periodic loop that lets the agent
   *think on its own*: idle self-reflection, proactive outreach, scheduled
   routines, and inbound polling.
3. **Messaging integrations** - reach it (and let it reach you) over
   **Discord, Telegram, WhatsApp, Email, and SMS**.

---

## How it works

```
            inbound msg                          outbound reply / proactive ping
 Discord  ┐                                                          ┌ Discord
 Telegram ┤                                                          ┤ Telegram
 WhatsApp ┼──> PlatformRegistry ──> OpenPup runtime ──> AgentHost ──>┼ WhatsApp
 Email    ┤        (envelopes)         (routing)      (code-puppy)   ┤ Email
 SMS      ┘                                  │                       └ SMS
                                             │
                                  ┌──────────┴──────────┐
                                  │   Heartbeat (loop)  │
                                  │  reflect / outreach │
                                  │  routines / inbound │
                                  └──────────┬──────────┘
                                             │
                                     puppy_kennel memory
```

- The agent is code-puppy plus OpenPup's own tools: `openpup_check_email`,
  `openpup_send_message`, `openpup_list_platforms`, and (optionally) the
  **Universal Constructor** (`universal_constructor`) so it can build brand-new
  tools for itself at runtime. So "check my email" or "text my owner" just work.
- Every inbound message becomes a normalized **`Envelope`** and is routed
  through the agent; the reply goes back out on the same channel.
- The **heartbeat** ticks on a jittered interval and runs whichever behaviors
  you enable. Idle reflection reads recent memory, writes a short private
  reflection back to memory, and builds a continuous inner narrative across
  restarts - the "simulated consciousness."
- All long-term state lives in the **kennel** so the mind survives restarts.

## Install

```bash
git clone https://github.com/mpfaffenberger/openpup
cd openpup
uv venv && source .venv/bin/activate
uv pip install -e ".[all]"      # or pick extras: .[discord,telegram,email]
```

Extras: `discord`, `telegram`, `whatsapp` (uses `web`), `email`, `sms`, `web`, `all`.

## Configure

The easiest way is the **interactive TUI** (same arrow-key menu style as
code-puppy's `/agent` and `/model_settings`):

```bash
openpup config
```

It opens a nested menu - Identity & Model, Owner & Memory, Heartbeat, and one
section per platform plus the webhook server - with a live preview pane, secret
masking, on/off toggles, a model picker (pulled from your code-puppy config),
and a behaviors multi-toggle. Changes are written back to `.env`.

```
OpenPup configuration  (/path/.env)

> Identity & Model
  Owner & Memory
  Heartbeat (consciousness)
  Telegram
  ...
  Save & exit
  Exit without saving
+- Preview ----------------------------------------+
| 4 settings - currently on                        |
+--------------------------------------------------+
(Up/Down or Ctrl+P/N to move, Enter to confirm, Esc to cancel)
```

Prefer editing by hand? Copy the template instead:

```bash
cp .env.example .env   # then edit
```

You also need a working code-puppy model configuration (`~/.code_puppy`). Set
`OPENPUP_MODEL` (or use the TUI model picker), or leave it blank for
code-puppy's default.

## Run

```bash
openpup config     # interactive TUI configuration menus
openpup status     # show config + enabled platforms
openpup run        # start the always-on companion (Ctrl-C to stop)
```

Other commands:

```bash
openpup say telegram:123456 "hello from the pup"     # one-off message
openpup memory recall "what did we decide about X"    # search memory
openpup memory recent                                 # recent memories
openpup routine add digest "Summarize today's AI news" \
    --deliver telegram:123456 --daily 08:00           # scheduled routine
openpup routine list
```

## Agentic behavior & prompting (hermes-style)

OpenPup ports hermes-agent's prompting and task-tracking discipline on top of
code-puppy's tool-calling loop, so the agent plans and *finishes* multi-step
work instead of stopping after a stub:

- **Editable identity** — `~/.openpup/SOUL.md` is your pup's persona (created on
  first run; edit it freely). `~/.openpup/USER.md` holds durable facts about you.
- **Layered system prompt** — SOUL identity -> capabilities -> agentic guidance
  ("take action, don't describe it"; "finish the job, never fabricate output";
  memory discipline) -> user profile -> recent-memory snapshot -> timestamp.
- **Task list tool** — `openpup_todo` (ported from hermes) lets the agent
  decompose a request into a plan and work it top-to-bottom, one item
  `in_progress` at a time. Per-conversation, so chats don't share a list.

What's intentionally *not* copied: hermes's own run-loop (code-puppy already has
one) and heavyweight subsystems (kanban/browser/tts). OpenPup's heartbeat +
routines cover recurring autonomous work.

## Access control (owner + allowlists)

OpenPup distinguishes **you (the owner)** from anyone else who messages the bot.
The owner is your `OPENPUP_OWNER_ADDRESS` (`platform:channel`). Privileged tools
(reading your email, sending messages on your behalf) are **owner-only**; the
agent is told per-message whether it's talking to you or a stranger.

Each platform has an access **mode**:

| Mode | Who can interact |
|------|------------------|
| `open` (default) | anyone (but the owner is still distinguished) |
| `allowlist` | the owner + allow-listed senders |
| `owner_only` | only the owner |

```bash
openpup access list                          # show owner + policies
openpup access owner telegram:12345          # mark yourself as owner
openpup access allow telegram 67890          # whitelist a friend (-> allowlist mode)
openpup access mode telegram allowlist       # lock telegram to owner + allowlist
openpup access deny telegram 67890           # remove someone
```

Senders are matched by chat id / phone / email / user id, so it works across
platforms. Policies persist to `~/.openpup/access.json`.

## The heartbeat behaviors

Configured via `OPENPUP_HEARTBEAT_BEHAVIORS` (comma-separated):

| Behavior   | What it does |
|------------|--------------|
| `reflect`  | Reads recent memory, writes a short private reflection back to memory. |
| `outreach` | Decides whether to message you unprompted. Guard-railed: quiet hours + daily cap + the agent must explicitly opt in. |
| `routines` | Runs due scheduled tasks and delivers their output (with a `[SILENT]` no-spam escape hatch). |
| `inbound`  | Polls poll-based adapters (e.g. email IMAP) for new messages. |

Tuning knobs (see `.env.example`): `OPENPUP_HEARTBEAT_INTERVAL`,
`OPENPUP_HEARTBEAT_JITTER`, `OPENPUP_QUIET_HOURS`, `OPENPUP_OUTREACH_MAX_PER_DAY`,
`OPENPUP_REFLECTION_MODEL` (use a cheap model for ticks).

## Platform notes

- **Discord** - bot token; responds to DMs and @mentions.
- **Telegram** - bot token; long-polling, no public URL needed.
- **WhatsApp** - Meta Cloud API; requires the webhook server
  (`OPENPUP_WEB_ENABLED=true`) reachable over HTTPS + a verified number.
- **Email** - IMAP polling in, SMTP out; subject lines are preserved on replies.
- **SMS** - Twilio; inbound via the webhook server's `/webhook/sms` route.

## Architecture

```
src/openpup/
  config.py            # pydantic-settings, all env config
  runtime.py           # boots everything, central async loop
  agent_host.py        # headless code-puppy SDK harness
  memory.py            # facade over puppy_kennel
  webserver.py         # FastAPI inbound webhooks (WhatsApp/SMS)
  messaging/
    envelope.py        # normalized message model
    registry.py        # adapter registry + delivery
  platforms/
    base.py            # PlatformAdapter interface + factory
    discord_adapter.py telegram_adapter.py whatsapp_adapter.py
    email_adapter.py   sms_adapter.py
  heartbeat/
    engine.py          # the tick loop
    scheduler.py       # routine scheduling (no cron dep)
    reflect.py outreach.py routines.py
  tui/
    select.py          # prompt_toolkit arrow-select + input primitives
    env_store.py       # comment-preserving .env editor
    menus.py           # the config menu tree
```

## Development

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
ruff check --fix src tests && ruff format src tests
```

## License

MIT. Built on code-puppy. Integration designs inspired by hermes-agent.
