"""A tiny, comment-preserving .env editor backing the config menus.

OpenPup is configured via environment variables (pydantic-settings), so the
TUI's job is to read/update a ``.env`` file. This editor keeps existing
comments and ordering, updates keys in place, and appends new keys at the end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional


class EnvStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lines: List[str] = []
        self._index: Dict[str, int] = {}
        self.load()

    # ---- io --------------------------------------------------------------
    def load(self) -> None:
        self._lines = []
        self._index = {}
        if self.path.exists():
            self._lines = self.path.read_text().splitlines()
        else:
            # Seed from .env.example if present, else start empty.
            example = self.path.parent / ".env.example"
            if example.exists():
                self._lines = example.read_text().splitlines()
        self._reindex()

    def _reindex(self) -> None:
        self._index = {}
        for i, line in enumerate(self._lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            self._index[key] = i

    def save(self) -> None:
        self.path.write_text("\n".join(self._lines) + "\n")
        self._reindex()

    # ---- accessors -------------------------------------------------------
    def get(self, key: str, default: str = "") -> str:
        idx = self._index.get(key)
        if idx is None:
            return default
        _, _, value = self._lines[idx].partition("=")
        return value.strip()

    def set(self, key: str, value: str) -> None:
        line = f"{key}={value}"
        idx = self._index.get(key)
        if idx is None:
            self._lines.append(line)
        else:
            self._lines[idx] = line
        self._reindex()

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get(key, "true" if default else "false").strip().lower()
        return raw in ("1", "true", "yes", "on")

    def set_bool(self, key: str, value: bool) -> None:
        self.set(key, "true" if value else "false")

    def as_dict(self) -> Dict[str, str]:
        return {k: self.get(k) for k in self._index}


def default_env_path(explicit: Optional[Path] = None) -> Path:
    """Resolve the .env path: explicit > ./.env in CWD."""
    return explicit or (Path.cwd() / ".env")
