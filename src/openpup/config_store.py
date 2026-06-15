"""SQLite-backed configuration store.

OpenPup's config used to live only in a ``.env`` file. That meant every change
(adding an owner SMS number, rotating a token, ...) was a hand-edit of a flat
file followed by a full process restart. Gross.

Config now lives in a SQLite database at ``config_home()/config.db`` so it can
be read and written at runtime. On first run we perform a **one-time migration**
that imports an existing user's ``.env`` into the database, after which the DB
is the source of truth.

Design notes
------------
* The store mirrors :class:`openpup.tui.env_store.EnvStore`'s accessor surface
  (``get``/``set``/``get_bool``/``set_bool``/``as_dict``/``save``/``load``) so
  the setup wizard and TUI menus persist to SQLite without any other changes.
* Keys are the same uppercase env-var names that :class:`openpup.config.Settings`
  already uses as aliases. ``get_settings()`` injects stored values into
  ``os.environ`` before constructing ``Settings``, so a value stored here is
  read exactly as an env var would be -- no changes to the Settings model.
* Writes apply *live*: ``set`` updates ``os.environ`` and clears the
  ``get_settings`` cache, so the next read reflects the change without a
  restart. (Already-running platform adapters still need a restart to pick up
  new credentials -- that's a connection lifecycle thing, not a config thing.)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set

logger = logging.getLogger("openpup.config_store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS config_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Infra/bootstrap env vars computed at import time. Storing these would pin
# absolute paths and fight monkeypatching in tests, so they never enter the
# store and are never written back to the environment from it.
_EXCLUDED_KEYS: Set[str] = frozenset(
    {
        "OPENPUP_HOME",
        "PUPPY_KENNEL_ROOT",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
    }
)

_TRUE = {"1", "true", "yes", "on"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def known_config_keys() -> Set[str]:
    """The config keys OpenPup recognizes: every Settings field alias.

    This is what "all config" means -- bounded to the actual Settings model so
    the store never accumulates unrelated environment junk.
    """
    from openpup.config import Settings

    out = {(f.alias or name) for name, f in Settings.model_fields.items()}
    return out - _EXCLUDED_KEYS


def is_secret_key(key: str) -> bool:
    """Whether a key holds a credential that should be masked when displayed."""
    upper = key.upper()
    return any(token in upper for token in ("PASSWORD", "TOKEN", "SECRET")) or upper.endswith(
        "_KEY"
    )


class ConfigStore:
    """SQLite-backed config; a drop-in for EnvStore's accessor surface."""

    def __init__(self, path: Optional[Path] = None) -> None:
        from openpup.config import config_home

        self.path = Path(path) if path is not None else (config_home() / "config.db")
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # ---- low-level writes (no env / cache side effects) ------------------
    def _put(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO config(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, value, _now()),
            )

    def _meta_get(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM config_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def _meta_set(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO config_meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ---- EnvStore-compatible accessor surface ----------------------------
    def get(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        """Persist a config value and apply it live (env + settings cache)."""
        value = str(value)
        self._put(key, value)
        if key not in _EXCLUDED_KEYS:
            os.environ[key] = value
        self._invalidate_settings()

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get(key, "true" if default else "false").strip().lower()
        return raw in _TRUE

    def set_bool(self, key: str, value: bool) -> None:
        self.set(key, "true" if value else "false")

    def delete(self, key: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM config WHERE key = ?", (key,))
        os.environ.pop(key, None)
        self._invalidate_settings()

    def as_dict(self) -> Dict[str, str]:
        rows = self._conn.execute("SELECT key, value FROM config").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def save(self) -> None:
        """No-op: writes are immediate. Kept for EnvStore interface parity."""

    def load(self) -> None:
        """No-op: state lives in SQLite. Kept for EnvStore interface parity."""

    # ---- environment + migration -----------------------------------------
    def apply_to_environ(self, override: bool = True) -> None:
        """Push stored config into ``os.environ`` so Settings reads the DB."""
        for key, value in self.as_dict().items():
            if key in _EXCLUDED_KEYS:
                continue
            if override or key not in os.environ:
                os.environ[key] = value

    def bootstrap(self) -> None:
        """Run the one-time ``.env`` migration (idempotent)."""
        if self._meta_get("migrated_at"):
            return
        try:
            self._migrate_from_env()
        except Exception:  # never let migration break startup
            logger.exception("config migration from .env failed")
        finally:
            self._meta_set("migrated_at", _now())

    def _migrate_from_env(self) -> None:
        from openpup.tui.env_store import EnvStore, default_env_path

        env_path = default_env_path()
        if not env_path.exists():
            self._meta_set("migration_source", "none (fresh install)")
            return
        old = EnvStore(env_path).as_dict()
        allowed = known_config_keys()
        existing = set(self.as_dict())
        imported = 0
        for key, value in old.items():
            if key in _EXCLUDED_KEYS or key not in allowed or key in existing:
                continue
            if value is None or value == "":
                continue
            self._put(key, value)
            imported += 1
        self._meta_set("migration_source", str(env_path))
        self._meta_set("migration_imported", str(imported))
        logger.info(
            "Migrated %d config value(s) from %s into %s", imported, env_path, self.path
        )

    def _invalidate_settings(self) -> None:
        try:
            from openpup.config import get_settings

            get_settings.cache_clear()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Process-wide singleton (keyed to the active config_home)
# --------------------------------------------------------------------------
_store_lock = threading.Lock()
_store: Optional[ConfigStore] = None
_store_path: Optional[Path] = None


def get_config_store() -> ConfigStore:
    """Return the process-wide ConfigStore for the current config home.

    Rebuilds if ``config_home()`` changed (e.g. a test monkeypatched it), so
    test isolation via ``OPENPUP_HOME`` keeps working.
    """
    global _store, _store_path
    from openpup.config import config_home

    want = config_home() / "config.db"
    with _store_lock:
        if _store is None or _store_path != want:
            _store = ConfigStore(want)
            _store_path = want
        return _store


def set_config(key: str, value: str) -> None:
    """Set one config value in the store (and apply it live)."""
    get_config_store().set(key, value)
