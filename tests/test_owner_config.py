"""Tests for multi-platform owner address config."""

import os
from unittest.mock import patch

from openpup.config import Settings

# Keys that load_dotenv(override=True) may have injected into os.environ from
# the real .env file. We must mask them so unit tests stay isolated.
_OWNER_KEYS = ("OPENPUP_OWNER_ADDRESS", "OPENPUP_OWNER_ADDRESSES")


def _settings(**kw):
    clean_env = {k: v for k, v in os.environ.items() if k not in _OWNER_KEYS}
    with patch.dict(os.environ, clean_env, clear=True):
        return Settings(_env_file=None, **kw)


def test_owner_addresses_combines_primary_and_extras():
    s = _settings(
        OPENPUP_OWNER_ADDRESS="telegram:111",
        OPENPUP_OWNER_ADDRESSES="telegram:111,sms:+15559876543",
    )
    assert s.owner_addresses == ["telegram:111", "sms:+15559876543"]


def test_owner_addresses_primary_only():
    s = _settings(OPENPUP_OWNER_ADDRESS="telegram:111")
    assert s.owner_addresses == ["telegram:111"]


def test_owner_addresses_extras_without_primary():
    s = _settings(OPENPUP_OWNER_ADDRESSES="sms:+15551112222")
    assert s.owner_addresses == ["sms:+15551112222"]


def test_owner_for_platform():
    s = _settings(
        OPENPUP_OWNER_ADDRESS="telegram:111",
        OPENPUP_OWNER_ADDRESSES="sms:+15559876543",
    )
    assert s.owner_for_platform("sms") == "sms:+15559876543"
    assert s.owner_for_platform("telegram") == "telegram:111"
    assert s.owner_for_platform("discord") is None


def test_owner_none():
    s = _settings()
    assert s.owner_addresses == []
    assert s.owner_for_platform("sms") is None
