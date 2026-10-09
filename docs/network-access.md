# Reaching the dashboard from a phone

The dashboard is served by the UNO Q at port 8000 (`UNOQ_IN_CONTAINER=1`
container bind, steering 13). The page is called **NMEA2000 Dashboard**.

## WiFi plan: join a known network, otherwise become the hotspot

NetworkManager on the board holds three WiFi profiles, tried in priority
order:

| Profile | Priority | What it does |
|---|---|---|
| home network | 0 | Joins the home WiFi |
| phone hotspot | -10 | Joins the owner's iPhone Personal Hotspot |
| `boatfi-ap` | -100 | The board's own access point, SSID **BoatFi**, WPA2, 2.4 GHz channel 6 |

The lowest priority means BoatFi only starts when no known network is in range.
In access-point mode the board is `10.42.0.1` (NetworkManager shared mode
provides DHCP).

Open `http://boatlab.local:8000` (mDNS via avahi) or, on BoatFi,
`http://10.42.0.1:8000`.

Passwords live only in NetworkManager on the board. They are not in this
repository.

### Create the profiles

```bash
nmcli connection add type wifi ifname wlan0 con-name iphone-hotspot ssid <PHONE_NAME> \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk '<PASSWORD>' \
  connection.autoconnect yes connection.autoconnect-priority -10

nmcli connection add type wifi ifname wlan0 con-name boatfi-ap ssid BoatFi mode ap \
  802-11-wireless.band bg 802-11-wireless.channel 6 \
  wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp \
  wifi-sec.psk '<PASSWORD>' ipv4.method shared ipv6.method ignore \
  connection.autoconnect yes connection.autoconnect-priority -100
```

### Limits

- **Status: configured, not yet tested away from home WiFi.**
- Station and access point at the same time are possible on this chip, but only
  on one shared channel (the board's published `iw list` shows `#channels <= 1`),
  and NetworkManager does not set it up on its own. This design uses one mode
  at a time instead.
- If the home network drops while the board runs, NetworkManager may fall to
  BoatFi and will not switch back by itself. A power cycle, or
  `nmcli connection up <home profile>`, recovers it.
- **Docker vs the iPhone hotspot (found and fixed 2026-10-09).** iOS hands out 172.20.10.0/28 (address
  range is not configurable), and Docker had put the app network on 172.20.0.0/16. The container then
  treated the phone as on-link, its replies never left the Docker bridge, and the dashboard never answered
  (ssh still worked). Reproduced with `scripts/check_hotspot_reachability.sh`, fixed by
  `scripts/fix_docker_subnet.sh` (Docker's pool is now 198.18.0.0/16). Both are simulations of the subnet
  on a virtual interface, not a real phone; the real-phone test is still to do.
- The board's address on a phone hotspot is whatever the phone's DHCP gives (it was 172.20.10.2 when the board
  joined first). Find it from the laptop: `ssh arduino@boatlab.local`, or the host black box
  (`~/unoq-blackbox`) records the address on every change. Android hotspots use other ranges.
- There is no authentication on the dashboard (steering 15). The WiFi
  password is the only access control.

## Bluetooth

The BLE feed is read-only. Its live characteristics use a compact binary
format (`docs/ble-protocol.md`), which a generic scanner such as LightBlue
shows as hex. A plain-text readings characteristic is proposed, not built.
The live characteristics have not yet been exercised with real bus traffic.
