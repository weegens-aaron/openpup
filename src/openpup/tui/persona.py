"""Interactive persona editor for OpenPup's SOUL (identity).

``openpup persona`` (and the Persona entry in ``openpup config``) lets you set
the pup's Name, Personality vibe, and Proactivity from presets, preview the
generated SOUL, and regenerate ``~/.openpup/SOUL.md`` -- or drop into your
$EDITOR for full hand-control.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from openpup import prompting
from openpup.tui.env_store import EnvStore, default_env_path
from openpup.tui.select import arrow_select_async, confirm, prompt_text

console = Console()

PERSONALITY_LABELS = {
    "warm_loyal_sassy": "Warm & loyal but sassy",
    "sharp_dry": "Sharp & dry",
    "calm_pro": "Calm & professional",
    "chaotic_retriever": "Chaotic golden retriever",
}
PROACTIVITY_LABELS = {
    "relentless": "Relentless problem-solver",
    "proactive": "Proactive",
    "balanced": "Balanced",
    "reserved": "Reserved",
}


def _label(mapping: dict, key: str) -> str:
    return mapping.get(key, key)


async def _pick(title: str, presets: dict, labels: dict, current: str) -> Optional[str]:
    keys = list(presets.keys())
    options = [labels.get(k, k) for k in keys]
    start = keys.index(current) if current in keys else 0

    def preview(idx: int) -> str:
        return presets.get(keys[idx], "")

    picked = await arrow_select_async(title, options, preview_callback=preview, start_index=start)
    if picked is None:
        return None
    return keys[options.index(picked)]


async def run_persona_menu(env_path: Optional[Path] = None) -> None:
    path = default_env_path(env_path)
    store = EnvStore(path)

    name = store.get("OPENPUP_NAME") or "OpenPup"
    personality = store.get("OPENPUP_PERSONALITY") or prompting.DEFAULT_PERSONALITY
    proactivity = store.get("OPENPUP_PROACTIVITY") or prompting.DEFAULT_PROACTIVITY

    console.print(
        Panel.fit(
            "[bold]Persona editor[/bold]\n"
            "Shape your pup's identity. Changes regenerate ~/.openpup/SOUL.md.",
            border_style="green",
        )
    )

    while True:
        options = [
            f"Name                {name}",
            f"Personality         {_label(PERSONALITY_LABELS, personality)}",
            f"Proactivity         {_label(PROACTIVITY_LABELS, proactivity)}",
            "Preview SOUL",
            "Save + regenerate SOUL.md",
            "Edit SOUL.md in $EDITOR (advanced)",
            "Exit without saving",
        ]

        def preview(idx: int) -> str:
            if idx == 0:
                return "The name your pup goes by."
            if idx == 1:
                return prompting.PERSONALITY_PRESETS.get(personality, "")
            if idx == 2:
                return prompting.PROACTIVITY_PRESETS.get(proactivity, "")
            if idx == 3:
                return "Show the full generated SOUL text."
            if idx == 4:
                return "Write OPENPUP_NAME/PERSONALITY/PROACTIVITY to .env and rebuild SOUL.md."
            if idx == 5:
                return "Open the raw SOUL.md in your editor for full control."
            return "Discard changes."

        picked = await arrow_select_async("Persona", options, preview_callback=preview)
        if picked is None or picked.startswith("Exit"):
            return

        if picked.startswith("Name"):
            val = await prompt_text("Name:", default=name)
            if val:
                name = val.strip()
        elif picked.startswith("Personality"):
            chosen = await _pick(
                "Personality", prompting.PERSONALITY_PRESETS, PERSONALITY_LABELS, personality
            )
            if chosen:
                personality = chosen
        elif picked.startswith("Proactivity"):
            chosen = await _pick(
                "Proactivity", prompting.PROACTIVITY_PRESETS, PROACTIVITY_LABELS, proactivity
            )
            if chosen:
                proactivity = chosen
        elif picked.startswith("Preview"):
            console.print(
                Panel(
                    prompting.render_soul(name, personality, proactivity),
                    title="SOUL preview",
                    border_style="cyan",
                )
            )
            await confirm("Looks good? (either choice returns to the menu)", default_yes=True)
        elif picked.startswith("Save"):
            store.set("OPENPUP_NAME", name)
            store.set("OPENPUP_PERSONALITY", personality)
            store.set("OPENPUP_PROACTIVITY", proactivity)
            store.save()
            soul = prompting.write_soul(name, personality, proactivity)
            console.print(f"[green]Saved persona and regenerated {soul}[/green]")
            return
        elif picked.startswith("Edit SOUL.md"):
            soul = prompting.soul_path()
            if not soul.exists():
                prompting.write_soul(name, personality, proactivity)
            editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
            console.print(f"[dim]Opening {soul} in {editor}...[/dim]")
            try:
                subprocess.call([editor, str(soul)])
                console.print("[green]SOUL.md saved (hand-edited).[/green]")
            except Exception as exc:
                console.print(f"[red]Could not launch editor ({exc}). Edit {soul} manually.[/red]")
            return
