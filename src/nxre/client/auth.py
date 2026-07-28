"""Bearer-token authentication against the NX Witness REST API.

NX v4 issues a session bearer token from ``POST /rest/v4/login/sessions``. A local
admin account works fully offline (no Nx Cloud dependency). Tokens have a finite
lifetime, so we track expiry and refresh proactively.

Analogy: the token is a day-pass wristband. We keep it in our pocket, glance at the
printed expiry, and go get a fresh one a little before it lapses — rather than being
turned away at the gate (a 401) mid-task.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

LOGIN_PATH = "/rest/v4/login/sessions"

# Refresh this many seconds before the server-reported expiry, to avoid racing it.
REFRESH_SKEW_S = 30
# Fallback lifetime if the server doesn't tell us one.
DEFAULT_TTL_S = 600


@dataclass
class Token:
    value: str
    expires_at: float  # monotonic epoch seconds

    def is_valid(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return bool(self.value) and now < (self.expires_at - REFRESH_SKEW_S)

    def header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.value}"}


def _extract_token(payload: dict) -> str:
    """NX has used a few field names across builds; accept the common ones."""
    for key in ("token", "id", "value"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    raise AuthError(f"Login response contained no recognizable token field: {sorted(payload)}")


def _extract_ttl(payload: dict) -> int:
    for key in ("expiresInS", "ageS", "expirationS"):
        val = payload.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return int(val)
    return DEFAULT_TTL_S


class AuthError(RuntimeError):
    """Raised when authentication fails."""


async def login(client: httpx.AsyncClient, username: str, password: str) -> Token:
    """Obtain a fresh bearer token. ``client`` must have ``base_url`` set."""
    body = {"username": username, "password": password, "setCookie": False}
    resp = await client.post(LOGIN_PATH, json=body)
    if resp.status_code >= 400:
        raise AuthError(
            f"Login failed for user {username!r}: HTTP {resp.status_code} {resp.text[:200]}"
        )
    payload = resp.json()
    return Token(value=_extract_token(payload), expires_at=time.time() + _extract_ttl(payload))
