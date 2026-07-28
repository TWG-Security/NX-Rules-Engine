"""FastAPI companion service: browser login, a live rules manager, the webhook
receiver, and a small inspection API.

``nxre serve`` runs this. Open ``http://127.0.0.1:8787`` in a browser: sign in with
your NX account, then manage NX event rules right in the page — list them, toggle them
on/off, create new ones, edit them, and delete them. Every change is applied straight to
the NX server over ``/rest/v4`` using your logged-in session.

Analogy: the service is the front desk of the building. Show your NX badge once (log in)
and the desk keeps a day-pass on file; from then on you work the rule book directly at the
counter instead of filing paperwork elsewhere.
"""

from __future__ import annotations

import html
import json
import logging
import time
from typing import Any
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from ruamel.yaml import YAML

from ..client import auth
from ..client.manifest import Manifest
from ..client.nx_client import NxApiError, NxClient
from ..config import NxSystem, Settings
from ..engine.automations import AutomationEngine
from ..engine.bus import EventBus
from ..engine.ingest.webhook import handle_payload
from ..models.automation import Automation
from ..models.rule import NativeRule
from ..rules import repo
from ..session import SessionStore

log = logging.getLogger("nxre.service")
_yaml = YAML(typ="safe")

# TWG Security brand palette (fallback values; see TWGsecurity.com).
_RED = "#C0392B"
_CHARCOAL = "#1A1A1A"

# Used only if the live manifest can't be fetched, so the dropdowns are never empty.
_FALLBACK_EVENTS = [
    "deviceDisconnected", "motion", "generic", "softwareTrigger",
    "networkIssue", "storageFailure", "cameraInput",
]
_FALLBACK_ACTIONS = [
    "writeToLog", "http", "bookmark", "showPopup", "sendEmail",
    "playSound", "deviceOutput",
]


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
        items = data if isinstance(data, list) else [data]
        automations.extend(Automation.from_yaml_obj(item) for item in items)
    return automations


# ---------------------------------------------------------------------------
# HTML rendering — self-contained, no external assets (this may run air-gapped).
# ---------------------------------------------------------------------------
def _page(title: str, body: str, max_width: int = 420) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:flex-start; justify-content:center;
         padding:40px 16px; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         background:{_CHARCOAL}; color:#fff; }}
  .card {{ width:100%; max-width:{max_width}px; background:#242424; border:1px solid #333;
          border-radius:12px; padding:32px; box-shadow:0 10px 40px rgba(0,0,0,.5); }}
  .brand {{ font-weight:800; letter-spacing:.5px; font-size:20px; }}
  .brand span {{ color:{_RED}; }}
  h1 {{ font-size:22px; margin:18px 0 4px; }}
  .muted {{ color:#9aa0a6; font-size:13px; margin:0 0 20px; }}
  label {{ display:block; font-size:13px; color:#c7c7c7; margin:14px 0 6px; }}
  input[type=text], input[type=password], select, textarea {{ width:100%; padding:11px 12px;
          border-radius:8px; border:1px solid #3a3a3a; background:#1b1b1b; color:#fff; font-size:15px; }}
  textarea {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size:13px; min-height:120px; }}
  input:focus, select:focus, textarea:focus {{ outline:none; border-color:{_RED}; }}
  .check {{ display:flex; align-items:center; gap:8px; margin-top:14px; font-size:14px; }}
  .check input {{ width:auto; }}
  button, .btn {{ display:inline-block; padding:11px 16px; border:0; border-radius:8px;
           background:{_RED}; color:#fff; font-size:14px; font-weight:600; cursor:pointer;
           text-decoration:none; text-align:center; }}
  button:hover, .btn:hover {{ filter:brightness(1.08); }}
  .btn.ghost, button.ghost {{ background:#2f2f2f; }}
  .btn.block {{ width:100%; margin-top:22px; }}
  .err {{ background:#3a1c1a; border:1px solid {_RED}; color:#ffb3ab; padding:10px 12px;
         border-radius:8px; font-size:13px; margin-top:14px; }}
  .ok-banner {{ background:#14311f; border:1px solid #2f7d4f; color:#8ff0b0; padding:10px 12px;
         border-radius:8px; font-size:13px; margin-bottom:14px; }}
  .row {{ display:flex; justify-content:space-between; padding:9px 0; border-bottom:1px solid #303030;
         font-size:14px; }}
  .row:last-child {{ border-bottom:0; }}
  .row .k {{ color:#9aa0a6; }} .row .v {{ font-weight:600; }}
  .ok {{ color:#4ade80; }}
  .topbar {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }}
  table {{ width:100%; border-collapse:collapse; margin-top:8px; }}
  th, td {{ text-align:left; padding:9px 8px; border-bottom:1px solid #303030; font-size:13px;
           vertical-align:middle; }}
  th {{ color:#9aa0a6; font-weight:600; }}
  .pill {{ font-size:11px; padding:2px 8px; border-radius:20px; font-weight:700; }}
  .pill.on {{ background:#14311f; color:#8ff0b0; }}
  .pill.off {{ background:#333; color:#aaa; }}
  .acts form {{ display:inline; }}
  .acts a, .acts button {{ padding:6px 10px; font-size:12px; }}
  .small {{ font-size:12px; color:#9aa0a6; }}
  code {{ background:#1b1b1b; padding:2px 6px; border-radius:5px; font-size:12px; }}
  a.link {{ color:#f0a39b; }}
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
  <input id="u" type="text" name="username" autocomplete="username" autofocus required>
  <label for="p">NX password</label>
  <input id="p" name="password" type="password" autocomplete="current-password" required>
  <button class="block" type="submit">Sign in</button>
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
<a class="btn block" href="/rules">Manage rules &rarr;</a>
<form class="logout" method="post" action="/logout">
  <button class="ghost block" type="submit">Sign out</button></form>"""
    return _page("Status — NX Rules Engine", body)


def _rules_page(
    system: str, base_url: str, rules: list[NativeRule], writable: bool,
    notice: str = "", error: str = "",
) -> str:
    banner = f'<div class="ok-banner">{html.escape(notice)}</div>' if notice else ""
    banner += f'<div class="err">{html.escape(error)}</div>' if error else ""
    ro = "" if writable else (
        '<div class="err">This system is marked read-only in config '
        '(<code>writable: false</code>) — changes are disabled.</div>'
    )
    rows = ""
    for r in rules:
        rid = r.id or ""
        state = "on" if r.enabled else "off"
        toggle_label = "Disable" if r.enabled else "Enable"
        acts = f'<a class="btn ghost" href="/rules/{html.escape(rid)}/edit">Edit</a>'
        if writable:
            acts += (
                f'<form method="post" action="/rules/{html.escape(rid)}/toggle">'
                f'<button class="ghost" type="submit">{toggle_label}</button></form>'
                f'<form method="post" action="/rules/{html.escape(rid)}/delete" '
                f'onsubmit="return confirm(\'Delete this rule?\')">'
                f'<button type="submit">Delete</button></form>'
            )
        rows += (
            f'<tr><td><span class="pill {state}">{state.upper()}</span></td>'
            f'<td>{html.escape(r.event_type or "?")}</td>'
            f'<td>{html.escape(r.action_type or "?")}</td>'
            f'<td>{html.escape(r.comment or "")}</td>'
            f'<td class="acts">{acts}</td></tr>'
        )
    if not rows:
        rows = '<tr><td colspan="5" class="small">No rules on the server yet.</td></tr>'
    new_btn = '<a class="btn" href="/rules/new">+ New rule</a>' if writable else ""
    body = f"""
<div class="topbar">
  <h1 style="margin:0">Rules — {html.escape(system)}</h1>
  <a class="link" href="/">&larr; Status</a>
</div>
<p class="muted">{len(rules)} rule(s) on {html.escape(base_url)}. {new_btn}</p>
{banner}{ro}
<table>
  <tr><th>State</th><th>Event</th><th>Action</th><th>Comment</th><th></th></tr>
  {rows}
</table>"""
    return _page("Rules — NX Rules Engine", body, max_width=900)


def _options(values: list[str], selected: str) -> str:
    out = ['<option value=""></option>']
    for v in values:
        sel = " selected" if v == selected else ""
        out.append(f'<option value="{html.escape(v)}"{sel}>{html.escape(v)}</option>')
    return "".join(out)


def _rule_form_page(
    system: str, mode: str, event_types: list[str], action_types: list[str],
    comment: str, enabled: bool, event_json: str, action_json: str,
    event_type: str = "", action_type: str = "", rule_id: str | None = None,
    error: str = "",
) -> str:
    is_edit = mode == "edit"
    action = f"/rules/{html.escape(rule_id)}/update" if is_edit else "/rules/create"
    title = "Edit rule" if is_edit else "New rule"
    checked = " checked" if enabled else ""
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    templates = "" if is_edit else """
<p class="small">Start from a template:
  <a class="link" href="/rules/new?template=disconnect">log on camera disconnect</a> ·
  <a class="link" href="/rules/new?template=webhook">forward events to nxre</a> ·
  <a class="link" href="/rules/new?template=blank">blank</a></p>"""
    body = f"""
<div class="topbar">
  <h1 style="margin:0">{title}</h1>
  <a class="link" href="/rules">&larr; Rules</a>
</div>
{templates}{err}
<form method="post" action="{action}">
  <label for="c">Comment</label>
  <input id="c" type="text" name="comment" value="{html.escape(comment)}"
         placeholder="What does this rule do?">

  <label for="et">Event type</label>
  <select id="et" name="event_type">{_options(event_types, event_type)}</select>
  <label for="ej">Event details (JSON)</label>
  <textarea id="ej" name="event_json">{html.escape(event_json)}</textarea>

  <label for="at">Action type</label>
  <select id="at" name="action_type">{_options(action_types, action_type)}</select>
  <label for="aj">Action details (JSON)</label>
  <textarea id="aj" name="action_json">{html.escape(action_json)}</textarea>

  <div class="check"><input id="en" type="checkbox" name="enabled"{checked}>
    <label for="en" style="margin:0">Enabled</label></div>

  <button class="block" type="submit">{"Save changes" if is_edit else "Create rule"}</button>
  <p class="small">The type you pick above is written into the JSON's <code>type</code> field on save.
     Leave the JSON as <code>{{}}</code> for a minimal rule.</p>
</form>"""
    return _page(f"{title} — NX Rules Engine", body, max_width=640)


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

    def _client() -> NxClient | None:
        token = _valid_token()
        return NxClient(sys_cfg, token=token) if token else None

    async def _manifest_types(client: NxClient) -> tuple[list[str], list[str]]:
        try:
            m = await Manifest.fetch(client)
            return (m.event_types() or _FALLBACK_EVENTS), (m.action_types() or _FALLBACK_ACTIONS)
        except (NxApiError, httpx.HTTPError, ValueError, TypeError):
            return _FALLBACK_EVENTS, _FALLBACK_ACTIONS

    def _template(name: str) -> tuple[str, dict, dict]:
        if name == "disconnect":
            r = repo.scaffold_write_to_log("Log when a camera disconnects")
        elif name == "webhook":
            r = repo.scaffold_webhook_rule(settings.webhook.public_url, "Forward NX events to nxre")
        else:
            return "", {"type": ""}, {"type": ""}
        return r.comment, r.event, r.action

    def _parse_rule_form(raw: str) -> tuple[str, bool, str, str, str, str, dict, dict]:
        """Return (comment, enabled, event_type, action_type, event_json, action_json,
        event_obj, action_obj). Raises ValueError with a friendly message on bad JSON."""
        form = parse_qs(raw, keep_blank_values=True)
        comment = (form.get("comment") or [""])[0]
        enabled = "enabled" in form
        event_type = (form.get("event_type") or [""])[0].strip()
        action_type = (form.get("action_type") or [""])[0].strip()
        event_json = (form.get("event_json") or ["{}"])[0]
        action_json = (form.get("action_json") or ["{}"])[0]
        try:
            event = json.loads(event_json or "{}")
            action = json.loads(action_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from None
        if not isinstance(event, dict) or not isinstance(action, dict):
            raise ValueError(  # noqa: TRY004 — user-facing validation, caller catches ValueError
                'Event and Action must each be a JSON object, e.g. {"type": "..."}.'
            )
        # The dropdown wins for the type, so the two controls can't disagree.
        if event_type:
            event["type"] = event_type
        if action_type:
            action["type"] = action_type
        if not event.get("type") or not action.get("type"):
            raise ValueError("Pick both an event type and an action type.")
        return comment, enabled, event_type, action_type, event_json, action_json, event, action

    # -- browser UI: auth ---------------------------------------------------
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
                base_url=sys_cfg.base_url.rstrip("/"), verify=sys_cfg.verify_tls, timeout=15.0,
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

    # -- browser UI: rules --------------------------------------------------
    @app.get("/rules", response_class=HTMLResponse)
    async def rules_index(notice: str = "", error: str = ""):
        client = _client()
        if client is None:
            return RedirectResponse("/", status_code=303)
        async with client:
            try:
                rules = [NativeRule.from_api(r) for r in await client.get_rules()]
            except NxApiError as exc:
                return HTMLResponse(
                    _rules_page(system, sys_cfg.base_url, [], sys_cfg.writable, error=str(exc))
                )
        return HTMLResponse(
            _rules_page(system, sys_cfg.base_url, rules, sys_cfg.writable, notice=notice, error=error)
        )

    @app.get("/rules/new", response_class=HTMLResponse)
    async def rules_new(template: str = ""):
        client = _client()
        if client is None:
            return RedirectResponse("/", status_code=303)
        async with client:
            ev_types, ac_types = await _manifest_types(client)
        comment, event, action = _template(template or "blank")
        return HTMLResponse(_rule_form_page(
            system, "new", ev_types, ac_types, comment, True,
            json.dumps(event, indent=2), json.dumps(action, indent=2),
            event_type=event.get("type", ""), action_type=action.get("type", ""),
        ))

    @app.post("/rules/create")
    async def rules_create(request: Request):
        if not sys_cfg.writable:
            return RedirectResponse("/rules?error=System+is+read-only", status_code=303)
        client = _client()
        if client is None:
            return RedirectResponse("/", status_code=303)
        raw = (await request.body()).decode("utf-8")
        async with client:
            ev_types, ac_types = await _manifest_types(client)
            try:
                comment, enabled, et, at, ej, aj, event, action = _parse_rule_form(raw)
            except ValueError as exc:
                form = parse_qs(raw, keep_blank_values=True)
                return HTMLResponse(_rule_form_page(
                    system, "new", ev_types, ac_types,
                    (form.get("comment") or [""])[0], "enabled" in form,
                    (form.get("event_json") or ["{}"])[0], (form.get("action_json") or ["{}"])[0],
                    (form.get("event_type") or [""])[0], (form.get("action_type") or [""])[0],
                    error=str(exc),
                ), status_code=400)
            rule = NativeRule(comment=comment, enabled=enabled, event=event, action=action)
            try:
                await client.create_rule(rule.to_api_body())
            except NxApiError as exc:
                return HTMLResponse(_rule_form_page(
                    system, "new", ev_types, ac_types, comment, enabled, ej, aj, et, at,
                    error=f"NX rejected the rule: {exc}",
                ), status_code=400)
        return RedirectResponse("/rules?notice=Rule+created", status_code=303)

    @app.get("/rules/{rule_id}/edit", response_class=HTMLResponse)
    async def rules_edit(rule_id: str):
        client = _client()
        if client is None:
            return RedirectResponse("/", status_code=303)
        async with client:
            ev_types, ac_types = await _manifest_types(client)
            try:
                rule = NativeRule.from_api(await client.get_rule(rule_id))
            except NxApiError as exc:
                return RedirectResponse(f"/rules?error={html.escape(str(exc))}", status_code=303)
        return HTMLResponse(_rule_form_page(
            system, "edit", ev_types, ac_types, rule.comment, rule.enabled,
            json.dumps(rule.event, indent=2), json.dumps(rule.action, indent=2),
            event_type=rule.event_type or "", action_type=rule.action_type or "", rule_id=rule_id,
        ))

    @app.post("/rules/{rule_id}/update")
    async def rules_update(rule_id: str, request: Request):
        if not sys_cfg.writable:
            return RedirectResponse("/rules?error=System+is+read-only", status_code=303)
        client = _client()
        if client is None:
            return RedirectResponse("/", status_code=303)
        raw = (await request.body()).decode("utf-8")
        async with client:
            ev_types, ac_types = await _manifest_types(client)
            try:
                comment, enabled, et, at, ej, aj, event, action = _parse_rule_form(raw)
            except ValueError as exc:
                form = parse_qs(raw, keep_blank_values=True)
                return HTMLResponse(_rule_form_page(
                    system, "edit", ev_types, ac_types,
                    (form.get("comment") or [""])[0], "enabled" in form,
                    (form.get("event_json") or ["{}"])[0], (form.get("action_json") or ["{}"])[0],
                    (form.get("event_type") or [""])[0], (form.get("action_type") or [""])[0],
                    rule_id=rule_id, error=str(exc),
                ), status_code=400)
            rule = NativeRule(comment=comment, enabled=enabled, event=event, action=action)
            try:
                await client.update_rule(rule_id, rule.to_api_body())
            except NxApiError as exc:
                return HTMLResponse(_rule_form_page(
                    system, "edit", ev_types, ac_types, comment, enabled, ej, aj, et, at,
                    rule_id=rule_id, error=f"NX rejected the update: {exc}",
                ), status_code=400)
        return RedirectResponse("/rules?notice=Rule+updated", status_code=303)

    @app.post("/rules/{rule_id}/toggle")
    async def rules_toggle(rule_id: str):
        if not sys_cfg.writable:
            return RedirectResponse("/rules?error=System+is+read-only", status_code=303)
        client = _client()
        if client is None:
            return RedirectResponse("/", status_code=303)
        async with client:
            try:
                rule = NativeRule.from_api(await client.get_rule(rule_id))
                rule.enabled = not rule.enabled
                await client.update_rule(rule_id, rule.to_api_body())
            except NxApiError as exc:
                return RedirectResponse(f"/rules?error={html.escape(str(exc))}", status_code=303)
        state = "enabled" if rule.enabled else "disabled"
        return RedirectResponse(f"/rules?notice=Rule+{state}", status_code=303)

    @app.post("/rules/{rule_id}/delete")
    async def rules_delete(rule_id: str):
        if not sys_cfg.writable:
            return RedirectResponse("/rules?error=System+is+read-only", status_code=303)
        client = _client()
        if client is None:
            return RedirectResponse("/", status_code=303)
        async with client:
            try:
                await client.delete_rule(rule_id)
            except NxApiError as exc:
                return RedirectResponse(f"/rules?error={html.escape(str(exc))}", status_code=303)
        return RedirectResponse("/rules?notice=Rule+deleted", status_code=303)

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
