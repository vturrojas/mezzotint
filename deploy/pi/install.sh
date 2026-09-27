#!/usr/bin/env bash
# Mezzotint Frame installer for Raspberry Pi OS (Bookworm or newer, 64-bit Lite recommended).
#
#   ./deploy/pi/install.sh --studio http://studio-mac.local:8765 --token <token> [--hostname mezzotint]
#
# Enables SPI/I2C for the Inky, installs the frame into a venv, and runs it as a
# systemd service on port 80 so your phone can open http://<hostname>.local/
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
STUDIO=""; TOKEN=""; NEWHOST=""; PANEL="auto"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --studio) STUDIO="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --hostname) NEWHOST="$2"; shift 2 ;;
    --panel) PANEL="$2"; shift 2 ;;   # auto | what-red | phat-red
    *) echo "unknown option $1"; exit 2 ;;
  esac
done

say() { printf "\033[1;31m▌\033[0m %s\n" "$*"; }
die() { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

[[ -n "$STUDIO" && -n "$TOKEN" ]] || die "Usage: $0 --studio http://<mac>.local:8765 --token <token>  (the Mac installer prints both)"
[[ $EUID -ne 0 ]] || die "Run as your normal user (it will sudo when needed), not as root."
USER_NAME="$(id -un)"
VENV="$HOME/.virtualenvs/mezzotint"
DATA="$HOME/mezzotint-data"
CONFIG=/boot/firmware/config.txt; [[ -f $CONFIG ]] || CONFIG=/boot/config.txt

[[ "$(uname -m)" == "aarch64" ]] || say "Warning: 32-bit OS detected. 64-bit Raspberry Pi OS Lite installs much faster (prebuilt wheels)."

say "Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-dev python3-numpy python3-pil build-essential git avahi-daemon >/dev/null

say "Enabling SPI and I2C for the Inky..."
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0
REBOOT=0
if ! grep -q '^dtoverlay=spi0-0cs' "$CONFIG"; then
  echo 'dtoverlay=spi0-0cs' | sudo tee -a "$CONFIG" >/dev/null   # Inky drives chip-select itself
  REBOOT=1
fi

if [[ -n "$NEWHOST" && "$(hostname)" != "$NEWHOST" ]]; then
  say "Renaming this Pi to $NEWHOST (reachable as $NEWHOST.local)..."
  sudo hostnamectl set-hostname "$NEWHOST"
  sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$NEWHOST/" /etc/hosts
  REBOOT=1
fi

say "Creating the Python environment..."
[[ -d "$VENV" ]] || python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "${REPO}[frame]"

say "Checking the studio at $STUDIO..."
if curl -sf --noproxy '*' -m 8 -H "Authorization: Bearer $TOKEN" "$STUDIO/health" >/dev/null; then
  say "Studio reachable and token accepted."
else
  say "Warning: could not reach the studio right now. The frame will keep retrying; check the Mac is awake."
fi

say "Writing configuration..."
sudo mkdir -p /etc/mezzotint
sudo tee /etc/mezzotint/frame.env >/dev/null <<EOF
MEZZOTINT_STUDIO_URL=$STUDIO
MEZZOTINT_TOKEN=$TOKEN
MEZZOTINT_DATA=$DATA
MEZZOTINT_PANEL=$PANEL
MEZZOTINT_FRAME_PORT=80
EOF
sudo chmod 600 /etc/mezzotint/frame.env

sudo tee /etc/systemd/system/mezzotint-frame.service >/dev/null <<EOF
[Unit]
Description=Mezzotint e-paper frame
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
Group=$USER_NAME
SupplementaryGroups=spi i2c gpio
EnvironmentFile=/etc/mezzotint/frame.env
ExecStart=$VENV/bin/mezzotint-frame
Restart=always
RestartSec=5
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=false
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

# Be kind to the SD card: cap the journal.
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=50M\n' | sudo tee /etc/systemd/journald.conf.d/mezzotint.conf >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable mezzotint-frame >/dev/null

HOSTNAME_NOW="${NEWHOST:-$(hostname)}"
if (( REBOOT )); then
  say "Done. Rebooting to apply SPI/hostname changes; the frame starts by itself."
  say "Afterwards, open http://$HOSTNAME_NOW.local/ on your phone, or scan the QR on the frame."
  sleep 3; sudo reboot
else
  sudo systemctl restart mezzotint-frame
  say "Done. Open http://$HOSTNAME_NOW.local/ on your phone, or scan the QR on the frame."
  say "Logs: journalctl -u mezzotint-frame -f"
fi
