# NX Rules Engine (`nxre`)

**A Home Assistant-style, version-controlled rules & automation engine for NX Witness (Network Optix) VMS.**

NX's built-in event-rules engine is opaque, un-versioned, edited one rule at a time in a
GUI, and stores credentials in plaintext. `nxre` is a **bolt-on companion service** that
runs *local to the NX server* and gives you:

1. **Native NX rules** — build, edit, enable/disable, and delete NX's own event rules
   from a **visual, no-code editor in the browser** whose fields come straight from your
   server's manifest (so it mirrors NX's own editor: analytics object type + attributes,
   multi-camera pickers, HTTP action, etc.). These are real NX rules — they show up in NX
   and NX runs them. Also available as version-controlled YAML via the CLI (pull/diff/apply,
   with embedded credentials auto-redacted).
2. **Home Assistant-style automations** — a real `trigger → condition → action` engine
   for the logic NX can't express, built with a **fully visual, no-code builder** in the
   browser (When / And if / Then do). Every field is a dropdown or simple box — pick your
   event, pick a camera from the live list, stack conditions (match all/any), and choose
   actions: send an NX notification, call any URL (webhook), trigger a camera output relay,
   bookmark the moment on a camera, fire a soft trigger, or write to the log.

> **Why a service, not an NX "plugin"?** The NX Server Plugin SDK only supports
> *analytics / camera / storage* integrations (C++) — there is no in-process slot for a
> rules engine. So `nxre` installs as a co-located service (systemd or Docker) that drives
> NX over its REST API (`/rest/v4`) and receives events via a webhook. It *installs* like a
> plugin; it *runs* alongside the mediaserver. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Install

Full guide: **[`docs/INSTALL.md`](docs/INSTALL.md)**. The short version:

### Developer / workstation
```bash
git clone <this-repo> && cd NX-Rules-Engine
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'

cp nxre.config.example.yaml nxre.config.yaml   # then edit hosts
nxre login                 # log in as your own NX user (prompts; caches the token)
nxre rules pull            # you're off
```

### On the NX server (systemd) — the "boom done" path
```bash
sudo installer/install.sh          # installs, assumes NX at 127.0.0.1:7001, starts the service
```
Then open **http://127.0.0.1:8787** in a browser and sign in with your NX account. From there:
- **Automations (if-this-then-that)** — build `When → And if → Then do` workflows visually.
- **Native NX rules** — list, create, edit, enable/disable, delete NX's own event rules.

Automations react to NX events forwarded to the service, so create the one-click
**"forward events to nxre"** NX rule (Rules → New → template) to feed the engine.
Re-running the installer after `git pull` updates in place (clean venv rebuild + restart).

### Docker
```bash
docker build -t nxre -f installer/Dockerfile .
docker run -d --name nxre --network host nxre
# open http://127.0.0.1:8787 and log in
```

---

## Configure

There's nothing to configure for the common case: on the NX box, `installer/install.sh`
drops a default config that assumes the mediaserver is at `https://127.0.0.1:7001`, and
you log in from the **web page** (`http://127.0.0.1:8787`) with your NX account.

Two ways to log in, both take your NX username/password and cache **only** the bearer
token (`~/.nxre/session.json`, `0600` — the service uses `/opt/nxre/session.json`); the
password is never written to disk:
- **Web** — open the service page and sign in. Best for the `serve` companion.
- **CLI** — `nxre login` (prompts), for the `rules` commands. `nxre logout` clears it.

To point at a different host or add more sites, copy `nxre.config.example.yaml` →
`nxre.config.yaml` (gitignored) and edit:

```yaml
default_system: TWG
webhook: { public_url: http://127.0.0.1:8787 }
systems:
  TWG:
    base_url: https://127.0.0.1:7001
    username: ""          # blank → log in interactively as yourself
    verify_tls: false     # NX ships a self-signed cert
    writable: true        # only writable systems accept ANY write
```

For **unattended** startup (systemd/Docker, no one to type a password), use a service
account instead: set `username` to it and pass its password via the
`NXRE__<SYSTEM>__PASSWORD` env var. A live login session always takes precedence; the
service-account password is the fallback when there's no valid cached token.

---

## Usage

```bash
nxre serve                      # companion service + web login at http://127.0.0.1:8787
nxre login                      # (CLI alternative) authenticate as your NX user
nxre logout [--all]             # discard the cached session token(s)
nxre rules pull                 # live rules -> rules/<system>/*.yaml (secrets redacted)
nxre rules list                 # table of local desired rules
nxre rules show <id>            # one rule
nxre rules diff                 # desired-vs-live plan (with a write-class per change)
nxre rules apply                # SAFE changes auto-apply; GUARDED needs --apply
nxre rules apply --apply        # also execute GUARDED changes
nxre rules new --action writeToLog   # scaffold a safe demo rule
nxre rules new --webhook             # scaffold the event-ingestion webhook rule
nxre rules enable <id> / disable <id>
nxre validate                   # check rules against the cached manifest
```

### Safe-write policy
Every planned change is classified so nothing surprising happens on a live site:

| Class | What | Applied |
|---|---|---|
| **SAFE** | enable/disable, comment edits, new `writeToLog` or the webhook rule | automatically |
| **GUARDED** | editing/deleting an existing rule, or any credentialed / device-driving action | only with `--apply` |
| **BLOCKED** | any change to a system not marked `writable` | never |

---

## Test
```bash
pip install -e '.[dev]'
pytest            # 32 tests, all NX I/O mocked — no live server needed
```

## Project layout & roadmap
See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md).

_Built by TWG Security. Licensed MPL-2.0 (matching the NX SDK)._
