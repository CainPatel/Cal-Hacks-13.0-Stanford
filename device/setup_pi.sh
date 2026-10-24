#!/usr/bin/env bash
# One-shot setup for the approval key on a fresh Raspberry Pi OS (Bookworm).
# Run from the cloned repo on the Pi: sudo ./device/setup_pi.sh   (safe to re-run)
set -euo pipefail

[ "$(uname -s)" = Linux ] || { echo "This is for the Raspberry Pi, not this machine." >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }
RUN_USER=${SUDO_USER:?run with sudo from your normal user, not as root}
REPO=$(cd "$(dirname "$0")/.." && pwd)

# lgpio is gpiozero's pin backend on Pi 5 (works on older Pis too); pip can't build it easily.
apt-get update
apt-get install -y python3-venv python3-lgpio curl

# --system-site-packages so the venv sees the apt lgpio.
sudo -u "$RUN_USER" python3 -m venv --system-site-packages "$REPO/.venv"
sudo -u "$RUN_USER" "$REPO/.venv/bin/pip" install -r "$REPO/requirements.txt"

command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh

cat > /etc/systemd/system/drawkey.service <<EOF
[Unit]
Description=Draw approval key
Wants=network-online.target
After=network-online.target

[Service]
User=$RUN_USER
SupplementaryGroups=gpio
WorkingDirectory=$REPO
ExecStart=$REPO/.venv/bin/uvicorn device.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable drawkey
systemctl restart drawkey

for _ in $(seq 20); do
  if PUB=$(curl -fs localhost:8000/pubkey); then
    echo; echo "Device is up. Public key (send to A for config/contract.yaml esp32_pubkey):"
    echo "$PUB"; exit 0
  fi
  sleep 1
done
echo "Service didn't answer on :8000. Check: journalctl -u drawkey -n 50" >&2
exit 1
