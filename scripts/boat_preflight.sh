#!/usr/bin/env bash
# Boat preflight: is the UNO Q running the PRODUCTION build, not a bench build?
# Read-only. Usage: scripts/boat_preflight.sh [user@host]   (default arduino@boatlab.local;
# on the board's own hotspot use arduino@10.42.0.1). Exits non-zero on any failure.
set -u
HOST="${1:-${UNOQ_HOST:-arduino@boatlab.local}}"
APP='ArduinoApps/unoq_nmea2000_capture'
REPO_SKETCH="$(dirname "$0")/../firmware/unoq_nmea2000_capture/sketch/sketch.ino"
fail=0
check() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }
sum() { shasum -a 256 | cut -c1-16; }

ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" true 2>/dev/null || { echo "FAIL  cannot reach $HOST over ssh (first time on this computer? run: ssh $HOST   once, to accept the host key)"; exit 1; }
board_sketch=$(ssh -o BatchMode=yes "$HOST" "sha256sum $APP/sketch/sketch.ino" | cut -c1-16)
check "board sketch is byte-identical to the repo's production sketch" "[ '$board_sketch' = \"\$(sum < '$REPO_SKETCH')\" ]"
check "production sketch has no bench ACK code" "! grep -q BENCH_ONLY_ACK '$REPO_SKETCH'"
check "receiver is hardware, not simulator" "ssh -o BatchMode=yes $HOST \"grep -q 'setdefault(\\\"UNOQ_RECEIVER\\\", \\\"hardware\\\")' $APP/python/main.py\""
health=$(ssh -o BatchMode=yes "$HOST" "docker exec unoq_nmea2000_capture-main-1 python3 -c 'from arduino.app_utils import Bridge; print(Bridge.call(\"get_health\"))'" 2>&1 | tail -1)
echo "      MCU health: $health"
check "MCU is ListenOnly with the silent pin high" "echo '$health' | grep -qE ',ListenOnly,[a-z]+,true,'"
check "dashboard answers" "ssh -o BatchMode=yes $HOST 'curl -sf -m5 http://127.0.0.1:8000/api/v1/health >/dev/null'"
check "MCU recovery service is active (resets the MCU if the Bridge wedges)" "ssh -o BatchMode=yes $HOST 'systemctl is-active --quiet unoq-mcu-recover'"
check "dashboard reports the Bridge answering (receiver_ok_age_s)" "ssh -o BatchMode=yes $HOST 'curl -sf -m5 http://127.0.0.1:8000/api/v1/health | grep -Eq receiver_ok_age_s.:[0-9]'"
check "no bench soak script left running" "! ssh -o BatchMode=yes $HOST 'ps -eo cmd | grep -Eq \"[s]oak_(watch|obs)\"'"
echo "MANUAL: D6 jumper removed? CAN wiring/terminators checked? Power via a fused, surge-protected feed?"
exit $fail
