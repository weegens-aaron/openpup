"""Route OpenPup's stdlib logging through code-puppy's rich queue console.

Every module keeps using plain ``logging.getLogger("openpup.*")`` -- this
swaps the *sink*, not the sources. Records flow into code-puppy's
``QueueConsole`` (code_puppy.tools.common.console) and a
``SynchronousInteractiveRenderer`` drains the queue onto the terminal.

Bonus: starting a renderer also surfaces the agent's own ``emit_info`` /
``emit_warning`` output during runs, which previously piled up unrendered in
the queue's startup buffer.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from typing import Optional

#: Third-party loggers that bark on every HTTP request. WARNING-only, always.
_HTTP_LOGGERS = ("httpx", "httpcore", "hpack", "h2")
#: Chatty platform SDKs whose INFO lines are noise ("logging in using static
#: token", "Application started", ...). Keep their warnings/errors, drop INFO.
_CHAT_LOGGERS = ("discord", "discord.client", "telegram", "telegram.ext")
#: code-puppy's OAuth model-loaders re-emit "Loaded/Filtered N models" on every
#: agent (re)build -- so a single streaming retry storm reprints them many
#: times. Pure operational noise; clamp to WARNING.
_MODEL_LOADER_LOGGERS = (
    "code_puppy.plugins.claude_code_oauth",
    "code_puppy.plugins.chatgpt_oauth",
    "code_puppy.plugins.gemini_oauth",
    "code_puppy.plugins.copilot_oauth",
)
#: Everything we clamp to WARNING so it stops spamming INFO at the owner.
NOISY_LOGGERS = _HTTP_LOGGERS + _CHAT_LOGGERS + _MODEL_LOADER_LOGGERS

_DATE_FORMAT = "%H:%M:%S"

# Per-level (label, label-style, message-style). Labels are lowercase + padded
# so columns line up; structural bits are dim so the MESSAGE is what pops.
# Color only -- no glyphs/emoji -- so it stays crisp in any terminal.
_LEVELS = {
    logging.DEBUG: ("debug", "dim", "dim"),
    logging.INFO: ("info", "cyan", ""),
    logging.WARNING: ("warn", "yellow", "yellow"),
    logging.ERROR: ("error", "bold red", "red"),
    logging.CRITICAL: ("crit", "bold white on red", "bold red"),
}
_DEFAULT_LEVEL = ("log", "", "")

# Exception text is formatted via a bare Formatter (stdlib does the traceback).
_EXC_FORMATTER = logging.Formatter()

_renderer = None
_renderer_lock = threading.Lock()


class QueueConsoleHandler(logging.Handler):
    """A logging.Handler that prints pretty, styled lines through code-puppy's
    rich console.

    Each record renders as::

        15:06:43  info   registry        Registered platform adapter: discord

    -- dim timestamp, a colored level badge, the dimmed subsystem (the
    ``openpup.`` prefix is stripped), then the message in a level-appropriate
    style. Built as a ``rich.text.Text`` with explicit spans (never markup),
    so messages containing ``[brackets]`` render literally. A re-entrancy
    guard drops logs emitted *while rendering a log*.
    """

    def __init__(self) -> None:
        super().__init__()
        self._reentry = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._reentry, "active", False):
            return
        self._reentry.active = True
        try:
            from code_puppy.tools.common import console

            console.print(self._render(record))
        except Exception:
            self.handleError(record)
        finally:
            self._reentry.active = False

    def _render(self, record: logging.LogRecord) -> "object":
        from rich.text import Text

        label, label_style, msg_style = _LEVELS.get(record.levelno, _DEFAULT_LEVEL)
        name = record.name
        if name.startswith("openpup."):
            name = name[len("openpup.") :]

        line = Text()
        line.append(time.strftime(_DATE_FORMAT, time.localtime(record.created)), style="dim")
        line.append("  ")
        line.append(f"{label:<5}", style=label_style)
        line.append("  ")
        line.append(f"{name:<14}", style="dim")
        line.append(" ")
        line.append(record.getMessage(), style=msg_style)
        if record.exc_info:
            line.append("\n")
            line.append(_EXC_FORMATTER.formatException(record.exc_info), style="dim red")
        return line


def _ensure_renderer() -> None:
    """Start (once) a renderer that drains the global message queue.

    If code-puppy fell back to a plain rich Console (no queue system),
    printing already hits the terminal directly and no renderer is needed.
    """
    global _renderer
    with _renderer_lock:
        if _renderer is not None:
            return
        from code_puppy.tools.common import console

        fallback = getattr(console, "fallback_console", None)
        if fallback is None:  # plain Console -- nothing to drain
            return
        from code_puppy.messaging import (
            SynchronousInteractiveRenderer,
            get_global_queue,
        )

        _renderer = SynchronousInteractiveRenderer(get_global_queue(), fallback)
        _renderer.start()
        atexit.register(_stop_renderer)


def _stop_renderer() -> None:
    global _renderer
    with _renderer_lock:
        if _renderer is not None:
            _renderer.stop()
            _renderer = None


def setup_logging(verbose: bool, handler: Optional[logging.Handler] = None) -> None:
    """Point root logging at code-puppy's rich console and start rendering.

    Args:
        verbose: DEBUG level when True, INFO otherwise.
        handler: Override the sink (tests). Defaults to QueueConsoleHandler.
    """
    sink = handler or QueueConsoleHandler()
    # QueueConsoleHandler composes its own styled output; a format string is
    # only a courtesy for an override handler passed by tests.
    sink.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(sink)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    if handler is None:
        _ensure_renderer()
