"""Regression: OpenPup must use its OWN kennel (absolute path), not share
code-puppy's, and must expand ``~``."""

import os

from openpup.config import get_settings


def test_kennel_root_is_absolute_and_expanded(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("PUPPY_KENNEL_ROOT", "~/.openpup/kennel")
    try:
        s = get_settings()
        val = os.environ["PUPPY_KENNEL_ROOT"]
        assert "~" not in val, "tilde must be expanded"
        assert os.path.isabs(val), "kennel root must be absolute"
        assert str(s.kennel_path) == val
        # And it points at OpenPup's kennel, not code-puppy's default.
        assert val.endswith("/.openpup/kennel")
        assert ".code_puppy" not in val
    finally:
        get_settings.cache_clear()


def test_kennel_root_default_is_openpup(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.delenv("PUPPY_KENNEL_ROOT", raising=False)
    try:
        s = get_settings()
        # default kennel is OpenPup's own, separate from code-puppy
        assert str(s.kennel_path).endswith("/.openpup/kennel")
    finally:
        get_settings.cache_clear()
