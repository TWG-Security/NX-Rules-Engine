"""FastAPI companion service: a browser login + status page, the webhook receiver,
and a small inspection API.

``nxre serve`` runs this. Open ``http://127.0.0.1:8787`` in a browser: if you haven't
authenticated yet you get a login page; sign in with your NX account and you land on a
status page confirming the connection. Under the hood it also receives NX "Do HTTP
Request" pushes at ``/webhook/nx`` and exposes ``/events/recent`` and ``/health``.

Analogy: the service is the front desk of the building. Walk up (open the page), show
your NX badge once (log in), and the desk keeps a day-pass on file so you're not asked
again until it expires. The password is shown to the desk and immediately forgotten;
only the day-pass (bearer token) is kept, in a locked drawer.
"""

from __future__ import annotations

import html
import logging
import time
from typing import Any
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from ruamel.yaml import YAML

from ..client import auth
from ..config import NxSystem, Settings
from ..engine.automations import AutomationEngine
from ..engine.bus import EventBus
from ..engine.ingest.webhook import handle_payload
from ..models.automation import Automation
from ..session import SessionStore

log = logging.getLogger("nxre.service")
_yaml = YAML(typ="safe")

# TWG Security brand palette (fallback values; see TWGsecurity.com).
_RED = "#C0392B"
_CHARCOAL = "#1A1A1A"


def load_automations(settings: Settings, system: str) -> list[Automation]:
    """Load HA-style automations for a system from ``<automations_dir>/<system>/*.yaml``."""
    directory = settings.automations_dir / system
    if not directory.exists():
        return []
    automations: list[Automation] = []
    for path in sorted(directory.glob("*.yaml")):
        with open(path, encoding="utf-8") as fh:
            data = _yaml.load(fh)
        if not data:
            continue
        # a file may hold a single automation or a list of them
        items = data if isinstance(data, list) else [data]
        automations.extend(Automation.from_yaml_obj(item) for item in items)
    return automations


# ---------------------------------------------------------------------------
# HTML rendering — self-contained, no external assets (this may run air-gapped).
# ---------------------------------------------------------------------------
def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         background:{_CHARCOAL}; color:#fff; }}
  .card {{ width:100%; max-width:420px; background:#242424; border:1px solid #333;
          border-radius:12px; padding:32px; box-shadow:0 10px 40px rgba(0,0,0,.5); }}
  .brand {{ font-weight:800; letter-spacing:.5px; font-size:20px; }}
  .brand span {{ color:{_RED}; }}
  h1 {{ font-size:22px; margin:18px 0 4px; }}
  .muted {{ color:#9aa0a6; font-size:13px; margin:0 0 20px; }}
  label {{ display:block; font-size:13px; color:#c7c7c7; margin:14px 0 6px; }}
  input {{ width:100%; padding:11px 12px; border-radius:8px; border:1px solid #3a3a3a;
          background:#1b1b1b; color:#fff; font-size:15px; }}
  input:focus {{ outline:none; border-color:{_RED}; }}
  button {{ width:100%; margin-top:22px; padding:12px; border:0; border-radius:8px;
           background:{_RED}; color:#fff; font-size:15px; font-weight:600; cursor:pointer; }}
  button:hover {{ filter:brightness(1.08); }}
  .err {{ background:#3a1c1a; border:1px solid {_RED}; color:#ffb3ab; padding:10px 12px;
         border-radius:8px; font-size:13px; margin-top:14px; }}
  .row {{ display:flex; justify-content:space-between; padding:9px 0; border-bottom:1px solid #303030;
         font-size:14px; }}
  .row:last-child {{ border-bottom:0; }}
  .row .k {{ color:#9aa0a6; }} .row .v {{ font-weight:600; }}
  .ok {{ color:#4ade80; }}
  .logout {{ margin-top:22px; }}
  .logout button {{ background:#2f2f2f; }}
  code {{ background:#1b1b1b; padding:2px 6px; border-radius:5px; font-size:12px; }}
</style></head><body><div class="card">
<div class="brand">TWG <span>Security</span></div>
{body}
</div></body></html>"""


def _login_page(system: str, base_url: str, error: str | None = None) -> str:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    body = f"""
<h1>NX Rules Engine</h1>
<p class="muted">Sign in to <b>{html.escape(system)}</b> — {html.escape(base_url)}</p>
<form method="post" action="/login">
  <label for="u">NX username</label>
  <input id="u" name="username" autocomplete="username" autofocus required>
  <label for="p">NX password</label>
  <input id="p" name="password" type="password" autocomplete="current-password" required>
  <button type="submit">Sign in</button>
  {err}
</form>"""
    return _page("Sign in — NX Rules Engine", body)


def _status_page(system: str, base_url: str, user: str, expires_in_min: int, events: int) -> str:
    body = f"""
<h1 class="ok">&#10003; Connected</h1>
<p class="muted">The companion service is running and authenticated.</p>
<div class="row"><span class="k">System</span><span class="v">{html.escape(system)}</span></div>
<div class="row"><span class="k">NX server</span><span class="v">{html.escape(base_url)}</span></div>
<div class="row"><span class="k">Signed in as</span><span class="v">{html.escape(user)}</span></div>
<div class="row"><span class="k">Session valid</span><span class="v">~{expires_in_min} min</span></div>
<div class="row"><span class="k">Events received</span><span class="v">{events}</span></div>
<p class="muted" style="margin-top:18px">Point NX "Do HTTP Request" actions at
<code>/webhook/nx</code> on this host.</p>
<form class="logout" method="post" action="/logout"><button type="submit">Sign out</button></form>"""
    return _page("Status — NX Rules Engine", body)


def create_app(settings: Settings, system: str | None = None) -> FastAPI:
    system = system or settings.default_system
    sys_cfg: NxSystem = settings.system_or_local(system)
    store = SessionStore()
    app = FastAPI(title="nxre", version="0.1.0")

    bus = EventBus()
    automations = load_automations(settings, system)
    engine = AutomationEngine(automations)
    engine.attach(bus)

    app.state.bus = bus
    app.state.engine = engine
    app.state.system = system
    app.state.session_store = store

    def _valid_token():
        token = store.load(system)
        return token if (token and token.is_valid()) else None

    # -- browser UI ---------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        token = _valid_token()
        if token is None:
            return HTMLResponse(_login_page(system, sys_cfg.base_url))
        user = store.username(system) or sys_cfg.username or "(unknown)"
        remaining = max(0, int(token.expires_at - time.time())) // 60
        return HTMLResponse(_status_page(system, sys_cfg.base_url, user, remaining, len(bus.recent)))

    @app.post("/login")
    async def login(request: Request):
        form = parse_qs((await request.body()).decode("utf-8"))
        username = (form.get("username") or [""])[0].strip()
        password = (form.get("password") or [""])[0]
        if not username or not password:
            return HTMLResponse(
                _login_page(system, sys_cfg.base_url, "Enter both username and password."),
                status_code=400,
            )
        try:
            async with httpx.AsyncClient(
                base_url=sys_cfg.base_url.rstrip("/"),
                verify=sys_cfg.verify_tls,
                timeout=15.0,
            ) as client:
                token = await auth.login(client, username, password)
        except (auth.AuthError, httpx.HTTPError) as exc:
            log.warning("web login failed for %s: %s", username, exc)
            msg = "Login failed — check the username and password." if isinstance(
                exc, auth.AuthError
            ) else f"Could not reach NX at {sys_cfg.base_url}."
            return HTMLResponse(_login_page(system, sys_cfg.base_url, msg), status_code=401)
        store.save(system, token, username)
        log.info("web login OK for %s on %s", username, system)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout():
        store.clear(system)
        return RedirectResponse("/", status_code=303)

    # -- health & webhook ---------------------------------------------------
    @app.get("/health")
    async def health() -> dict[str, Any]:
        token = _valid_token()
        return {
            "status": "ok",
            "system": system,
            "authenticated": token is not None,
            "authenticated_user": store.username(system) if token else None,
            "automations": len(automations),
            "events_seen": len(bus.recent),
        }

    @app.post("/webhook/nx")
    async def webhook_nx(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 — NX may send form/plain; fall back gracefully
            payload = {"description": (await request.body()).decode("utf-8", "replace")}
        event = await handle_payload(bus, payload if isinstance(payload, dict) else {"raw": payload})
        log.info("webhook event received: %s / %s", event.type, event.source)
        return {"ok": True, "type": event.type}

    @app.get("/events/recent")
    async def events_recent(limit: int = 50) -> list[dict[str, Any]]:
        events = list(bus.recent)[-limit:]
        return [
            {
                "type": e.type,
                "source": e.source,
                "caption": e.caption,
                "description": e.description,
                "received_at": e.received_at,
            }
            for e in events
        ]

    return app
