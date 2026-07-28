"""Async REST client for a single NX Witness site.

Wraps ``httpx.AsyncClient`` with lazy bearer-token auth (auto-refresh + one retry on
401) and typed helpers for the endpoints Phase 1 needs: the event-rules CRUD, the
event/action manifests, the event log, and the generic-event / soft-trigger ingress
used for testing.

All endpoint paths are confirmed against the uploaded ``nxmetaapiv4_2.json`` spec.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..config import NxSystem
from . import auth
from .auth import Token

RULES_PATH = "/rest/v4/events/rules"
EVENT_MANIFEST_PATH = "/rest/v4/events/manifest/events"
ACTION_MANIFEST_PATH = "/rest/v4/events/manifest/actions"
EVENT_LOG_PATH = "/rest/v4/events/log"
GENERIC_EVENT_PATH = "/rest/v4/events/generic"
SOFT_TRIGGER_PATH = "/rest/v4/events/triggers"


class NxApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: str):
        self.status = status
        super().__init__(f"{method} {path} -> HTTP {status}: {body[:300]}")


class NxClient:
    """A live connection to one NX site. Use as an async context manager."""

    def __init__(self, system: NxSystem, *, token: Token | None = None, timeout: float = 15.0):
        self.system = system
        # A caller (e.g. `nxre login`) can hand us a cached user session token. If it
        # lapses and the system also has a service-account password, we re-login with
        # that; otherwise we surface a clear "run `nxre login`" error.
        self._token: Token | None = token
        self._client = httpx.AsyncClient(
            base_url=system.base_url.rstrip("/"),
            verify=system.verify_tls,
            timeout=timeout,
        )

    async def __aenter__(self) -> "NxClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- auth ---------------------------------------------------------------
    async def _ensure_token(self) -> Token:
        if self._token is not None and self._token.is_valid():
            return self._token
        # Token missing or expired: re-authenticate with the service-account password
        # if one is configured; a bare user session with no password can't self-renew.
        password = self.system.resolved_password()
        if password:
            self._token = await auth.login(self._client, self.system.username, password)
            return self._token
        raise auth.AuthError(
            "No valid NX session and no service-account password configured. "
            "Run `nxre login` to authenticate as your NX user."
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        token = await self._ensure_token()
        headers = {**kwargs.pop("headers", {}), **token.header()}
        resp = await self._client.request(method, path, headers=headers, **kwargs)
        if resp.status_code == 401:
            # Token may have lapsed early; force one refresh + retry.
            self._token = None
            token = await self._ensure_token()
            headers = {**kwargs.pop("headers", {}), **token.header()}
            resp = await self._client.request(method, path, headers=headers, **kwargs)
        if resp.status_code >= 400:
            raise NxApiError(method, path, resp.status_code, resp.text)
        if not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        return resp.json() if "json" in ctype else resp.text

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        """NX responses wrap the body in a top-level ``result`` key."""
        if isinstance(payload, dict) and "result" in payload:
            return payload["result"]
        return payload

    # -- rules CRUD ---------------------------------------------------------
    async def get_rules(self) -> list[dict]:
        return list(self._unwrap(await self._request("GET", RULES_PATH)))

    async def get_rule(self, rule_id: str) -> dict:
        return dict(self._unwrap(await self._request("GET", f"{RULES_PATH}/{rule_id}")))

    async def create_rule(self, body: dict) -> dict:
        return dict(self._unwrap(await self._request("POST", RULES_PATH, json=body)))

    async def update_rule(self, rule_id: str, body: dict, *, replace: bool = False) -> dict:
        method = "PUT" if replace else "PATCH"
        return dict(self._unwrap(await self._request(method, f"{RULES_PATH}/{rule_id}", json=body)))

    async def delete_rule(self, rule_id: str) -> None:
        await self._request("DELETE", f"{RULES_PATH}/{rule_id}")

    # -- manifests ----------------------------------------------------------
    async def get_event_manifest(self) -> dict:
        return dict(self._unwrap(await self._request("GET", EVENT_MANIFEST_PATH)))

    async def get_action_manifest(self) -> dict:
        return dict(self._unwrap(await self._request("GET", ACTION_MANIFEST_PATH)))

    # -- event log & ingress (used for testing / the live loop) ------------
    async def get_event_log(self, **params: Any) -> list[dict]:
        clean = {k: v for k, v in params.items() if v is not None}
        return list(self._unwrap(await self._request("GET", EVENT_LOG_PATH, params=clean)))

    async def create_generic_event(self, body: dict) -> Any:
        return self._unwrap(await self._request("POST", GENERIC_EVENT_PATH, json=body))

    async def fire_soft_trigger(self, body: dict) -> Any:
        return self._unwrap(await self._request("POST", SOFT_TRIGGER_PATH, json=body))
