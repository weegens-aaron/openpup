"""Tests for openpup.logging_setup (rich-console logging sink)."""

import logging

from openpup.logging_setup import NOISY_LOGGERS, QueueConsoleHandler, setup_logging


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _setup(verbose: bool) -> _CaptureHandler:
    h = _CaptureHandler()
    setup_logging(verbose, handler=h)
    return h


def test_openpup_info_passes_and_noisy_info_is_muzzled():
    h = _setup(verbose=False)
    logging.getLogger("openpup.runtime").info("useful")
    for name in NOISY_LOGGERS:
        logging.getLogger(name).info("spam")
    assert [r.getMessage() for r in h.records] == ["useful"]


def test_noisy_warnings_still_bark():
    h = _setup(verbose=False)
    logging.getLogger("httpx").warning("actually important")
    assert [r.getMessage() for r in h.records] == ["actually important"]


def test_verbose_enables_debug_but_keeps_muzzle():
    h = _setup(verbose=True)
    logging.getLogger("openpup.runtime").debug("crumbs")
    logging.getLogger("httpx").debug("frame spam")
    assert [r.getMessage() for r in h.records] == ["crumbs"]


def test_setup_is_idempotent_no_duplicate_handlers():
    _setup(verbose=False)
    h = _setup(verbose=False)
    assert logging.getLogger().handlers == [h]


def _record(name, msg, level=logging.INFO):
    return logging.LogRecord(name, level, __file__, 1, msg, args=(), exc_info=None)


def test_render_strips_openpup_prefix_and_keeps_message():
    handler = QueueConsoleHandler()
    line = handler._render(_record("openpup.registry", "Registered adapter: discord"))
    plain = line.plain
    assert "openpup." not in plain  # noisy namespace prefix dropped
    assert "registry" in plain
    assert "info" in plain  # level badge
    assert plain.endswith("Registered adapter: discord")


def test_render_keeps_brackets_literal_not_markup():
    handler = QueueConsoleHandler()
    line = handler._render(_record("openpup.runtime", "Enabled: ['discord', 'sms']"))
    # Rendered as Text spans, so brackets survive verbatim (no markup blowup).
    assert "['discord', 'sms']" in line.plain


def test_render_level_badges():
    handler = QueueConsoleHandler()
    assert "warn" in handler._render(_record("openpup.x", "m", logging.WARNING)).plain
    assert "error" in handler._render(_record("openpup.x", "m", logging.ERROR)).plain
