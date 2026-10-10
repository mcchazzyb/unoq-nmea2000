#!/usr/bin/env bash
# Install the host side of the dashboard "Shut down" button on the board: a path unit that watches for the
# request file, and the service that powers the board off. Safe to re-run. It COPIES the units into
# /etc/systemd/system (a symlink into /home fails to load at boot because /home mounts later).
#
# Usage: scripts/install_shutdown.sh [user@host]      (default arduino@boatlab.local)
#
# This does not deploy the app. The button also needs the app side (web/, service/) on the board.
set -eu
HOST="${1:-${UNOQ_HOST:-arduino@boatlab.local}}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE='unoq-nmea2000'

ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" "mkdir -p ~/$REMOTE/deploy"
scp -q -o BatchMode=yes \
  "$ROOT/deploy/unoq_shutdown.sh" "$ROOT/deploy/unoq-shutdown.path" "$ROOT/deploy/unoq-shutdown.service" \
  "$HOST:$REMOTE/deploy/"

ssh -o BatchMode=yes "$HOST" bash -s <<'REMOTE_SCRIPT'
set -eu
D="$HOME/unoq-nmea2000/deploy"
sudo systemctl disable unoq-shutdown.path >/dev/null 2>&1 || true
sudo rm -f /etc/systemd/system/unoq-shutdown.path /etc/systemd/system/unoq-shutdown.service
sudo cp "$D/unoq-shutdown.path" "$D/unoq-shutdown.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now unoq-shutdown.path
sleep 1
printf 'unoq-shutdown.path: %s / %s\n' "$(systemctl is-enabled unoq-shutdown.path)" "$(systemctl is-active unoq-shutdown.path)"
REMOTE_SCRIPT
