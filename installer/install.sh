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
cp -r "${PROJECT_ROOT}/." "${PREFIX}/"

echo ">> Creating virtualenv"
python3 -m venv "${PREFIX}/.venv"
"${PREFIX}/.venv/bin/pip" install --upgrade pip >/dev/null
"${PREFIX}/.venv/bin/pip" install "${PREFIX}" >/dev/null

echo ">> Writing default config (assumes NX on this box at 127.0.0.1:7001)"
if [[ ! -f "${PREFIX}/nxre.config.yaml" ]]; then
  cp "${PREFIX}/nxre.config.example.yaml" "${PREFIX}/nxre.config.yaml"
fi

echo ">> Installing systemd unit"
install -m 0644 "${SERVICE_SRC}" /etc/systemd/system/nxre.service
systemctl daemon-reload

echo ">> Enabling and starting nxre"
systemctl enable --now nxre

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
