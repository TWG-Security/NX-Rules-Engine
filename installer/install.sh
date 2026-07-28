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

echo ">> Installing systemd unit"
install -m 0644 "${SERVICE_SRC}" /etc/systemd/system/nxre.service
systemctl daemon-reload

cat <<EOF

nxre installed to ${PREFIX}.

Next steps:
  1. Put your config at ${PREFIX}/nxre.config.yaml
  2. Set the NX service-account password in the unit:
       sudoedit /etc/systemd/system/nxre.service      # [Service] Environment=NXRE__TWG__PASSWORD=...
       sudo systemctl daemon-reload
  3. Start it:
       sudo systemctl enable --now nxre
       journalctl -u nxre -f

EOF
