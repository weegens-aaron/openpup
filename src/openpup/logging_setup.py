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
from typing import Optional

#: Third-party loggers that bark on every HTTP request. WARNING-only, always.
NOISY_LOGGERS = ("httpx", "httpcore", "hpack", "h2")

_LEVEL_STYLES = {
    logging.DEBUG: "dim",
    logging.WARNING: "yellow",
    logging.ERROR: "bold red",
    logging.CRITICAL: "bold white on red",
}

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"

_renderer = None
_renderer_lock = threading.Lock()


class QueueConsoleHandler(logging.Handler):
    """A logging.Handler that prints through code-puppy's rich console.

    Uses ``rich.text.Text`` (not markup) so log messages containing
    ``[brackets]`` render literally instead of exploding as rich markup.
    A re-entrancy guard drops logs emitted *while rendering a log* so a
    chatty renderer can't feed back into itself.
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
            from rich.text import Text

            style = _LEVEL_STYLES.get(record.levelno)
            console.print(Text(self.format(record), style=style or ""))
        except Exception:
            self.handleError(record)
        finally:
            self._reentry.active = False


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
    sink.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(sink)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    if handler is None:
        _ensure_renderer()
