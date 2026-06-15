"""Tests for the SQLite-backed config store + one-time .env migration."""

import os

import openpup.config_store as cs
from openpup.config_store import ConfigStore, is_secret_key, known_config_keys


def test_get_set_roundtrip(tmp_path):
    store = ConfigStore(tmp_path / "config.db")
    assert store.get("OPENPUP_SEND_POLICY", "open") == "open"  # default returned
    store.set("OPENPUP_SEND_POLICY", "owner_only")
    assert store.get("OPENPUP_SEND_POLICY") == "owner_only"
    # persists across reopen
    reopened = ConfigStore(tmp_path / "config.db")
    assert reopened.get("OPENPUP_SEND_POLICY") == "owner_only"


def test_set_applies_to_environ_and_clears_cache(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path / "config.db")
    monkeypatch.delenv("OPENPUP_SEND_RATE_PER_MIN", raising=False)
    store.set("OPENPUP_SEND_RATE_PER_MIN", "42")
    assert os.environ["OPENPUP_SEND_RATE_PER_MIN"] == "42"


def test_bool_helpers(tmp_path):
    store = ConfigStore(tmp_path / "config.db")
    store.set_bool("EMAIL_ENABLED", True)
    assert store.get("EMAIL_ENABLED") == "true"
    assert store.get_bool("EMAIL_ENABLED") is True
    store.set_bool("EMAIL_ENABLED", False)
    assert store.get_bool("EMAIL_ENABLED") is False


def test_excluded_keys_not_written_to_environ(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path / "config.db")
    monkeypatch.setenv("PUPPY_KENNEL_ROOT", "/real/kennel")
    # Even if someone stores an excluded key, set() must not clobber the env.
    store._put("PUPPY_KENNEL_ROOT", "/db/kennel")
    store.apply_to_environ()
    assert os.environ["PUPPY_KENNEL_ROOT"] == "/real/kennel"


def test_known_keys_include_settings_aliases_exclude_infra():
    keys = known_config_keys()
    assert "OPENPUP_OWNER_ADDRESSES" in keys
    assert "EMAIL_PASSWORD" in keys
    assert "PUPPY_KENNEL_ROOT" not in keys
    assert "OPENPUP_HOME" not in keys


def test_is_secret_key():
    assert is_secret_key("EMAIL_PASSWORD")
    assert is_secret_key("TELEGRAM_BOT_TOKEN")
    assert is_secret_key("OPENPUP_WEBHOOK_SECRET")
    assert is_secret_key("BOODLETON_API_KEY")
    assert not is_secret_key("OPENPUP_SEND_POLICY")


def test_migration_imports_env_once(tmp_path, monkeypatch):
    # Fake an existing user's .env in a temp cwd.
    env = tmp_path / ".env"
    env.write_text(
        "OPENPUP_OWNER_ADDRESS=telegram:111\n"
        "EMAIL_PASSWORD=secret\n"
        "# a comment\n"
        "BOGUS_UNKNOWN_KEY=ignored\n"
        "OPENPUP_HOME=/should/not/migrate\n"
    )
    monkeypatch.chdir(tmp_path)

    store = ConfigStore(tmp_path / "config.db")
    store.bootstrap()

    data = store.as_dict()
    assert data["OPENPUP_OWNER_ADDRESS"] == "telegram:111"
    assert data["EMAIL_PASSWORD"] == "secret"
    # unknown + infra keys are NOT migrated
    assert "BOGUS_UNKNOWN_KEY" not in data
    assert "OPENPUP_HOME" not in data
    assert store._meta_get("migrated_at")

    # Second bootstrap is a no-op even if the user later sets a value: the
    # migration must not re-import / overwrite.
    store.set("OPENPUP_OWNER_ADDRESS", "telegram:999")
    store.bootstrap()
    assert store.get("OPENPUP_OWNER_ADDRESS") == "telegram:999"


def test_migration_fresh_install_no_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .env here
    store = ConfigStore(tmp_path / "config.db")
    store.bootstrap()
    assert store.as_dict() == {}
    assert store._meta_get("migrated_at")
    assert "fresh install" in (store._meta_get("migration_source") or "")


def test_get_config_store_rebinds_on_home_change(tmp_path, monkeypatch):
    import openpup.config as config_mod

    home_a = tmp_path / "a"
    home_b = tmp_path / "b"
    monkeypatch.setattr(config_mod, "config_home", lambda: home_a)
    cs._store = None
    cs._store_path = None
    store_a = cs.get_config_store()
    assert store_a.path == home_a / "config.db"

    monkeypatch.setattr(config_mod, "config_home", lambda: home_b)
    store_b = cs.get_config_store()
    assert store_b.path == home_b / "config.db"
    assert store_a is not store_b
