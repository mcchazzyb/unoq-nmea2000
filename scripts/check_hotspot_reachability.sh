#!/usr/bin/env bash
# Can a phone on the iPhone hotspot subnet (172.20.10.0/28) reach the dashboard on this board?
#
# Found 2026-10-09: Docker put the app network on 172.20.0.0/16, which contains 172.20.10.0/28. The
# container then treated the phone as on-link and its replies never left the Docker bridge, so ssh worked
# but the dashboard on port 8000 never answered. This reproduces that with a temporary network namespace
# that plays the phone, for three subnets: a control, the BoatFi access point, and the iPhone hotspot.
#
# Run ON the board (needs sudo; adds and removes a temporary veth + namespace, changes nothing else):
#   scripts/check_hotspot_reachability.sh arduino@<board>
# Exit status is non-zero if any case fails.
set -u
if [ "$#" -ge 1 ] && [ "$(id -u)" -ne 0 ]; then
  exec ssh -o BatchMode=yes "$1" 'sudo bash -s' < "$0"
fi
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)"; exit 2; }

PORT="${UNOQ_PORT:-8000}"
fail=0
cleanup() { ip netns del phone 2>/dev/null; ip link del vph 2>/dev/null; true; }
trap cleanup EXIT

trial() {  # $1=board addr/prefix  $2=client addr/prefix  $3=board addr  $4=label
  cleanup
  ip netns add phone
  ip link add vph type veth peer name vph-ns
  ip link set vph-ns netns phone
  ip addr add "$1" dev vph; ip link set vph up
  ip netns exec phone ip addr add "$2" dev vph-ns
  ip netns exec phone ip link set vph-ns up
  code=$(ip netns exec phone curl -s -m 6 -o /dev/null -w '%{http_code}' "http://$3:$PORT/api/v1/health")
  if [ "$code" = "200" ]; then echo "PASS  $4: dashboard answers (HTTP 200)"; else echo "FAIL  $4: dashboard did not answer (HTTP $code)"; fail=1; fi
  cleanup
}

trial 10.99.10.2/28  10.99.10.3/28  10.99.10.2  "control 10.99.10.0/28"
trial 10.42.0.1/24   10.42.0.5/24   10.42.0.1   "BoatFi access point 10.42.0.0/24"
trial 172.20.10.2/28 172.20.10.3/28 172.20.10.2 "iPhone hotspot 172.20.10.0/28"
exit $fail
