#!/usr/bin/env bash
# Install nxre as a systemd service on the NX Witness server.
# Run as root:  sudo installer/install.sh
set -euo pipefail

PREFIX="${NXRE_PREFIX:-/opt/nxre}"
SERVICE_SRC="$(cd "$(dirname "$0")" && pwd)/nxre.service"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo installer/install.sh)." >&2
  exit 1
fi

echo ">> Installing nxre into ${PREFIX}"
mkdir -p "${PREFIX}"
# Copy the project WITHOUT the developer virtualenv, git dir, or caches. Copying a
# source .venv is what caused the old "203/EXEC" breakage: the copied launcher kept a
# shebang pointing back into the dev clone's Python. We always build a fresh venv below.
# Preserve any existing config/session on the target (no --delete).
if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude '.venv' --exclude '.git' --exclude '__pycache__' \
    --exclude '.pytest_cache' --exclude '*.egg-info' \
    --exclude 'session.json' --exclude 'nxre.config.yaml' \
    "${PROJECT_ROOT}/" "${PREFIX}/"
else
  cp -r "${PROJECT_ROOT}/." "${PREFIX}/"
  rm -rf "${PREFIX}/.venv" "${PREFIX}/.git" "${PREFIX}/.pytest_cache"
  find "${PREFIX}" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
fi

echo ">> Building a clean virtualenv"
# --clear wipes any stale venv so an update never inherits an old install/shebang.
python3 -m venv --clear "${PREFIX}/.venv"
"${PREFIX}/.venv/bin/pip" install --upgrade pip >/dev/null
"${PREFIX}/.venv/bin/pip" install --force-reinstall "${PREFIX}" >/dev/null

echo ">> Writing default config (assumes NX on this box at 127.0.0.1:7001)"
if [[ ! -f "${PREFIX}/nxre.config.yaml" ]]; then
  cp "${PREFIX}/nxre.config.example.yaml" "${PREFIX}/nxre.config.yaml"
fi

echo ">> Installing systemd unit"
install -m 0644 "${SERVICE_SRC}" /etc/systemd/system/nxre.service
systemctl daemon-reload

# Fresh install -> enable + start. Update (already running) -> restart to load new code,
# so nobody has to remember `systemctl restart` after an upgrade.
if systemctl is-active --quiet nxre; then
  echo ">> Restarting nxre (update)"
  systemctl restart nxre
else
  echo ">> Enabling and starting nxre"
  systemctl enable --now nxre
fi

# Figure out the port the service is actually serving on (default 8787).
PORT="$(sed -n 's/^[[:space:]]*port:[[:space:]]*\([0-9]\+\).*/\1/p' "${PREFIX}/nxre.config.yaml" | head -n1)"
PORT="${PORT:-8787}"

cat <<EOF

nxre is installed and running.

  ->  Open  http://127.0.0.1:${PORT}  in a browser and sign in with your NX account.

That's it. The service assumes NX is on this host (https://127.0.0.1:7001); edit
${PREFIX}/nxre.config.yaml if that's not the case, then: sudo systemctl restart nxre

Handy:
  systemctl status nxre
  journalctl -u nxre -f        # live log

For unattended restarts with no browser, you can instead set a service-account
password in the unit (Environment=NXRE__TWG__PASSWORD=...) — optional.

EOF
