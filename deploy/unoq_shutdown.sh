#!/bin/sh
# Host side of the dashboard "Shut down" button. Run by unoq-shutdown.service when the dashboard writes
# its request file (see service/shutdown_request.py).
#
#   unoq_shutdown.sh <request-file>
#
# The request file holds the kernel boot id of the boot it was written in. Power off only if it matches
# THIS boot. A request left behind by a power cut must never shut the board down again at the next boot.
# The file is deleted first either way (so a failing poweroff can never re-trigger this in a loop). Then
# the outcome is reported with a marker the path unit does not watch: <file>.queued ONLY after systemctl
# has accepted the power-off, or <file>.failed if it could not. The dashboard says "shutdown started" only
# on .queued; "the file is gone" alone proves nothing (systemctl may still be about to fail).
#
# Test hooks (never set in production): UNOQ_BOOT_ID_FILE, UNOQ_SYSTEMCTL, UNOQ_POWEROFF_EXTRA.
set -u
file="${1:-}"
[ -n "$file" ] && [ -f "$file" ] || exit 0

want="$(head -c 64 "$file" | tr -d '[:space:]')"
rm -f "$file"

now="$(tr -d '[:space:]' < "${UNOQ_BOOT_ID_FILE:-/proc/sys/kernel/random/boot_id}")"
if [ -z "$want" ] || [ "$want" != "$now" ]; then
  echo "unoq-shutdown: ignoring a stale or empty request (written in another boot)"
  exit 0
fi

echo "unoq-shutdown: clean power-off requested from the dashboard"
sync
# --no-block: queue the job and return, so this service is not waiting on its own shutdown.
# shellcheck disable=SC2086
if "${UNOQ_SYSTEMCTL:-/usr/bin/systemctl}" --no-block poweroff ${UNOQ_POWEROFF_EXTRA:-}; then
  echo "queued" > "$file.queued"
  exit 0
else
  rc=$?
  echo "unoq-shutdown: systemctl poweroff FAILED (exit $rc); the board is NOT shutting down"
  echo "systemctl poweroff exit $rc" > "$file.failed"
  exit "$rc"
fi
