"""Reusable prompt_toolkit selection + input primitives (code-puppy style).

Kept dependency-free of code-puppy internals so OpenPup owns its own UX.
"""

from __future__ import annotations

import html
import sys
from typing import Callable, List, Optional

from prompt_toolkit import Application, PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl


async def arrow_select_async(
    message: str,
    choices: List[str],
    preview_callback: Optional[Callable[[int], str]] = None,
    start_index: int = 0,
) -> Optional[str]:
    """Arrow-key navigable selector with an optional preview pane.

    Returns the selected choice, or ``None`` if the user cancels (Ctrl-C/Esc).
    """
    if not choices:
        return None

    selected = [max(0, min(start_index, len(choices) - 1))]
    result: List[Optional[str]] = [None]

    def render() -> HTML:
        safe_message = html.escape(message)
        lines = [f"<b>{safe_message}</b>", ""]
        for i, choice in enumerate(choices):
            safe_choice = html.escape(choice)
            if i == selected[0]:
                lines.append(f"<ansigreen>> {safe_choice}</ansigreen>")
            else:
                lines.append(f"  {safe_choice}")
        lines.append("")
        if preview_callback is not None:
            preview = preview_callback(selected[0])
            if preview:
                import textwrap

                width = 64
                lines.append("<ansiyellow>+- Preview " + "-" * (width - 10) + "+</ansiyellow>")
                wrapped = []
                for para in preview.splitlines() or [""]:
                    wrapped.extend(textwrap.wrap(para, width=width - 2) or [""])
                for wl in wrapped:
                    safe = html.escape(wl).ljust(width - 2)
                    lines.append(f"<dim>| {safe} |</dim>")
                lines.append("<ansiyellow>+" + "-" * width + "+</ansiyellow>")
                lines.append("")
        lines.append(
            "<ansicyan>(Up/Down or Ctrl+P/N to move, Enter to confirm, Esc to cancel)</ansicyan>"
        )
        return HTML("\n".join(lines))

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("c-p")
    def _up(event):
        selected[0] = (selected[0] - 1) % len(choices)
        event.app.invalidate()

    @kb.add("down")
    @kb.add("c-n")
    def _down(event):
        selected[0] = (selected[0] + 1) % len(choices)
        event.app.invalidate()

    @kb.add("enter")
    def _accept(event):
        result[0] = choices[selected[0]]
        event.app.exit()

    @kb.add("c-c")
    @kb.add("escape")
    def _cancel(event):
        result[0] = None
        event.app.exit()

    control = FormattedTextControl(render)
    app = Application(layout=Layout(Window(content=control)), key_bindings=kb, full_screen=False)

    sys.stdout.flush()
    sys.stderr.flush()
    await app.run_async()
    return result[0]


async def prompt_text(message: str, default: str = "", is_password: bool = False) -> Optional[str]:
    """Single-line text input. Returns the string, or None on Ctrl-C/Esc.

    Async because the config menu runs inside a live event loop -- the
    synchronous ``prompt()`` would call ``asyncio.run()`` and blow up.
    """
    session: PromptSession = PromptSession()
    try:
        return await session.prompt_async(
            HTML(f"<ansicyan>{html.escape(message)}</ansicyan> "),
            default=default or "",
            is_password=is_password,
        )
    except (KeyboardInterrupt, EOFError):
        return None


async def confirm(message: str, default_yes: bool = True) -> bool:
    """Yes/No selector. Returns the boolean choice."""
    choices = ["yes", "no"]
    start = 0 if default_yes else 1
    picked = await arrow_select_async(message, choices, start_index=start)
    return picked == "yes"
