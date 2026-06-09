"""Editable per-platform user roster (config TUI).

A table of users for each connector (Discord, Telegram, WhatsApp, SMS,
iMessage) you can edit: name, handle, role, and notes. Roles feed access
control -- ``blocked`` users are ignored, ``allowed`` can always talk to the
pup, ``owner`` is treated as you. Backed by the same contacts.json the pup
learns into automatically.
"""

from __future__ import annotations

from typing import Optional

from rich.console import Console

from openpup.directory import get_directory
from openpup.tui.select import arrow_select_async, confirm, prompt_text

console = Console()

PLATFORMS = ["discord", "telegram", "whatsapp", "sms", "imessage"]
ROLE_CHOICES = ["(none)", "allowed", "blocked", "owner"]


def _role_label(role: str) -> str:
    return role or "-"


async def _pick_role(current: str) -> Optional[str]:
    start = ROLE_CHOICES.index(current) if current in ROLE_CHOICES else 0
    if not current:
        start = 0

    def preview(idx: int) -> str:
        return {
            "(none)": "No special access; follows the platform's mode.",
            "allowed": "Always allowed to talk to the pup (even in allowlist mode).",
            "blocked": "Ignored -- messages are dropped.",
            "owner": "Treated as you (the owner). Full access.",
        }.get(ROLE_CHOICES[idx], "")

    picked = await arrow_select_async(
        "Role", ROLE_CHOICES, preview_callback=preview, start_index=start
    )
    if picked is None:
        return None
    return "" if picked == "(none)" else picked


async def _edit_user(platform: str, user: dict) -> None:
    directory = get_directory()
    while True:
        options = [
            f"Name      {user.get('name', '')}",
            f"Handle    {user['channel']}",
            f"Role      {_role_label(user.get('role', ''))}",
            f"Notes     {user.get('notes', '')}",
            "Remove user",
            "<- Back",
        ]
        picked = await arrow_select_async(
            f"{platform}: {user.get('name') or user['channel']}", options
        )
        if picked is None or picked.startswith("<- Back"):
            return
        if picked.startswith("Name"):
            val = await prompt_text("Name:", default=user.get("name", ""))
            if val is not None:
                directory.upsert(platform, user["channel"], name=val.strip())
                user["name"] = val.strip()
        elif picked.startswith("Role"):
            role = await _pick_role(user.get("role", ""))
            if role is not None:
                directory.upsert(platform, user["channel"], role=role)
                user["role"] = role
        elif picked.startswith("Notes"):
            val = await prompt_text("Notes:", default=user.get("notes", ""))
            if val is not None:
                directory.upsert(platform, user["channel"], notes=val.strip())
                user["notes"] = val.strip()
        elif picked.startswith("Handle"):
            console.print("[dim]Handle is the key; to change it, remove and re-add.[/dim]")
        elif picked.startswith("Remove"):
            if await confirm(f"Remove {user.get('name') or user['channel']}?", default_yes=False):
                directory.remove(platform, user["channel"])
                return


async def _add_user(platform: str) -> None:
    directory = get_directory()
    handle = await prompt_text(f"{platform} handle (chat id / phone / email / user id):")
    if not handle or not handle.strip():
        return
    handle = handle.strip()
    name = await prompt_text("Name:", default=handle)
    role = await _pick_role("")
    notes = await prompt_text("Notes (optional):", default="")
    directory.upsert(
        platform,
        handle,
        name=(name or handle).strip(),
        role=role or "",
        notes=(notes or "").strip(),
    )
    console.print(f"[green]Added {name or handle} to {platform}.[/green]")


async def _platform_menu(platform: str) -> None:
    directory = get_directory()
    while True:
        users = directory.by_platform(platform)
        rows = [
            f"{(u.get('name') or u['channel']):<22} {u['channel']:<24} [{_role_label(u.get('role', ''))}]"
            for u in users
        ]
        options = rows + ["+ Add user", "<- Back"]

        def preview(idx: int) -> str:
            if idx < len(users):
                u = users[idx]
                return u.get("notes") or "(no notes)"
            return ""

        picked = await arrow_select_async(
            f"{platform} users ({len(users)})", options, preview_callback=preview
        )
        if picked is None or picked.startswith("<- Back"):
            return
        if picked.startswith("+ Add user"):
            await _add_user(platform)
            continue
        idx = options.index(picked)
        await _edit_user(platform, users[idx])


async def run_roster_menu() -> None:
    directory = get_directory()
    while True:
        options = [f"{p:<12} ({len(directory.by_platform(p))} users)" for p in PLATFORMS]
        options.append("<- Done")
        picked = await arrow_select_async("User roster -- pick a platform", options)
        if picked is None or picked.startswith("<- Done"):
            return
        platform = PLATFORMS[options.index(picked)]
        await _platform_menu(platform)
