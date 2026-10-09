# Diagnostics that survive a dirty power-off

The board is powered from a boat circuit that gets switched off without a shutdown, and it has no
battery-backed clock. On the first boat trip (2026-10-09) that destroyed the evidence: the journal lost its
last minutes, App Lab deleted the app container and its log at the next boot, and nothing recorded how many
frames the CAN controller had seen. These pieces fix that. None of them touches the CAN bus.

## What is recorded

| Where | What | How often |
|---|---|---|
| `~/ArduinoApps/unoq_nmea2000_capture/.cache/blackbox/blackbox.jsonl*` (app) | frames received, frames per second, age of the last frame, PGNs and devices seen, frames persisted, the MCU's own counters (frames it counted, controller mode, bus error counters, drops, restarts), Bridge answer age, WARNING+ log lines, `bus_state` changes | a tick every 10 s, a full per-PGN summary every minute, an event on each change |
| `~/unoq-blackbox/blackbox.jsonl*` (host) | WiFi connection, mode (`ap` = BoatFi) and IP, NTP synced, state of the services, connected Bluetooth clients, free disk, a digest of the app's bus status | a tick every 10 s, a `change` line on each difference |
| journald | the usual system log, kept on disk and synced every 10 s (was every 5 minutes) | `/etc/systemd/journald.conf.d/unoq.conf` |

Every line is written with a single append and `fsync` before the call returns, so a power cut loses at
most the line being written. Files rotate at 2 MB and nine are kept per recorder (about two days).
The capture log (`.cache/captures`) is separate; the black box is deliberately not inside it.

## The phone shows it too

The dashboard has a **Bus status** strip under the title:

- red, *No frames received from the bus yet*: nothing has arrived since the app started;
- green, *Receiving from the bus: N frames/s*;
- amber, *Bus went silent N s ago*.

Under it: frames, last-frame age, devices and PGNs seen, the controller mode, bus error counters, how many
frames the MCU counted and dropped, MCU restarts, and how long ago the Bridge last answered. The green
dot at the top right only means the browser reaches the board; it says nothing about the bus.
`GET /api/v1/bus-status` returns the same data.

## Reading it after a trip

```bash
scripts/blackbox_report.py --fetch arduino@<board>      # copies both black boxes and prints a report
```

One section per power-on. The board's clock is wrong until NTP syncs, so lines are ordered by kernel
uptime and the wall clock is corrected from the moment the host log reported NTP synced; a boot with no
sync is marked *clock unsynced*. The last line of each boot is the last thing written before the power went.

## Install / update

```bash
scripts/install_blackbox.sh arduino@<board>             # host black box, journald drop-in, recovery service
scp -r firmware/unoq_nmea2000_capture/{web,service,python} arduino@<board>:ArduinoApps/unoq_nmea2000_capture/
ssh arduino@<board> 'arduino-app-cli app restart user:unoq_nmea2000_capture'
scripts/boat_preflight.sh arduino@<board>               # checks all of the above
```

Units are copied into `/etc/systemd/system`, never linked from `/home` (a symlink into the separate `/home`
partition fails to load at boot).

## Verified

2026-10-09 on the board: a hard reboot with no sync (`echo b > /proc/sysrq-trigger`) about 4 s after the
last tick. The black box's last record was 4 s before the cut and every service came back active. The journal
lost the last 14 s (it previously lost up to 5 minutes). This is not a real power cut on the supply, but it is
the same "no flush, no unmount" condition for the filesystem.

## Limits

- Frames themselves are in the capture log, which fsyncs every frame; at high rates that is the throughput
  limit, not the black box.
- A full disk stops the writes (the recorder returns False and carries on); free space is in the host ticks.
