# Architecture

## The big picture

```
        NX Witness Server (local)
   REST /rest/v4  ▲            │  "Do HTTP Request" action (a native rule nxre provisions)
                  │            ▼
        ┌─────────┴────────────────────────────┐
        │        nxre companion service         │
        │                                       │
        │  client/   auth + async NX REST client│  ← manifests, rules CRUD, event log
        │  models/   NativeRule, Automation     │
        │  secrets   ${secret:*} redact/resolve │  ← keeps creds out of committed YAML
        │  rules/    pull→YAML · validate · diff │  ← native-rules-as-code (Phase 1)
        │            · apply (safe-write policy) │
        │  engine/   bus → automations → actions │  ← Home Assistant-style engine (P2+)
        │  service/  FastAPI: /webhook, /events  │  ← receives NX pushes, inspection API
        └───────────────────────────────────────┘
        CLI (typer)  +  version-controlled  rules/<system>/*.yaml
```

## Why a companion service (not an NX plugin)

The NX **Server Plugin SDK** (`server_plugin_sdk/readme.md`) supports exactly four
integration types — **Analytics, Camera, Storage, Cloud Storage** — all C++ `.so`/`.dll`
libraries loaded by the mediaserver. There is **no in-process slot for a rules engine**.
So `nxre` is a separate process that drives NX over its REST API. This is also the
approach Network Optix steers integrators toward (`nx_open_integrations` samples).

## Event ingestion — three paths, one bus

Everything becomes an `engine.bus.Event` published to a small async `EventBus`.

| Path | Mechanism | Status | Notes |
|---|---|---|---|
| **Webhook** (primary) | Native NX rule with a *Do HTTP Request* action → `POST /webhook/nx` | **Phase 1** | Sub-second, official pattern, no polling. `nxre rules new --webhook` provisions it. |
| **JSON-RPC/WebSocket** | `GET /jsonrpc` subscriptions to REST resources | Phase 2 (stub) | Real push feed per the spec, but the exact subscribe payload must be verified live before we depend on it. See `engine/ingest/jsonrpc_ws.py`. |
| **Event-log poll** | `GET /rest/v4/events/log` | fallback | For backfill/reconciliation, not low-latency automation. |

## Data model

- **`models/rule.NativeRule`** mirrors the NX rule object
  (`{event, action, enabled, schedule[], comment}` + server-managed `id`/`etag`).
  `event`/`action` stay as dicts because their shape is polymorphic per `type`; the
  **manifest** (`client/manifest.py`) is the source of truth for validation.
  `fingerprint()` gives a stable desired-state hash that ignores `id`/`etag` for diffing.
- **`models/automation.Automation`** is the HA-style `trigger[]/condition[]/action[]/mode`
  format. Parsed and carried in Phase 1; conditions + action dispatch go live in Phase 2.

## Secret handling

NX stores action credentials inline in rule JSON. On **pull**, `secrets.redact_secrets`
replaces any `password`/`token`/`auth`-style value with a `${secret:NAME}` placeholder and
stashes the real value in a gitignored `secrets.local.yaml` (mode `0600`). On **load for
apply**, `secrets.resolve_secrets` puts the real values back. Result: rule YAML is safe to
commit and review; secrets live in one locked file.

## Safe-write policy (`rules/diff.py`)

Diffing desired-vs-live produces a `Plan` whose entries each carry a **write class**:

- **SAFE** — enable/disable, comment edits, or creating a `writeToLog` / the webhook rule.
  Applied automatically.
- **GUARDED** — editing/deleting an existing rule, or any credentialed / device-driving
  action. Requires `--apply`.
- **BLOCKED** — any change to a system without `writable: true`. Never applied.

This keeps `nxre apply` safe to run against a busy production site by default.

## Module map

| Path | Responsibility |
|---|---|
| `config.py` | Load sites + safe-write settings; per-site password from env |
| `client/auth.py` | Bearer-token login + expiry tracking |
| `client/nx_client.py` | Async REST client (rules CRUD, manifests, event log, ingress) |
| `client/manifest.py` | Event/action manifest view + disk cache |
| `models/rule.py` · `models/automation.py` | Typed models |
| `secrets.py` | Redact / resolve `${secret:*}` |
| `rules/repo.py` | pull → YAML, load, scaffold, enable/disable |
| `rules/validate.py` | Validate against manifest |
| `rules/diff.py` | Build plan + classify write safety |
| `rules/apply.py` | Execute plan under the policy |
| `engine/bus.py` | Event + async pub/sub |
| `engine/ingest/webhook.py` · `jsonrpc_ws.py` | Ingestion adapters |
| `engine/actions/*` | Action registry + NX-native handlers |
| `engine/automations.py` | Trigger matching + (P2) action dispatch |
| `service/app.py` | FastAPI: `/health`, `/webhook/nx`, `/events/recent` |
| `cli.py` | Typer CLI |
