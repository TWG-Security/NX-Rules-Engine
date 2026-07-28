# Roadmap

## Phase 1 — Native rules as code ✅ (this release)
- Async NX REST client with bearer auth + refresh.
- `rules pull` → versioned YAML, secrets redacted to `${secret:*}`.
- Manifest-driven validation.
- `diff` / `apply` with the SAFE / GUARDED / BLOCKED write-class policy.
- `rules new` scaffolds (`writeToLog`, webhook ingestion rule); `enable` / `disable`.
- Companion service (`serve`): `/webhook/nx` receiver + `/events/recent` + `/health`.
- Engine skeleton (event bus, automation model, action registry) — the foundation.
- 32 mocked tests, no live server required.

## Phase 2 — The automation engine
- Full Home Assistant-style `trigger / condition / action / mode` execution.
- Condition evaluation (time, state, template, and/or/not).
- Verify + implement the JSON-RPC/WebSocket subscription (`engine/ingest/jsonrpc_ws.py`)
  against a live server; add the transaction-bus firehose for richer triggers.
- Jinja-style templating in action fields (like HA).

## Phase 3 — Custom integrations
- Action kinds beyond NX: **ConnectWise** ticket, **HubSpot**, email / SMS / Slack.
- PTZ presets, multi-step sequences, delays, retries.
- These plug into `engine/actions/registry.py` — no engine changes.

## Phase 4 — Multi-site
- Manage all TWG sites (`Bethel_Church`, `MedEvac`, `SecTV`, `TWG`) from one repo.
- Per-site overlays / shared rule templates; bulk diff & apply.

## Phase 5 — Web UI
- FastAPI-served rule/automation browser + editor + live event log.

## Phase 6 — Packaging polish
- Signed installer, auto-discovery of the local server, a proper secrets vault backend,
  health/metrics endpoints, log rotation.

## Verified-live checklist (carry-over from Phase 1 research)
- [ ] Exact JSON-RPC `/jsonrpc` subscribe payload.
- [ ] Bearer token TTL / refresh cadence in practice.
- [ ] Server accepts a *Do HTTP Request* rule pointing at `localhost:<port>`.
