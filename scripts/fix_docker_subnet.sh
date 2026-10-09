#!/usr/bin/env bash
# Move Docker's address pool off 172.16/12 so the app network can never overlap a phone hotspot.
#
# Why: the iPhone Personal Hotspot always hands out 172.20.10.0/28, and Docker's default pool put the
# app network on 172.20.0.0/16. With the overlap the dashboard is unreachable from the phone (ssh still
# works). New Docker networks now come from 198.18.0.0/16 (RFC 2544 benchmarking space: not used on real
# LANs, hotspots or by BoatFi 10.42.0.0/24), in /24 slices.
#
# Usage: scripts/fix_docker_subnet.sh arduino@<board>
# What it does on the board: backs up /etc/docker/daemon.json, merges "default-address-pools", stops the
# app, removes its container and network, restarts Docker, starts the app (which recreates the network from the
# new pool). The dashboard is down for about a minute. Reversible: restore the .bak file, repeat.
set -eu
HOST="${1:?usage: fix_docker_subnet.sh user@host}"
APP='user:unoq_nmea2000_capture'
NET='unoq_nmea2000_capture_default'
CONTAINER='unoq_nmea2000_capture-main-1'

ssh -o BatchMode=yes "$HOST" APP="$APP" NET="$NET" CONTAINER="$CONTAINER" bash -s <<'REMOTE_SCRIPT'
set -eu
CONF=/etc/docker/daemon.json
sudo cp "$CONF" "$CONF.bak-$(date +%Y%m%d%H%M%S)"
sudo python3 - <<'PY'
import json
path = "/etc/docker/daemon.json"
conf = json.load(open(path))
conf["default-address-pools"] = [{"base": "198.18.0.0/16", "size": 24}]
json.dump(conf, open(path, "w"), indent="\t")
open(path, "a").write("\n")
PY
arduino-app-cli app stop "$APP" || true
# the stopped container still points at the old network id; remove it too or `app start` fails
docker rm -f "$CONTAINER" 2>/dev/null || true
docker network rm "$NET" 2>/dev/null || true
sudo systemctl restart docker
sleep 5
arduino-app-cli app start "$APP"
sleep 20
docker network inspect "$NET" --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}}{{end}}'
REMOTE_SCRIPT
