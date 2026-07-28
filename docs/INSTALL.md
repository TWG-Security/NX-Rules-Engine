# Installing `nxre`

`nxre` runs as a **companion service co-located with the NX Witness mediaserver** (or on
any host that can reach it over HTTPS). It talks to NX over `/rest/v4` and receives live
events on a webhook. Three install paths are supported.

---

## 0. Prerequisites — an NX account

`nxre` authenticates with a **local** NX account (no Nx Cloud dependency; works offline).
You need an account with permission to read/write event rules (**Administrator**, or the
minimum role your policy allows). Note the server URL, usually `https://<host>:7001`.

There are two ways to authenticate — pick per how you run `nxre`:

### A. Interactive login (recommended for people)
Just use **your own NX user**. Create the config (no password in it), then log in:
```bash
cp nxre.config.example.yaml nxre.config.yaml
$EDITOR nxre.config.yaml          # set base_url + writable per site; leave username blank
nxre login                        # prompts for your NX username + password
```
`nxre login` authenticates and caches **only the bearer token** to `~/.nxre/session.json`
(`0600`). Every later command — and `nxre serve` — reuses it until it expires, then asks
again. Your password is never written to disk. `nxre logout` (or `--all`) clears it.
Override the location with `NXRE_SESSION_FILE` if you want the token elsewhere.

### B. Service account (for unattended systemd/Docker startup)
A background service can't stop to prompt, so give it a dedicated login:
1. In the NX Desktop client: **System Administration → Users → New User**.
2. Type **Local**, name it e.g. `nxre-service`, give it a strong password, grant the role.
3. Put that name in the config's `username`, and pass the password via env (never in YAML):
```bash
export NXRE__TWG__PASSWORD='the-password'      # NXRE__<SYSTEM_NAME_UPPER>__PASSWORD
```

A live login session always takes precedence; the service-account password is only used
as a fallback when there's no valid cached token.

---

## 1. Developer / workstation install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'           # editable install + test deps

nxre login                        # authenticate as your NX user
nxre rules pull                   # verify connectivity
pytest                            # run the test suite
```

Requires Python 3.11+.

---

## 2. Production install on the NX server (systemd)

`installer/install.sh` copies the project into `/opt/nxre`, builds a virtualenv, and
installs a systemd unit. Run it as root on the NX box:

```bash
sudo installer/install.sh
```

What it does:
- creates `/opt/nxre` and a venv at `/opt/nxre/.venv`
- `pip install .` into that venv
- installs `installer/nxre.service` to `/etc/systemd/system/nxre.service`
- prints next steps

Then provide config + credential and start it. A systemd-managed service restarts with
no TTY, so it uses the **service-account** path (option B above) — set its password in the
unit's environment:
```bash
sudo cp nxre.config.yaml /opt/nxre/nxre.config.yaml
sudoedit /etc/systemd/system/nxre.service      # set NXRE__<SYSTEM>__PASSWORD in [Service] Environment
sudo systemctl daemon-reload
sudo systemctl enable --now nxre
systemctl status nxre
journalctl -u nxre -f                          # watch the live event log
```

The service runs `nxre serve` — the webhook receiver + inspection API on the port from
your config (default `8787`).

---

## 3. Docker

```bash
docker build -t nxre -f installer/Dockerfile .

docker run -d --name nxre \
  --network host \
  -e NXRE__TWG__PASSWORD='the-password' \
  -v "$PWD/nxre.config.yaml:/app/nxre.config.yaml:ro" \
  -v "$PWD/rules:/app/rules" \
  nxre
```

`--network host` lets the container reach `https://127.0.0.1:7001` and lets NX reach the
webhook on `127.0.0.1:8787`. Adjust `public_url` in the config to match how NX addresses
the container.

---

## 4. Wire up the live event feed (optional, proves the loop)

```bash
nxre rules new --webhook          # scaffold a "Do HTTP Request -> nxre" rule (SAFE)
nxre rules apply                  # push it (auto-applies; it's SAFE)
# now trigger a generic event / camera event on the site and watch:
curl -s localhost:8787/events/recent | jq .
```

To remove it later: delete the file under `rules/<system>/` and `nxre rules apply --apply --prune`.

---

## Upgrading
```bash
git pull
# dev:        pip install -e '.[dev]'
# systemd:    sudo installer/install.sh && sudo systemctl restart nxre
# docker:     docker build -t nxre -f installer/Dockerfile . && docker restart nxre
```

## Security notes
- `nxre.config.yaml` and `secrets.local.*` are **gitignored** — keep them that way.
- The secret store (`secrets.local.yaml`) is written `0600`. Back it up securely; it holds
  the credentials redacted out of your rule YAML.
- Prefer per-site service accounts with the least privilege that still allows rule CRUD.
