#!/usr/bin/env bash
# Install the host-side diagnostics on the board: the black box service, the journald drop-in and the
# MCU recovery service. Safe to re-run. It COPIES units into /etc/systemd/system (a symlink into /home
# fails to load at boot because /home mounts later; the boat, 2026-10-09).
#
# Usage: scripts/install_blackbox.sh [user@host]      (default arduino@boatlab.local)
#
# This does not deploy the app. To deploy the app side (bus status, flight recorder):
#   scp -r firmware/unoq_nmea2000_capture/{web,service,python} user@host:ArduinoApps/unoq_nmea2000_capture/
#   ssh user@host 'arduino-app-cli app restart user:unoq_nmea2000_capture'
set -eu
HOST="${1:-${UNOQ_HOST:-arduino@boatlab.local}}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE='unoq-nmea2000'

ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" "mkdir -p ~/$REMOTE/deploy ~/$REMOTE/service"
scp -q -o BatchMode=yes \
  "$ROOT/deploy/unoq_blackbox.py" "$ROOT/deploy/unoq-blackbox.service" \
  "$ROOT/deploy/unoq_mcu_recover.py" "$ROOT/deploy/unoq-mcu-recover.service" \
  "$ROOT/deploy/journald-unoq.conf" "$HOST:$REMOTE/deploy/"
scp -q -o BatchMode=yes "$ROOT/service/flight_recorder.py" "$HOST:$REMOTE/service/"

ssh -o BatchMode=yes "$HOST" bash -s <<'REMOTE_SCRIPT'
set -eu
D="$HOME/unoq-nmea2000/deploy"
sudo mkdir -p /etc/systemd/journald.conf.d
sudo cp "$D/journald-unoq.conf" /etc/systemd/journald.conf.d/unoq.conf
sudo systemctl restart systemd-journald
for unit in unoq-mcu-recover unoq-blackbox; do
  sudo systemctl disable "$unit" >/dev/null 2>&1 || true
  sudo rm -f "/etc/systemd/system/$unit.service"
  sudo cp "$D/$unit.service" "/etc/systemd/system/$unit.service"
done
sudo systemctl daemon-reload
sudo systemctl enable --now unoq-mcu-recover unoq-blackbox
sleep 2
for unit in unoq-mcu-recover unoq-blackbox; do
  printf '%s: %s / %s\n' "$unit" "$(systemctl is-enabled $unit)" "$(systemctl is-active $unit)"
done
REMOTE_SCRIPT
