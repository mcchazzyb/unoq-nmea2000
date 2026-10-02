"""Real BlueZ GATT peripheral (Stage 10.3 spike) -- Requirement 13.1-13.10,
28.1-28.3.

ARCHITECTURAL CONSTRAINT FOUND DURING THIS SPIKE, documented here because it
shapes this module's whole shape: the Arduino App Lab Python container has
NO access to the host's D-Bus system bus or BlueZ (confirmed against the
real UNO Q -- `app-compose.yaml` mounts no dbus socket, and Arduino's own
forum/an open unmerged PR arduino/arduino-app-cli#547 confirm this is a
known, current limitation, not something fixable from inside the
container). A real BlueZ GATT peripheral can therefore NOT run inside the
same container as `web/main.py`.

This module is designed to run as a SEPARATE process directly on the
board's Linux host (outside any App Lab container), where BlueZ/D-Bus
already work natively. It talks to the containerized Web_API over
`http://127.0.0.1:<port>` (Docker's `ports:` publish mapping makes the
container's 8000 reachable on the host's own loopback) rather than sharing
Python objects with `LiveStateCache` directly -- the two run as unrelated
OS processes.

Library choice (locked by this spike, per design.md's deferred-choice note):
`PyGObject`'s `Gio.DBusConnection` (GDBus), NOT `python-dbus` -- found live on
the board that `python3-gi` is already installed and reaches `org.bluez`
immediately, while `python3-dbus` needs an `apt-get install` the deploy
account has no passwordless sudo for. GDBus is an equally standard BlueZ
GATT-peripheral implementation (BlueZ's own upstream examples exist in both
flavors); choosing it avoids a real deploy blocker for zero capability cost.

This module still consumes ONLY Domain_Model-shaped data (Requirement
13.9): `_entity_from_dict` below reconstructs real `protocol.domain`
dataclass instances from the Web_API's JSON rather than inventing a
parallel shape, so `ble.encoding.encode_envelope` and `MockBLEPublisher`'s
proven notify/stale/pairing logic are reused unchanged -- this module adds
ONLY the D-Bus GATT registration/advertisement layer on top.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from ble.publisher import GATT_SCHEMA, MockBLEPublisher, UnknownCharacteristicError
from protocol.domain import (
    UNSET,
    ElectricalState,
    EngineState,
    NavigationState,
    Provenance,
    TankState,
    VesselState,
)
from protocol.receiver import CanHealth

logger = logging.getLogger(__name__)

#: Standard BlueZ GATT object paths/interfaces this module registers against.
BLUEZ_SERVICE_NAME = "org.bluez"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"

#: 128-bit vendor base UUID this schema's 16-bit shorts (ble/publisher.py's
#: "A2D1".."A2D9") expand into, matching design.md's documented placeholder.
_UUID_BASE = "0000{}-0000-1000-8000-00805f9b34fb"
#: How often this process polls the Web_API for a fresh VesselState snapshot.
POLL_INTERVAL_S = 1.0


def _char_uuid(short: str) -> str:
    return _UUID_BASE.format(short.lower())


def _fetch_json(url: str, timeout: float = 2.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - local loopback only
        return json.loads(resp.read().decode("utf-8"))


def _provenance_from_dict(data: dict | None) -> Provenance | None:
    if data is None:
        return None
    return Provenance(
        source_address=data.get("source_address", "unknown"),
        source_pgn=data.get("source_pgn", "unknown"),
        node_identity=data.get("node_identity", "unknown"),
        raw_frame_ref="",  # not served over REST; not needed for BLE encoding
        timestamp=0.0,
        decoder_version="",
        quality=data.get("quality", "unknown"),
        staleness_s=data.get("staleness_s", 0.0),
        vendor_specific=data.get("vendor_specific", False),
        value_origin=data.get("value_origin", "decoded"),
        derivation=data.get("derivation"),
    )


def _field(data: dict, name: str):
    """REST's _entity_to_response_dict maps UNSET -> None (web/api.py); map
    back to UNSET here so encode_entity's own UNSET check behaves exactly as
    it does for a live in-process VesselState (Requirement 11's
    UNSET-distinct-from-None discipline applies at this boundary too, even
    though JSON itself cannot carry UNSET directly)."""
    value = data.get(name)
    return UNSET if value is None else value


def _engine_from_dict(key: str, data: dict) -> EngineState:
    instance_str, source_address_str = key.split(":", 1)
    return EngineState(
        engine_instance=int(instance_str),
        rpm=_field(data, "rpm"),
        coolant_temp=_field(data, "coolant_temp"),
        oil_pressure=_field(data, "oil_pressure"),
        alternator_voltage=_field(data, "alternator_voltage"),
        engine_hours=_field(data, "engine_hours"),
        fuel_rate=_field(data, "fuel_rate"),
        trim=_field(data, "trim"),
        status=_field(data, "status"),
        source_address=int(source_address_str),
        provenance=_provenance_from_dict(data.get("provenance")),
    )


def _tank_from_dict(key: str, data: dict) -> TankState:
    instance_str, source_address_str = key.split(":", 1)
    return TankState(
        tank_instance=int(instance_str),
        fluid_type=_field(data, "fluid_type"),
        level_percent=_field(data, "level_percent"),
        capacity_litres=_field(data, "capacity_litres"),
        quantity_remaining_litres=_field(data, "quantity_remaining_litres"),
        source_address=int(source_address_str),
        provenance=_provenance_from_dict(data.get("provenance")),
    )


def _electrical_from_dict(data: dict) -> ElectricalState:
    return ElectricalState(
        battery_voltage=_field(data, "battery_voltage"),
        charging_voltage=_field(data, "charging_voltage"),
        current=_field(data, "current"),
        battery_state=_field(data, "battery_state"),
        dc_source=_field(data, "dc_source"),
        source_address=data.get("source_address", 0) or 0,
        provenance=_provenance_from_dict(data.get("provenance")),
    )


def _navigation_from_dict(data: dict) -> NavigationState:
    return NavigationState(
        position=_field(data, "position"),
        cog=_field(data, "cog"),
        sog=_field(data, "sog"),
        heading=_field(data, "heading"),
        depth=_field(data, "depth"),
        water_temp=_field(data, "water_temp"),
        wind=_field(data, "wind"),
        source_address=data.get("source_address", 0) or 0,
        provenance=_provenance_from_dict(data.get("provenance")),
    )


def vessel_state_from_live_json(live: dict) -> VesselState:
    """Reconstruct a real VesselState from GET /api/v1/live's JSON body --
    the ONLY place this module's http-polling boundary meets Domain_Model
    types, keeping everything below it (encode_envelope, MockBLEPublisher)
    unaware it is fed from JSON rather than a live LiveStateCache."""
    vessel = VesselState()
    for key, data in live.get("engines", {}).items():
        engine = _engine_from_dict(key, data)
        vessel.engines[(engine.engine_instance, engine.source_address)] = engine
    for key, data in live.get("tanks", {}).items():
        tank = _tank_from_dict(key, data)
        vessel.tanks[(tank.tank_instance, tank.source_address)] = tank
    if live.get("electrical"):
        vessel.electrical = _electrical_from_dict(live["electrical"])
    if live.get("navigation"):
        vessel.navigation = _navigation_from_dict(live["navigation"])
    return vessel


def can_health_from_health_json(health: dict) -> CanHealth:
    return CanHealth(
        connected=bool(health.get("can_connected", False)),
        fps=0.0,
        dropped_frames=0,
        error_count=0,
        controller_state="unknown",
        termination_on=False,
    )


def _gi():
    """Lazily import PyGObject/GLib/Gio. Deferred to call time (never at
    module import) so this module stays importable on a host with no `gi`
    installed -- e.g. this project's own macOS/CI test runners, matching
    Requirement 17.9/17.10's host-testable-MVP discipline. Only the
    functions below that actually touch D-Bus/BlueZ call this; the pure
    JSON-to-Domain_Model reconstruction above never does."""
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib

    return Gio, GLib


class _Characteristic:
    """One `org.bluez.GattCharacteristic1` D-Bus object. BlueZ calls
    `ReadValue`/`StartNotify`/`StopNotify` on this object once registered;
    this class delegates every read to the wrapped MockBLEPublisher so the
    notify-vs-read split, pairing gate, and encoding logic is NOT
    reimplemented here (Property 22/23 already proven against the mock)."""

    def __init__(
        self,
        index: int,
        service_path: str,
        char,
        publisher: MockBLEPublisher,
    ) -> None:
        self.path = f"{service_path}/char{index:03d}"
        self._char = char
        self._publisher = publisher
        self._notifying = False

    def _read_bytes(self) -> bytes:
        name = self._char.name
        try:
            if name == "schema_version":
                return bytes([self._publisher.read_schema_version()])
            if name == "device_info":
                return json.dumps(self._publisher.device_info).encode("utf-8")
            if name == "connection_health":
                return json.dumps(self._publisher.read_connection_health()).encode("utf-8")
            if name == "diagnostics":
                return json.dumps(self._publisher.read_diagnostics()).encode("utf-8")
            value = self._publisher.read_characteristic(name)
            return value if value is not None else b""
        except UnknownCharacteristicError:
            logger.exception("bluez_publisher: unknown characteristic %s requested", name)
            return b""

    def method_call(
        self,
        connection,
        sender,
        object_path,
        interface_name,
        method_name,
        parameters,
        invocation,
    ) -> None:
        _, GLib = _gi()
        if method_name == "ReadValue":
            data = self._read_bytes()
            invocation.return_value(GLib.Variant("(ay)", (list(data),)))
        elif method_name == "StartNotify":
            self._notifying = True
            invocation.return_value(None)
        elif method_name == "StopNotify":
            self._notifying = False
            invocation.return_value(None)
        else:
            Gio, _ = _gi()
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, "no such method"
            )

    def get_property(self, connection, sender, object_path, interface_name, property_name):
        _, GLib = _gi()
        if property_name == "UUID":
            return GLib.Variant("s", _char_uuid(self._char.uuid))
        if property_name == "Service":
            return GLib.Variant("o", self.path.rsplit("/", 1)[0])
        if property_name == "Flags":
            flags = {
                "read": ["read"],
                "notify": ["notify"],
                "read_notify": ["read", "notify"],
            }[self._char.properties.value]
            return GLib.Variant("as", flags)
        return None


_CHARACTERISTIC_INTROSPECTION_XML = """
<node>
  <interface name="org.bluez.GattCharacteristic1">
    <method name="ReadValue">
      <arg name="options" type="a{sv}" direction="in"/>
      <arg name="value" type="ay" direction="out"/>
    </method>
    <method name="StartNotify"/>
    <method name="StopNotify"/>
    <property name="UUID" type="s" access="read"/>
    <property name="Service" type="o" access="read"/>
    <property name="Flags" type="as" access="read"/>
  </interface>
</node>
"""

_SERVICE_INTROSPECTION_XML = """
<node>
  <interface name="org.bluez.GattService1">
    <property name="UUID" type="s" access="read"/>
    <property name="Primary" type="b" access="read"/>
  </interface>
</node>
"""

_ADVERTISEMENT_INTROSPECTION_XML = """
<node>
  <interface name="org.bluez.LEAdvertisement1">
    <method name="Release"/>
    <property name="Type" type="s" access="read"/>
    <property name="LocalName" type="s" access="read"/>
    <property name="ServiceUUIDs" type="as" access="read"/>
  </interface>
</node>
"""

#: Vendor base short code for this schema's primary service, distinct from
#: any individual characteristic's A2D1..A2D9 short above.
_SERVICE_SHORT_UUID = "A2D0"
_LOCAL_NAME = "unoq-nmea2000"


@dataclass
class BlueZPublisher:
    """Real `BLEPublisher` implementation: registers one GATT service with
    the full GATT_SCHEMA as D-Bus-backed characteristics against the host's
    BlueZ daemon, plus an LE advertisement so a phone can discover it.

    Wraps a `MockBLEPublisher` for all state/encoding/pairing/staleness
    logic (Requirement 13.1-13.10, 28.1-28.3 are already proven against
    that class) -- this class's own responsibility is strictly the D-Bus
    registration plumbing BlueZ requires to expose that state over the air.
    """

    require_pairing: bool = True
    device_info: dict | None = None

    def __post_init__(self) -> None:
        self._mock = MockBLEPublisher(
            require_pairing=self.require_pairing, device_info=self.device_info or {}
        )
        self._bus = None
        self._registrations: list[int] = []
        self._service_path = "/org/unoq/nmea2000/service0"
        self._adv_path = "/org/unoq/nmea2000/advertisement0"
        self._chars: list[_Characteristic] = []

    # --- BLEPublisher protocol, delegated straight to the wrapped mock -----

    def publish(self, vessel: VesselState, now: float | None = None) -> None:
        self._mock.publish(vessel, now=now)

    def schema_version(self) -> int:
        return self._mock.schema_version()

    def update_can_health(self, health: CanHealth, now: float | None = None) -> None:
        self._mock.update_can_health(health, now=now)

    def update_diagnostics(self, *args, **kwargs) -> None:
        self._mock.update_diagnostics(*args, **kwargs)

    def pair(self, client_id: str) -> None:
        self._mock.pair(client_id)

    # --- D-Bus / BlueZ registration -----------------------------------------

    def start(self) -> None:
        """Register the GATT service + characteristics + LE advertisement
        with BlueZ over the real system D-Bus. Idempotent only in the sense
        that calling it twice would double-register; callers start it once
        per process lifetime."""
        Gio, _GLib = _gi()
        self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        adapter_path = self._find_adapter()
        self._register_service_and_characteristics()
        self._register_gatt_application(adapter_path)
        self._register_advertisement(adapter_path)
        logger.info(
            "bluez_publisher: GATT application + advertisement registration requested on %s "
            "(async -- the main loop must be running for BlueZ's completion callback to fire)",
            adapter_path,
        )

    def _find_adapter(self) -> str:
        Gio, _GLib = _gi()
        proxy = Gio.DBusProxy.new_sync(
            self._bus, Gio.DBusProxyFlags.NONE, None, BLUEZ_SERVICE_NAME, "/", DBUS_OM_IFACE, None
        )
        result = proxy.call_sync("GetManagedObjects", None, Gio.DBusCallFlags.NONE, -1, None)
        objects = result.unpack()[0]
        for path, interfaces in objects.items():
            if "org.bluez.Adapter1" in interfaces:
                return path
        raise RuntimeError("no org.bluez.Adapter1 found; is a Bluetooth adapter present?")

    def _register_service_and_characteristics(self) -> None:
        Gio, GLib = _gi()
        service_node_info = Gio.DBusNodeInfo.new_for_xml(_SERVICE_INTROSPECTION_XML)
        service_iface_info = service_node_info.interfaces[0]

        def service_get_property(connection, sender, object_path, interface_name, property_name):
            if property_name == "UUID":
                return GLib.Variant("s", _char_uuid(_SERVICE_SHORT_UUID))
            if property_name == "Primary":
                return GLib.Variant("b", True)
            return None

        reg_id = self._bus.register_object(
            self._service_path,
            service_iface_info,
            None,
            service_get_property,
            None,
        )
        self._registrations.append(reg_id)

        char_node_info = Gio.DBusNodeInfo.new_for_xml(_CHARACTERISTIC_INTROSPECTION_XML)
        char_iface_info = char_node_info.interfaces[0]
        for index, char in enumerate(GATT_SCHEMA):
            characteristic = _Characteristic(index, self._service_path, char, self._mock)
            self._chars.append(characteristic)
            reg_id = self._bus.register_object(
                characteristic.path,
                char_iface_info,
                characteristic.method_call,
                characteristic.get_property,
                None,
            )
            self._registrations.append(reg_id)

    def _register_gatt_application(self, adapter_path: str) -> None:
        """BlueZ's GattManager1.RegisterApplication expects an ObjectManager
        rooted at an application path whose children are the service +
        characteristic objects already registered above; expose that root
        via a minimal ObjectManager implementation."""
        Gio, GLib = _gi()
        om_xml = """
        <node>
          <interface name="org.freedesktop.DBus.ObjectManager">
            <method name="GetManagedObjects">
              <arg name="objects" type="a{oa{sa{sv}}}" direction="out"/>
            </method>
          </interface>
        </node>
        """
        om_node_info = Gio.DBusNodeInfo.new_for_xml(om_xml)
        om_iface_info = om_node_info.interfaces[0]

        def om_method_call(
            connection, sender, object_path, interface_name, method_name, parameters, invocation
        ) -> None:
            if method_name != "GetManagedObjects":
                invocation.return_error_literal(
                    Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, "no such method"
                )
                return
            objects: dict = {
                self._service_path: {
                    GATT_SERVICE_IFACE: {
                        "UUID": GLib.Variant("s", _char_uuid(_SERVICE_SHORT_UUID)),
                        "Primary": GLib.Variant("b", True),
                    }
                }
            }
            for characteristic in self._chars:
                objects[characteristic.path] = {
                    GATT_CHRC_IFACE: {
                        "UUID": GLib.Variant("s", _char_uuid(characteristic._char.uuid)),
                        "Service": GLib.Variant("o", self._service_path),
                        "Flags": GLib.Variant(
                            "as",
                            {
                                "read": ["read"],
                                "notify": ["notify"],
                                "read_notify": ["read", "notify"],
                            }[characteristic._char.properties.value],
                        ),
                    }
                }
            invocation.return_value(GLib.Variant("(a{oa{sa{sv}}})", (objects,)))

        app_path = "/org/unoq/nmea2000"
        reg_id = self._bus.register_object(app_path, om_iface_info, om_method_call, None, None)
        self._registrations.append(reg_id)

        gatt_mgr = Gio.DBusProxy.new_sync(
            self._bus, Gio.DBusProxyFlags.NONE, None, BLUEZ_SERVICE_NAME, adapter_path, GATT_MANAGER_IFACE, None
        )

        def _on_registered(proxy, result, _user_data=None) -> None:
            try:
                proxy.call_finish(result)
                logger.info("bluez_publisher: RegisterApplication succeeded")
            except GLib.Error:
                logger.exception("bluez_publisher: RegisterApplication failed")

        # Deliberately async (call(), not call_sync()): BlueZ's own
        # RegisterApplication implementation calls back into THIS process's
        # ObjectManager.GetManagedObjects to validate the application before
        # replying. A blocking call_sync() on the calling thread, with no
        # GLib main loop yet running to dispatch that inbound callback,
        # deadlocks -- found live on the board (this exact call hung
        # indefinitely under call_sync; isolated by stepping through
        # start()'s private methods one at a time with explicit prints).
        gatt_mgr.call(
            "RegisterApplication",
            GLib.Variant("(oa{sv})", (app_path, {})),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            _on_registered,
        )

    def _register_advertisement(self, adapter_path: str) -> None:
        Gio, GLib = _gi()
        node_info = Gio.DBusNodeInfo.new_for_xml(_ADVERTISEMENT_INTROSPECTION_XML)
        iface_info = node_info.interfaces[0]

        def method_call(
            connection, sender, object_path, interface_name, method_name, parameters, invocation
        ) -> None:
            if method_name == "Release":
                invocation.return_value(None)
            else:
                invocation.return_error_literal(
                    Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, "no such method"
                )

        def get_property(connection, sender, object_path, interface_name, property_name):
            if property_name == "Type":
                return GLib.Variant("s", "peripheral")
            if property_name == "LocalName":
                return GLib.Variant("s", _LOCAL_NAME)
            if property_name == "ServiceUUIDs":
                return GLib.Variant("as", [_char_uuid(_SERVICE_SHORT_UUID)])
            return None

        reg_id = self._bus.register_object(self._adv_path, iface_info, method_call, get_property, None)
        self._registrations.append(reg_id)

        adv_mgr = Gio.DBusProxy.new_sync(
            self._bus,
            Gio.DBusProxyFlags.NONE,
            None,
            BLUEZ_SERVICE_NAME,
            adapter_path,
            LE_ADVERTISING_MANAGER_IFACE,
            None,
        )

        def _on_advertised(proxy, result, _user_data=None) -> None:
            try:
                proxy.call_finish(result)
                logger.info("bluez_publisher: RegisterAdvertisement succeeded")
            except GLib.Error:
                logger.exception("bluez_publisher: RegisterAdvertisement failed")

        adv_mgr.call(
            "RegisterAdvertisement",
            GLib.Variant("(oa{sv})", (self._adv_path, {})),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            _on_advertised,
        )

    def stop(self) -> None:
        for reg_id in self._registrations:
            self._bus.unregister_object(reg_id)
        self._registrations.clear()


def run_forever(web_api_base_url: str, require_pairing: bool = True) -> None:
    """Poll the containerized Web_API's /api/v1/live + /api/v1/health over
    the host's loopback and keep republishing into the real BlueZ GATT
    peripheral. Runs the GLib main loop so BlueZ's D-Bus calls (ReadValue,
    StartNotify, ...) are serviced concurrently with the polling timer.

    A fetch failure (container restarting, Web_API briefly down) is caught
    and retried on the next poll tick rather than crashing this process
    (steering 11's fail-safe discipline applied to this new seam) -- the
    GATT peripheral keeps serving its last-known values meanwhile."""
    _Gio, GLib = _gi()
    publisher = BlueZPublisher(require_pairing=require_pairing, device_info={"name": _LOCAL_NAME})
    publisher.start()

    def _poll() -> bool:
        try:
            live = _fetch_json(f"{web_api_base_url}/api/v1/live")
            health = _fetch_json(f"{web_api_base_url}/api/v1/health")
            vessel = vessel_state_from_live_json(live)
            publisher.publish(vessel)
            publisher.update_can_health(can_health_from_health_json(health))
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            logger.exception("bluez_publisher: poll of %s failed; retrying next tick", web_api_base_url)
        return True  # keep the GLib timer armed

    GLib.timeout_add(int(POLL_INTERVAL_S * 1000), _poll)
    logger.info("bluez_publisher: polling %s every %.1fs", web_api_base_url, POLL_INTERVAL_S)
    GLib.MainLoop().run()


def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Real BlueZ GATT peripheral for unoq-nmea2000")
    parser.add_argument("--web-api", default="http://127.0.0.1:8000", help="Web_API base URL")
    parser.add_argument(
        "--open-access",
        action="store_true",
        help="Disable required pairing (Requirement 28.2's explicit opt-in; default is paired-only)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        run_forever(args.web_api, require_pairing=not args.open_access)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
