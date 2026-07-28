"""Persisted NX login sessions.

``nxre login`` authenticates as a real NX user and caches **only** the resulting
bearer token here — never the password. Later commands (and ``nxre serve``) reuse
that token until it expires, then prompt for a fresh login.

Analogy: logging in gets you a wristband (the token). We keep the wristband in a
locked drawer (a ``0600`` file), reuse it at the gate until it stops scanning, and
only then go back to the desk to get a new one. The desk never keeps your ID.

The file is keyed by system name, so one machine can hold live sessions for several
NX sites at once. It lives outside the repo (``~/.nxre/session.json`` by default,
overridable with ``NXRE_SESSION_FILE``) so tokens are never committed.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .client.auth import Token

SESSION_FILE_ENV = "NXRE_SESSION_FILE"
DEFAULT_SESSION_PATH = Path.home() / ".nxre" / "session.json"


def default_session_path() -> Path:
    env = os.environ.get(SESSION_FILE_ENV)
    return Path(env) if env else DEFAULT_SESSION_PATH


class SessionStore:
    """Read/write cached bearer tokens, keyed by NX system name."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path else default_session_path()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        # Owner-only, and set it on the temp file *before* it becomes the real one.
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, self.path)

    def load(self, system: str) -> Token | None:
        """Return the cached token for ``system`` (even if expired), or None."""
        entry = self._read().get(system)
        if not isinstance(entry, dict):
            return None
        value = entry.get("token")
        expires_at = entry.get("expires_at")
        if not isinstance(value, str) or not value:
            return None
        if not isinstance(expires_at, (int, float)):
            return None
        return Token(value=value, expires_at=float(expires_at))

    def username(self, system: str) -> str | None:
        """The NX username last used to log in to ``system`` (for prompt defaults)."""
        entry = self._read().get(system)
        return entry.get("username") if isinstance(entry, dict) else None

    def save(self, system: str, token: Token, username: str) -> None:
        data = self._read()
        data[system] = {
            "username": username,
            "token": token.value,
            "expires_at": token.expires_at,
        }
        self._write(data)

    def clear(self, system: str | None = None) -> bool:
        """Drop one system's session, or all of them when ``system`` is None.

        Returns True if anything was actually removed.
        """
        if system is None:
            if self.path.exists():
                self.path.unlink()
                return True
            return False
        data = self._read()
        if system in data:
            del data[system]
            if data:
                self._write(data)
            elif self.path.exists():
                self.path.unlink()
            return True
        return False
