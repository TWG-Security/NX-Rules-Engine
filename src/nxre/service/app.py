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
from pydantic import ValidationError

from .. import autos
from ..client import auth
from ..client.manifest import Manifest
from ..client.nx_client import NxApiError, NxClient
from ..config import NxSystem, Settings
from ..engine.actions.builtin import register_builtin_actions
from ..engine.actions.nx_actions import register_nx_actions_factory
from ..engine.actions.registry import ActionRegistry
from ..engine.automations import AutomationEngine
from ..engine.bus import EventBus
from ..engine.ingest.webhook import handle_payload
from ..models.automation import Action, Automation, Condition, Trigger
from ..models.rule import NativeRule
from ..session import SessionStore

log = logging.getLogger("nxre.service")

# TWG Security brand palette (fallback values; see TWGsecurity.com).
_RED = "#C0392B"
_CHARCOAL = "#1A1A1A"


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
  h2 {{ font-size:16px; margin:24px 0 4px; }}
  h2 .small {{ font-weight:400; }}
  .blk {{ position:relative; border:1px solid #333; border-radius:10px; padding:10px 14px 14px;
         margin:10px 0; background:#1e1e1e; }}
  .blk .del {{ position:absolute; top:8px; right:8px; background:#3a2a2a; padding:3px 9px; font-size:12px; }}
  .hint {{ background:#20262e; border:1px solid #33455a; color:#a9c7e6; padding:10px 12px;
          border-radius:8px; font-size:13px; margin:12px 0; }}
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
<a class="btn block" href="/rules">NX rules &rarr;</a>
<p class="small" style="margin:6px 2px 0">Real NX event rules — they appear in NX and NX runs them
(work even if this service is off).</p>
<a class="btn ghost block" href="/automations">nxre automations (if&nbsp;this&nbsp;then&nbsp;that) &rarr;</a>
<p class="small" style="margin:6px 2px 0">Cross-system logic run by <i>this service</i> — these do
<b>not</b> appear in NX's own rule list.</p>
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


# Field names (from the NX manifest) that pick devices — rendered as camera multi-selects.
_DEVICE_FIELD_NAMES = {
    "deviceIds", "cameraIds", "eventResourceIds", "eventDeviceIds",
    "deviceId", "targetDeviceId", "resourceIds",
}

# Manifest-driven native-rule builder. Every event/action type's fields come straight
# from the live NX manifest, so the editor mirrors NX's own — analytics object type,
# attributes, cameras, HTTP fields, etc. Plain string (JS braces need no escaping).
_RULE_BUILDER_JS = r"""
var EVENTS=[];    // [{id,displayName,fields:[{type,fieldName,displayName}]},...]
var ACTIONS=[];
var CAMERAS=[];   // [{id,name},...]
var DEVICE_FIELDS=[];
var INITIAL={};   // {comment,enabled,event:{type,...},action:{type,...}}
function esc(v){return (v==null?'':String(v)).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function isDeviceField(fn){return DEVICE_FIELDS.indexOf(fn)>=0;}
function byId(list,id){for(var i=0;i<list.length;i++)if(list[i].id===id)return list[i];return null;}

function typeOptions(list,sel){return '<option value="">— choose —</option>'+list.map(function(t){
  return '<option value="'+esc(t.id)+'"'+(t.id===sel?' selected':'')+'>'+esc(t.displayName)+'</option>';
}).join('');}

// Render the fields for a chosen type into `container`, prefilling from `data`.
function renderFields(container, typeObj, data){
  container.innerHTML='';
  if(!typeObj) return;
  typeObj.fields.forEach(function(f){
    var wrap=document.createElement('div'); wrap.className='fld';
    var cur=data?data[f.fieldName]:undefined;
    var label='<label>'+esc(f.displayName)+'</label>';
    if(isDeviceField(f.fieldName)){
      var multi=(f.fieldName.slice(-1)==='s');  // plural → multi-select
      var sel=Array.isArray(cur)?cur:(cur?[cur]:[]);
      var opts=CAMERAS.map(function(c){return '<option value="'+esc(c.id)+'"'+
        (sel.indexOf(c.id)>=0?' selected':'')+'>'+esc(c.name)+'</option>';}).join('');
      wrap.innerHTML=label+'<select data-fn="'+esc(f.fieldName)+'" data-kind="device"'+
        (multi?' multiple size="4"':'')+'>'+(multi?'':'<option value=""></option>')+opts+'</select>';
    } else {
      var v=(cur==null)?'':(typeof cur==='object'?JSON.stringify(cur):cur);
      wrap.innerHTML=label+'<input type="text" data-fn="'+esc(f.fieldName)+'" data-kind="text" value="'+
        esc(v)+'" placeholder="'+esc(f.displayName)+'">';
    }
    container.appendChild(wrap);
  });
  if(!typeObj.fields.length) container.innerHTML='<p class="small">No extra fields for this type.</p>';
}

function collectFields(container){
  var o={};
  [].forEach.call(container.querySelectorAll('[data-fn]'),function(el){
    var fn=el.getAttribute('data-fn');
    if(el.getAttribute('data-kind')==='device'){
      if(el.multiple){var ids=[].filter.call(el.options,function(o){return o.selected;})
        .map(function(o){return o.value;}); if(ids.length)o[fn]=ids;}
      else if(el.value)o[fn]=el.value;
    } else { var v=(el.value||'').trim(); if(v!=='')o[fn]=v; }
  });
  return o;
}

function onEventType(){var t=byId(EVENTS,document.getElementById('etype').value);
  renderFields(document.getElementById('efields'), t, (INITIAL.event||{}));}
function onActionType(){var t=byId(ACTIONS,document.getElementById('atype').value);
  renderFields(document.getElementById('afields'), t, (INITIAL.action||{}));}

function serialize(){
  var etype=document.getElementById('etype').value;
  var atype=document.getElementById('atype').value;
  if(!etype){alert('Pick an event (the “When”).');return false;}
  if(!atype){alert('Pick an action (the “Do”).');return false;}
  var event=collectFields(document.getElementById('efields')); event.type=etype;
  var action=collectFields(document.getElementById('afields')); action.type=atype;
  var payload={comment:document.getElementById('c').value.trim(),
    enabled:document.getElementById('en').checked, event:event, action:action};
  document.getElementById('payload').value=JSON.stringify(payload);
  return true;
}

function boot(){
  document.getElementById('etype').innerHTML=typeOptions(EVENTS,(INITIAL.event||{}).type||'');
  document.getElementById('atype').innerHTML=typeOptions(ACTIONS,(INITIAL.action||{}).type||'');
  document.getElementById('etype').onchange=onEventType;
  document.getElementById('atype').onchange=onActionType;
  document.getElementById('c').value=INITIAL.comment||'';
  document.getElementById('en').checked=INITIAL.enabled!==false;
  onEventType(); onActionType();
}
"""


def _rule_builder_page(
    system: str, is_edit: bool, action_url: str, events: list, actions: list, cameras: list,
    initial: dict, error: str = "",
) -> str:
    title = "Edit NX rule" if is_edit else "New NX rule"
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    head = f"""
<div class="topbar">
  <h1 style="margin:0">{title}</h1>
  <a class="link" href="/rules">&larr; NX rules</a>
</div>
<p class="muted">This creates a <b>native NX rule</b> — it appears in NX and NX runs it.
Fields mirror NX's own editor, pulled from your server.</p>
{err}
<form method="post" action="{action_url}" onsubmit="return serialize()">
  <input type="hidden" name="payload" id="payload">
  <label for="c">Name / comment</label>
  <input id="c" type="text" placeholder="What does this rule do?">

  <h2>When <span class="small">— the event</span></h2>
  <label for="etype">Event</label>
  <select id="etype"></select>
  <div id="efields"></div>

  <h2>Do <span class="small">— the action</span></h2>
  <label for="atype">Action</label>
  <select id="atype"></select>
  <div id="afields"></div>

  <div class="check"><input id="en" type="checkbox" checked>
    <label for="en" style="margin:0">Enabled</label></div>
  <button class="block" type="submit">{"Save changes" if is_edit else "Create rule"}</button>
</form>
<script>{_RULE_BUILDER_JS}
EVENTS={json.dumps(events)};
ACTIONS={json.dumps(actions)};
CAMERAS={json.dumps(cameras)};
DEVICE_FIELDS={json.dumps(sorted(_DEVICE_FIELD_NAMES))};
INITIAL={json.dumps(initial)};
boot();
</script>"""
    return _page(f"{title} — NX Rules Engine", head, max_width=680)


# ---------------------------------------------------------------------------
# HA-style automation builder ("if this then that")
# ---------------------------------------------------------------------------
_COMMON_EVENTS = [
    "analyticsObject", "analytics", "motion", "deviceDisconnected", "generic",
    "softwareTrigger", "networkIssue", "storageFailure", "cameraInput",
]


def _automations_list_page(
    system: str, items: list[Automation], notice: str = "", error: str = "",
) -> str:
    banner = f'<div class="ok-banner">{html.escape(notice)}</div>' if notice else ""
    banner += f'<div class="err">{html.escape(error)}</div>' if error else ""
    rows = ""
    for a in items:
        aid = a.id or ""
        state = "on" if a.enabled else "off"
        toggle = "Disable" if a.enabled else "Enable"
        when = ", ".join(
            t.model_dump().get("event_type", t.model_dump().get("platform", "?")) for t in a.trigger
        ) or "—"
        then = ", ".join(x.kind for x in a.action) or "—"
        rows += (
            f'<tr><td>{html.escape(a.alias)}</td>'
            f'<td><span class="pill {state}">{state.upper()}</span></td>'
            f'<td>{html.escape(when)}</td><td>{html.escape(then)}</td>'
            f'<td class="acts">'
            f'<a class="btn ghost" href="/automations/{html.escape(aid)}/edit">Edit</a>'
            f'<form method="post" action="/automations/{html.escape(aid)}/toggle">'
            f'<button class="ghost" type="submit">{toggle}</button></form>'
            f'<form method="post" action="/automations/{html.escape(aid)}/delete" '
            f'onsubmit="return confirm(\'Delete this automation?\')">'
            f'<button type="submit">Delete</button></form></td></tr>'
        )
    if not rows:
        rows = '<tr><td colspan="5" class="small">No automations yet — create one.</td></tr>'
    body = f"""
<div class="topbar">
  <h1 style="margin:0">Automations — {html.escape(system)}</h1>
  <a class="link" href="/">&larr; Status</a>
</div>
<p class="muted">If-this-then-that rules run by nxre. <a class="btn" href="/automations/new">+ New automation</a></p>
{banner}
<div class="hint">These run in <b>this service</b> — they do <b>not</b> appear in NX's own rule list
(for rules that show in NX, use <a class="link" href="/rules">NX rules</a>). Automations react to
NX events sent here, so make sure an NX rule forwards events to <code>/webhook/nx</code>.</div>
<table>
  <tr><th>Name</th><th>State</th><th>When</th><th>Then do</th><th></th></tr>
  {rows}
</table>"""
    return _page("Automations — NX Rules Engine", body, max_width=900)


# Self-contained builder script (plain string, NOT an f-string — avoids brace escaping).
_FRIENDLY_EVENTS = {
    "analyticsObject": "An object is detected (person, vehicle, …)",
    "analytics": "An analytics event occurs",
    "motion": "Motion is detected",
    "deviceDisconnected": "A camera goes offline",
    "cameraInput": "A camera input signal fires",
    "analyticsSdkEvent": "An analytics event occurs",
    "generic": "A generic / custom event arrives",
    "softwareTrigger": "A soft trigger is pressed",
    "networkIssue": "A network issue occurs",
    "storageFailure": "A storage failure occurs",
}


def _friendly_event(t: str) -> str:
    return _FRIENDLY_EVENTS.get(t, t)


# Fully-visual builder — every input is a dropdown or simple field, no code anywhere.
# Plain string (NOT an f-string) so JS braces need no escaping.
_BUILDER_JS = r"""
var EVENTS=[];      // [[value,label],...] event types
var CAMERAS=[];     // [{id,name},...] devices
var INITIAL={};
function esc(v){return (v==null?'':String(v)).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function h(s){var t=document.createElement('template');t.innerHTML=s.trim();return t.content.firstChild;}
function rm(b){b.closest('.row').remove();}
function val(r,f){var e=r.querySelector('[data-f='+f+']');return e?(e.value||'').trim():'';}
function evOpts(sel){return '<option value="">— pick an event —</option>'+EVENTS.map(function(e){
  return '<option value="'+esc(e[0])+'"'+(e[0]===sel?' selected':'')+'>'+esc(e[1])+'</option>';}).join('');}
function camOpts(useId,sel){if(!CAMERAS.length)return '<option value="">(no cameras found)</option>';
  return CAMERAS.map(function(c){var v=useId?c.id:c.name;
  return '<option value="'+esc(v)+'"'+(v===sel?' selected':'')+'>'+esc(c.name)+'</option>';}).join('');}

function addTrigger(d){d=d||{};
  var r=h('<div class="row"><span class="lead">When</span>'+
    '<select class="grow" data-f="event_type">'+evOpts(d.event_type)+'</select>'+
    '<input type="text" data-f="object_type" value="'+esc(d.object_type)+'" '+
      'placeholder="type e.g. person" style="display:none;max-width:150px">'+
    '<span class="lead">on</span>'+
    '<select data-f="scope"><option value="any">any camera</option>'+
      '<option value="one">a specific camera</option></select>'+
    '<select class="grow" data-f="source" style="display:none">'+camOpts(false,d.source)+'</select>'+
    '<button type="button" class="x" onclick="rm(this)">remove</button></div>');
  var scope=r.querySelector('[data-f=scope]'), cam=r.querySelector('[data-f=source]');
  var etype=r.querySelector('[data-f=event_type]'), obj=r.querySelector('[data-f=object_type]');
  function upd(){cam.style.display=scope.value==='one'?'':'none';}
  // The object-type filter only makes sense for analytics/object events.
  function updObj(){obj.style.display=(etype.value==='analyticsObject'||etype.value==='analytics')?'':'none';}
  scope.value=d.source?'one':'any'; upd(); scope.onchange=upd;
  updObj(); etype.onchange=updObj;
  document.getElementById('triggers').appendChild(r);}

function addCond(d){d=d||{};
  var field='caption';
  if(d.condition==='time_between')field='time';
  else if(d.condition==='day_of_week')field='dow';
  else if(d.condition==='source_contains')field='camera';
  var r=h('<div class="row"><select data-f="field">'+
    '<option value="time">Time of day is</option><option value="dow">Day of week is</option>'+
    '<option value="camera">Camera is</option><option value="caption">Caption contains</option>'+
    '</select><span class="body grow"></span>'+
    '<button type="button" class="x" onclick="rm(this)">remove</button></div>');
  var body=r.querySelector('.body'), fsel=r.querySelector('[data-f=field]');
  function render(f){
    if(f==='time')body.innerHTML='<span class="lead">between</span> '+
      '<input type="time" data-f="after" value="'+esc(d.after||'22:00')+'"> <span class="lead">and</span> '+
      '<input type="time" data-f="before" value="'+esc(d.before||'06:00')+'">';
    else if(f==='dow'){var ds=(d.days||'').toLowerCase();
      body.innerHTML='<span class="lead">is</span> '+['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(function(x){
      return '<label style="font-size:12px;margin-right:7px"><input type="checkbox" class="dow" value="'+
        x.toLowerCase()+'"'+(ds.indexOf(x.toLowerCase())>=0?' checked':'')+'> '+x+'</label>';}).join('');}
    else if(f==='camera')body.innerHTML='<span class="lead">is</span> '+
      '<select class="grow" data-f="camval">'+camOpts(false,d.value)+'</select>';
    else body.innerHTML='<span class="lead">contains</span> '+
      '<input type="text" class="grow" data-f="capval" value="'+esc(d.value)+'" placeholder="e.g. intrusion">';}
  fsel.value=field; render(field); fsel.onchange=function(){render(fsel.value);};
  document.getElementById('conds').appendChild(r);}

function addAct(d){d=d||{};
  var r=h('<div class="row"><span class="lead">Do</span><select data-f="kind">'+
    '<option value="nx_mobile_notification">Notify the mobile app (push)</option>'+
    '<option value="nx_generic_event">Send an NX notification (in-VMS only)</option>'+
    '<option value="http">Call a webhook / URL</option>'+
    '<option value="nx_device_output">Trigger a camera output (relay)</option>'+
    '<option value="nx_bookmark">Bookmark the moment on a camera</option>'+
    '<option value="nx_soft_trigger">Fire an NX soft trigger</option>'+
    '<option value="log">Write to the log</option></select>'+
    '<span class="body grow"></span>'+
    '<button type="button" class="x" onclick="rm(this)">remove</button></div>');
  var body=r.querySelector('.body'), ksel=r.querySelector('[data-f=kind]');
  function render(k){
    if(k==='nx_mobile_notification')body.innerHTML='<input type="text" class="grow" data-f="title" value="'+
      esc(d.title)+'" placeholder="Push title, e.g. Person at Front Door"> '+
      '<input type="text" class="grow" data-f="body" value="'+esc(d.body)+'" placeholder="Body (optional)">';
    else if(k==='nx_generic_event')body.innerHTML='<input type="text" class="grow" data-f="caption" value="'+
      esc(d.caption)+'" placeholder="Notification text, e.g. Motion at {source}">';
    else if(k==='http')body.innerHTML='<select data-f="method"><option'+(d.method!=='GET'?' selected':'')+
      '>POST</option><option'+(d.method==='GET'?' selected':'')+'>GET</option></select> '+
      '<input type="text" class="grow" data-f="url" value="'+esc(d.url)+'" placeholder="https://...">';
    else if(k==='nx_device_output')body.innerHTML='<span class="lead">on</span> '+
      '<select class="grow" data-f="device_id">'+camOpts(true,d.device_id)+'</select>';
    else if(k==='nx_bookmark')body.innerHTML='<span class="lead">on</span> '+
      '<select class="grow" data-f="device_id">'+camOpts(true,d.device_id)+'</select> '+
      '<input type="text" data-f="bname" value="'+esc(d.name)+'" placeholder="bookmark name">';
    else if(k==='nx_soft_trigger')body.innerHTML='<input type="text" class="grow" data-f="trigger_id" value="'+
      esc(d.trigger_id)+'" placeholder="soft trigger id">';
    else body.innerHTML='<input type="text" class="grow" data-f="message" value="'+
      esc(d.message)+'" placeholder="log message">';}
  ksel.value=d.kind||'nx_mobile_notification'; render(ksel.value); ksel.onchange=function(){render(ksel.value);};
  document.getElementById('acts').appendChild(r);}

function kids(id){return [].slice.call(document.getElementById(id).children);}
function collTriggers(){return kids('triggers').map(function(r){
  var o={platform:'nx_event',event_type:val(r,'event_type')};
  if(r.querySelector('[data-f=scope]').value==='one'){var s=val(r,'source');if(s)o.source=s;}
  var ot=val(r,'object_type'); if(ot)o.object_type=ot;
  return o;}).filter(function(o){return o.event_type;});}
function collConds(){return kids('conds').map(function(r){
  var f=r.querySelector('[data-f=field]').value;
  if(f==='time')return {condition:'time_between',after:val(r,'after'),before:val(r,'before')};
  if(f==='dow')return {condition:'day_of_week',days:[].filter.call(r.querySelectorAll('.dow'),
    function(c){return c.checked;}).map(function(c){return c.value;}).join(',')};
  if(f==='camera')return {condition:'source_contains',value:val(r,'camval')};
  return {condition:'caption_contains',value:val(r,'capval')};});}
function collActs(){return kids('acts').map(function(r){
  var k=r.querySelector('[data-f=kind]').value,o={kind:k};
  if(k==='nx_mobile_notification'){o.title=val(r,'title');o.body=val(r,'body');}
  else if(k==='nx_generic_event')o.caption=val(r,'caption');
  else if(k==='http'){o.method=val(r,'method');o.url=val(r,'url');}
  else if(k==='nx_device_output')o.device_id=val(r,'device_id');
  else if(k==='nx_bookmark'){o.device_id=val(r,'device_id');o.name=val(r,'bname');}
  else if(k==='nx_soft_trigger')o.trigger_id=val(r,'trigger_id');
  else o.message=val(r,'message');
  return o;});}

function serialize(){
  var alias=document.getElementById('alias').value.trim();
  if(!alias){alert('Give the automation a name.');return false;}
  var triggers=collTriggers();
  if(!triggers.length){alert('Add at least one trigger (the “When”) and pick its event.');return false;}
  var acts=collActs();
  if(!acts.length){alert('Add at least one action (the “Then do”).');return false;}
  var payload={id:INITIAL.id||null, alias:alias,
    enabled:document.getElementById('enabled').checked, mode:'single',
    condition_match:document.getElementById('match').value,
    trigger:triggers, condition:collConds(), action:acts};
  document.getElementById('payload').value=JSON.stringify(payload);
  return true;}

function boot(){
  if(INITIAL && (INITIAL.trigger||INITIAL.action)){
    document.getElementById('alias').value=INITIAL.alias||'';
    document.getElementById('enabled').checked=INITIAL.enabled!==false;
    document.getElementById('match').value=(INITIAL.condition_match==='any')?'any':'all';
    (INITIAL.trigger||[]).forEach(addTrigger);
    (INITIAL.condition||[]).forEach(addCond);
    (INITIAL.action||[]).forEach(addAct);
  } else { addTrigger(); addAct(); }
}
"""


def _automation_builder_page(
    system: str, initial: dict, action_url: str, is_edit: bool,
    events: list, cameras: list, error: str = "",
) -> str:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    head = f"""
<div class="topbar">
  <h1 style="margin:0">{"Edit automation" if is_edit else "New automation"}</h1>
  <a class="link" href="/automations">&larr; Automations</a>
</div>
<p class="muted">Build it by picking from menus — no code anywhere. Stack as many layers as you like.</p>
{err}
<form method="post" action="{action_url}" onsubmit="return serialize()">
  <input type="hidden" name="payload" id="payload">
  <label for="alias">Name</label>
  <input id="alias" type="text" placeholder="e.g. After-hours motion at the loading dock">
  <div class="check"><input id="enabled" type="checkbox" checked>
    <label for="enabled" style="margin:0">Enabled</label></div>

  <h2>When <span class="small">— the event that starts it (any of these)</span></h2>
  <div id="triggers"></div>
  <button type="button" class="ghost" onclick="addTrigger()">+ Add trigger</button>

  <h2>And if <span class="small">(optional)</span></h2>
  <p class="small" style="margin:2px 0 6px">Match
    <select id="match" style="width:auto;display:inline-block;padding:4px 8px">
      <option value="all">all (AND)</option><option value="any">any (OR)</option>
    </select> of these — your “if this and this and this”.</p>
  <div id="conds"></div>
  <button type="button" class="ghost" onclick="addCond()">+ Add condition</button>

  <h2>Then do <span class="small">— actions run top to bottom</span></h2>
  <div id="acts"></div>
  <button type="button" class="ghost" onclick="addAct()">+ Add action</button>

  <button class="block" type="submit">{"Save changes" if is_edit else "Create automation"}</button>
</form>
<script>{_BUILDER_JS}
EVENTS = {json.dumps(events)};
CAMERAS = {json.dumps(cameras)};
INITIAL = {json.dumps(initial)};
boot();
</script>"""
    return _page(f"{'Edit' if is_edit else 'New'} automation — NX Rules Engine", head, max_width=760)


def create_app(settings: Settings, system: str | None = None) -> FastAPI:
    system = system or settings.default_system
    sys_cfg: NxSystem = settings.system_or_local(system)
    store = SessionStore()
    app = FastAPI(title="nxre", version="0.1.0")

    bus = EventBus()

    def _valid_token():
        token = store.load(system)
        return token if (token and token.is_valid()) else None

    def _client() -> NxClient | None:
        token = _valid_token()
        return NxClient(sys_cfg, token=token) if token else None

    # Wire the action registry so automations actually DO something: provider-agnostic
    # actions (log, http) plus NX actions that resolve the current login at fire time.
    registry = ActionRegistry()
    register_builtin_actions(registry)
    register_nx_actions_factory(registry, _client)

    engine = AutomationEngine(autos.load_all(settings, system), registry=registry)
    engine.attach(bus)

    def _reload_engine() -> None:
        engine.set_automations(autos.load_all(settings, system))

    app.state.bus = bus
    app.state.engine = engine
    app.state.system = system
    app.state.session_store = store

    async def _rule_builder_data() -> tuple[list, list, list]:
        """Manifest event/action items + cameras for the native-rule builder dropdowns."""
        events: list = []
        actions: list = []
        cameras: list = []
        client = _client()
        if client is not None:
            async with client:
                try:
                    m = await Manifest.fetch(client)
                    events, actions = m.event_items(), m.action_items()
                except (NxApiError, httpx.HTTPError, ValueError, TypeError):
                    pass
                try:
                    cameras = [
                        {"id": d.get("id", ""), "name": d.get("name") or d.get("id", "")}
                        for d in await client.get_devices() if d.get("id")
                    ]
                except (NxApiError, httpx.HTTPError, ValueError, TypeError):
                    pass
        return events, actions, cameras

    def _rule_payload(raw: str) -> tuple[str, bool, dict, dict]:
        """Parse the builder's JSON payload → (comment, enabled, event, action)."""
        form = parse_qs(raw, keep_blank_values=True)
        data = json.loads((form.get("payload") or ["{}"])[0])
        event = data.get("event") if isinstance(data.get("event"), dict) else {}
        action = data.get("action") if isinstance(data.get("action"), dict) else {}
        if not event.get("type") or not action.get("type"):
            raise ValueError("Pick both an event and an action.")
        return (data.get("comment") or "").strip(), bool(data.get("enabled", True)), event, action

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
    async def rules_new():
        if _valid_token() is None:
            return RedirectResponse("/", status_code=303)
        events, actions, cameras = await _rule_builder_data()
        return HTMLResponse(_rule_builder_page(
            system, False, "/rules/create", events, actions, cameras, {}))

    @app.post("/rules/create")
    async def rules_create(request: Request):
        if not sys_cfg.writable:
            return RedirectResponse("/rules?error=System+is+read-only", status_code=303)
        client = _client()
        if client is None:
            return RedirectResponse("/", status_code=303)
        raw = (await request.body()).decode("utf-8")
        try:
            comment, enabled, event, action = _rule_payload(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            events, actions, cameras = await _rule_builder_data()
            initial = json.loads((parse_qs(raw).get("payload") or ["{}"])[0] or "{}")
            return HTMLResponse(_rule_builder_page(
                system, False, "/rules/create", events, actions, cameras, initial,
                error=str(exc)), status_code=400)
        rule = NativeRule(comment=comment, enabled=enabled, event=event, action=action)
        async with client:
            try:
                await client.create_rule(rule.to_api_body())
            except NxApiError as exc:
                events, actions, cameras = await _rule_builder_data()
                return HTMLResponse(_rule_builder_page(
                    system, False, "/rules/create", events, actions, cameras,
                    {"comment": comment, "enabled": enabled, "event": event, "action": action},
                    error=f"NX rejected the rule: {exc}"), status_code=400)
        return RedirectResponse("/rules?notice=Rule+created", status_code=303)

    @app.get("/rules/{rule_id}/edit", response_class=HTMLResponse)
    async def rules_edit(rule_id: str):
        client = _client()
        if client is None:
            return RedirectResponse("/", status_code=303)
        async with client:
            try:
                rule = NativeRule.from_api(await client.get_rule(rule_id))
            except NxApiError as exc:
                return RedirectResponse(f"/rules?error={html.escape(str(exc))}", status_code=303)
        events, actions, cameras = await _rule_builder_data()
        initial = {"comment": rule.comment, "enabled": rule.enabled,
                   "event": rule.event, "action": rule.action}
        return HTMLResponse(_rule_builder_page(
            system, True, f"/rules/{rule_id}/update", events, actions, cameras, initial))

    @app.post("/rules/{rule_id}/update")
    async def rules_update(rule_id: str, request: Request):
        if not sys_cfg.writable:
            return RedirectResponse("/rules?error=System+is+read-only", status_code=303)
        client = _client()
        if client is None:
            return RedirectResponse("/", status_code=303)
        raw = (await request.body()).decode("utf-8")
        try:
            comment, enabled, event, action = _rule_payload(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            events, actions, cameras = await _rule_builder_data()
            initial = json.loads((parse_qs(raw).get("payload") or ["{}"])[0] or "{}")
            return HTMLResponse(_rule_builder_page(
                system, True, f"/rules/{rule_id}/update", events, actions, cameras, initial,
                error=str(exc)), status_code=400)
        rule = NativeRule(comment=comment, enabled=enabled, event=event, action=action)
        async with client:
            try:
                await client.update_rule(rule_id, rule.to_api_body())
            except NxApiError as exc:
                events, actions, cameras = await _rule_builder_data()
                return HTMLResponse(_rule_builder_page(
                    system, True, f"/rules/{rule_id}/update", events, actions, cameras,
                    {"comment": comment, "enabled": enabled, "event": event, "action": action},
                    error=f"NX rejected the update: {exc}"), status_code=400)
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

    # -- browser UI: automations (if-this-then-that) ------------------------
    def _build_automation(data: dict) -> Automation:
        triggers = [Trigger(**t) for t in data.get("trigger", []) if isinstance(t, dict)]
        conds = [Condition(**c) for c in data.get("condition", []) if isinstance(c, dict)]
        acts = [Action(**a) for a in data.get("action", []) if isinstance(a, dict)]
        mode = data.get("mode", "single")
        if mode not in ("single", "restart", "queued", "parallel"):
            mode = "single"
        match = data.get("condition_match")
        return Automation(
            id=(data.get("id") or None),
            alias=(data.get("alias") or "").strip() or "unnamed automation",
            enabled=bool(data.get("enabled", True)), mode=mode,
            condition_match=(match if match in ("all", "any") else "all"),
            trigger=triggers, condition=conds, action=acts,
        )

    async def _builder_data() -> tuple[list, list]:
        """Live event types + cameras for the builder dropdowns (fallbacks if offline)."""
        events = [[e, _friendly_event(e)] for e in _COMMON_EVENTS]
        cameras: list[dict] = []
        client = _client()
        if client is not None:
            async with client:
                try:
                    types = (await Manifest.fetch(client)).event_types()
                    if types:
                        events = [[e, _friendly_event(e)] for e in types]
                except (NxApiError, httpx.HTTPError, ValueError, TypeError):
                    pass
                try:
                    cameras = [
                        {"id": d.get("id", ""), "name": d.get("name") or d.get("id", "")}
                        for d in await client.get_devices() if d.get("id")
                    ]
                except (NxApiError, httpx.HTTPError, ValueError, TypeError):
                    pass
        return events, cameras

    @app.get("/automations", response_class=HTMLResponse)
    async def automations_index(notice: str = "", error: str = ""):
        if _valid_token() is None:
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(
            _automations_list_page(system, autos.load_all(settings, system), notice, error)
        )

    @app.get("/automations/new", response_class=HTMLResponse)
    async def automations_new():
        if _valid_token() is None:
            return RedirectResponse("/", status_code=303)
        events, cameras = await _builder_data()
        return HTMLResponse(
            _automation_builder_page(system, {}, "/automations/save", False, events, cameras)
        )

    @app.get("/automations/{auto_id}/edit", response_class=HTMLResponse)
    async def automations_edit(auto_id: str):
        if _valid_token() is None:
            return RedirectResponse("/", status_code=303)
        auto = autos.get(settings, system, auto_id)
        if auto is None:
            return RedirectResponse("/automations?error=Automation+not+found", status_code=303)
        events, cameras = await _builder_data()
        return HTMLResponse(_automation_builder_page(
            system, auto.model_dump(exclude_none=True), "/automations/save", True, events, cameras))

    @app.post("/automations/save")
    async def automations_save(request: Request):
        if _valid_token() is None:
            return RedirectResponse("/", status_code=303)
        form = parse_qs((await request.body()).decode("utf-8"))
        try:
            data = json.loads((form.get("payload") or ["{}"])[0])
        except json.JSONDecodeError:
            return RedirectResponse("/automations?error=Could+not+read+the+form", status_code=303)
        is_edit = bool(data.get("id"))
        try:
            auto = _build_automation(data)
            if not auto.trigger or not auto.action:
                raise ValueError("Add at least one trigger and one action.")
        except (ValidationError, ValueError) as exc:
            events, cameras = await _builder_data()
            return HTMLResponse(
                _automation_builder_page(
                    system, data, "/automations/save", is_edit, events, cameras, error=str(exc)),
                status_code=400,
            )
        autos.save(settings, system, auto)
        _reload_engine()
        return RedirectResponse("/automations?notice=Automation+saved", status_code=303)

    @app.post("/automations/{auto_id}/toggle")
    async def automations_toggle(auto_id: str):
        if _valid_token() is None:
            return RedirectResponse("/", status_code=303)
        auto = autos.get(settings, system, auto_id)
        if auto is not None:
            autos.set_enabled(settings, system, auto_id, not auto.enabled)
            _reload_engine()
        return RedirectResponse("/automations?notice=Automation+updated", status_code=303)

    @app.post("/automations/{auto_id}/delete")
    async def automations_delete(auto_id: str):
        if _valid_token() is None:
            return RedirectResponse("/", status_code=303)
        autos.delete(settings, system, auto_id)
        _reload_engine()
        return RedirectResponse("/automations?notice=Automation+deleted", status_code=303)

    # -- health & webhook ---------------------------------------------------
    @app.get("/health")
    async def health() -> dict[str, Any]:
        token = _valid_token()
        return {
            "status": "ok",
            "system": system,
            "authenticated": token is not None,
            "authenticated_user": store.username(system) if token else None,
            "automations": len(engine.automations),
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
