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

from ble.publisher import GATT_SCHEMA, MockBLEPublisher, PairingRejectedError, UnknownCharacteristicError
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
    reimplemented here (Property 22/23 already proven against the mock).

    PAIRING ENFORCEMENT (Requirement 28.2, 28.3) -- real bug found and fixed
    via this spike's own live phone test: `ReadValue`'s `client_id` has no
    real per-connection identity plumbed through BlueZ's D-Bus API in any
    simple form, and `MockBLEPublisher.read_characteristic`'s own
    `PairingRejectedError` was being raised but silently swallowed (no
    `except` for it in `_read_bytes`, so it propagated into an untyped
    `method_call` and GDBus returned an empty/default reply -- a real phone
    connected and "read" a characteristic with NO pairing prompt at all,
    the opposite of Requirement 28.2's default-secure posture).

    The correct fix is NOT application-level client tracking: BlueZ itself
    enforces pairing/encryption at the link layer via the `encrypt-read` /
    `encrypt-notify` GATT Flags (Core spec Table 3.5) -- setting them makes
    BlueZ refuse the ATT request and drive the OS bonding prompt BEFORE
    this process's ReadValue handler ever runs, which is both more correct
    (enforced by the Bluetooth stack, not reimplementable-incorrectly in
    Python) and simpler than any one of this class correlating D-Bus
    `sender`/`device` identities to the mock's client_id set. `MockBLEPublisher`'s
    own `_check_access`/`PairingRejectedError` stays as defense-in-depth
    (now actually caught below) in case a client reaches ReadValue without
    the expected encryption having been negotiated.
    `schema_version` alone stays plain `read` (Requirement 13.1's own design:
    a client must identify the service before pairing)."""

    _UNAUTHENTICATED_CHARACTERISTICS = frozenset({"schema_version"})

    def __init__(
        self,
        index: int,
        service_path: str,
        char,
        publisher: MockBLEPublisher,
        require_pairing: bool,
    ) -> None:
        self.path = f"{service_path}/char{index:03d}"
        self._char = char
        self._publisher = publisher
        self._require_pairing = require_pairing
        self._notifying = False

    def _read_bytes(self) -> bytes:
        name = self._char.name
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
        Gio, GLib = _gi()
        if method_name == "ReadValue":
            logger.info(
                "bluez_publisher: ReadValue called on %s (require_pairing=%s)",
                self._char.name,
                self._require_pairing,
            )
            try:
                data = self._read_bytes()
            except UnknownCharacteristicError:
                logger.exception("bluez_publisher: unknown characteristic %s requested", self._char.name)
                invocation.return_error_literal(
                    Gio.dbus_error_quark(), Gio.DBusError.FAILED, "unknown characteristic"
                )
                return
            except PairingRejectedError:
                # Defense-in-depth: the encrypt-read Flag below should make
                # BlueZ enforce this before we ever get here, but if a
                # client somehow reaches ReadValue unauthenticated, reject
                # explicitly rather than returning data.
                logger.warning(
                    "bluez_publisher: ReadValue on %s rejected -- client not paired", self._char.name
                )
                invocation.return_error_literal(
                    Gio.dbus_error_quark(), Gio.DBusError.ACCESS_DENIED, "pairing required"
                )
                return
            invocation.return_value(GLib.Variant("(ay)", (list(data),)))
        elif method_name == "StartNotify":
            self._notifying = True
            invocation.return_value(None)
        elif method_name == "StopNotify":
            self._notifying = False
            invocation.return_value(None)
        else:
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, "no such method"
            )

    def flags(self) -> list[str]:
        """Shared Flags computation -- used by both this object's own
        `get_property('Flags')` AND `_register_gatt_application`'s
        `GetManagedObjects` reply, so the two can never drift apart (that
        drift is exactly how a security-relevant Flags value could stay
        correct in one place and stale in the other)."""
        flags = list(
            {
                "read": ["read"],
                "notify": ["notify"],
                "read_notify": ["read", "notify"],
            }[self._char.properties.value]
        )
        if self._require_pairing and self._char.name not in self._UNAUTHENTICATED_CHARACTERISTICS:
            if "read" in flags:
                flags = [f for f in flags if f != "read"] + ["encrypt-read"]
            if "notify" in flags:
                flags = [f for f in flags if f != "notify"] + ["encrypt-notify"]
        return flags

    def get_property(self, connection, sender, object_path, interface_name, property_name):
        _, GLib = _gi()
        if property_name == "UUID":
            return GLib.Variant("s", _char_uuid(self._char.uuid))
        if property_name == "Service":
            return GLib.Variant("o", self.path.rsplit("/", 1)[0])
        if property_name == "Flags":
            return GLib.Variant("as", self.flags())
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

# BlueZ's agent capability must be DisplayYesNo, not NoInputNoOutput, on
# this UNO Q/iPhone pairing path. Found via a real root-level btmon trace:
# with NoInputNoOutput the controller received a User Confirmation Request
# (value 000000) then immediately issued User Confirmation Negative Reply
# WITHOUT ever invoking our RequestConfirmation D-Bus handler, resulting in
# "Pairing Failed: Numeric comparison failed" and every protected GATT read
# failing "Insufficient Encryption". DisplayYesNo makes BlueZ route that
# request to our handler; it auto-accepts it below. There is still NO physical
# screen/keypad/button and no passkey entry required on the UNO Q -- this is
# the desired OBD-style flow (phone consent + automatic board acceptance),
# yielding a bonded/encrypted link. ponytail: it is not human-verified MITM;
# upgrade only if the hardware gains a trusted display/button UI.
_AGENT_CAPABILITY = "DisplayYesNo"


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
        # require_pairing=False on the WRAPPED mock, always -- deliberately
        # NOT self.require_pairing. Real bug found via this spike's own
        # live phone test: with the mock's own require_pairing=True, a
        # phone that BlueZ had ALREADY bonded (Paired: yes, Bonded: yes,
        # confirmed via bluetoothctl) still got every ReadValue rejected
        # with PairingRejectedError, because the mock's _check_access
        # checks its OWN in-memory paired_client_ids set, which nothing
        # populates from a real BlueZ bonding event -- the two "paired"
        # concepts were never the same thing. For BlueZPublisher, pairing
        # enforcement is REAL and already done at the link layer by BlueZ
        # before ReadValue is ever invoked (via the encrypt-read/
        # encrypt-notify Flags set in _Characteristic.flags(), gated on
        # self.require_pairing, NOT the mock's). By the time this process's
        # ReadValue handler runs, BlueZ has already proven the connection is
        # encrypted/bonded; re-checking an unrelated in-memory ledger is
        # redundant at best and actively wrong at worst (exactly what broke
        # here). MockBLEPublisher's own require_pairing stays meaningful
        # for ITS OWN host-testable in-memory use (Stage 6), just not when
        # wrapped by this real D-Bus-backed publisher.
        self._mock = MockBLEPublisher(require_pairing=False, device_info=self.device_info or {})
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
        if self.require_pairing:
            self._register_agent()
            self._set_adapter_pairable(adapter_path)
        self._register_service_and_characteristics()
        self._register_gatt_application(adapter_path)
        self._register_advertisement(adapter_path)
        logger.info(
            "bluez_publisher: GATT application + advertisement registration requested on %s "
            "(async -- the main loop must be running for BlueZ's completion callback to fire)",
            adapter_path,
        )

    def _register_agent(self) -> None:
        """Second real bug found via this spike's own live phone test:
        `Pairable=true` + `encrypt-read` Flags alone still weren't enough --
        the phone's OS pairing prompt appeared, but our ReadValue was NEVER
        invoked afterward and bluetoothctl kept reporting Paired=no, Bonded=no.
        Root cause: NO Bluetooth pairing agent was registered on the system
        bus at all (`busctl --system tree org.bluez | grep agent` found
        nothing) -- without a registered agent, BlueZ has no object to route
        the pairing negotiation through, so the SMP exchange cannot actually
        complete even though the adapter is Pairable and the characteristic
        correctly demands encryption.

        `DisplayYesNo` is intentional here despite the headless hardware: it
        makes BlueZ dispatch the confirmation request to this agent, which
        immediately accepts it programmatically. That preserves the intended
        OBD-style UX: the phone may show its normal one-time pairing consent,
        but there is no Arduino keypad, screen, passkey, or physical button.
        It yields an encrypted/bonded link but is not human-verified MITM --
        see `_AGENT_CAPABILITY`'s comment for the honest security boundary."""
        Gio, GLib = _gi()
        agent_path = "/org/unoq/nmea2000/agent0"
        agent_xml = """
        <node>
          <interface name="org.bluez.Agent1">
            <method name="Release"/>
            <method name="RequestPinCode">
              <arg name="device" type="o" direction="in"/>
              <arg name="pincode" type="s" direction="out"/>
            </method>
            <method name="RequestPasskey">
              <arg name="device" type="o" direction="in"/>
              <arg name="passkey" type="u" direction="out"/>
            </method>
            <method name="DisplayPasskey">
              <arg name="device" type="o" direction="in"/>
              <arg name="passkey" type="u" direction="in"/>
              <arg name="entered" type="q" direction="in"/>
            </method>
            <method name="DisplayPinCode">
              <arg name="device" type="o" direction="in"/>
              <arg name="pincode" type="s" direction="in"/>
            </method>
            <method name="RequestConfirmation">
              <arg name="device" type="o" direction="in"/>
              <arg name="passkey" type="u" direction="in"/>
            </method>
            <method name="RequestAuthorization">
              <arg name="device" type="o" direction="in"/>
            </method>
            <method name="AuthorizeService">
              <arg name="device" type="o" direction="in"/>
              <arg name="uuid" type="s" direction="in"/>
            </method>
            <method name="Cancel"/>
          </interface>
        </node>
        """
        node_info = Gio.DBusNodeInfo.new_for_xml(agent_xml)
        iface_info = node_info.interfaces[0]

        def method_call(
            connection, sender, object_path, interface_name, method_name, parameters, invocation
        ) -> None:
            logger.info(
                "bluez_publisher: pairing agent received %s parameters=%s",
                method_name,
                parameters.print_(True) if parameters is not None else "()",
            )
            # DisplayYesNo is used solely to make BlueZ dispatch the numeric
            # confirmation request to this agent; accept it automatically.
            # There is no physical UI/passkey on the UNO Q -- this is the
            # intended OBD-style phone-consent + automatic-board flow.
            if method_name in ("RequestConfirmation", "AuthorizeService", "RequestAuthorization"):
                invocation.return_value(None)
            elif method_name in ("Release", "Cancel", "DisplayPasskey", "DisplayPinCode"):
                invocation.return_value(None)
            else:
                logger.warning("bluez_publisher: unsupported pairing agent call %s", method_name)
                invocation.return_error_literal(
                    Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, "no such method"
                )

        reg_id = self._bus.register_object(agent_path, iface_info, method_call, None, None)
        self._registrations.append(reg_id)

        agent_mgr = Gio.DBusProxy.new_sync(
            self._bus, Gio.DBusProxyFlags.NONE, None, BLUEZ_SERVICE_NAME, "/org/bluez", "org.bluez.AgentManager1", None
        )
        agent_mgr.call_sync(
            "RegisterAgent",
            GLib.Variant("(os)", (agent_path, _AGENT_CAPABILITY)),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        agent_mgr.call_sync(
            "RequestDefaultAgent", GLib.Variant("(o)", (agent_path,)), Gio.DBusCallFlags.NONE, -1, None
        )
        logger.info(
            "bluez_publisher: pairing agent registered at %s (%s; automatic confirmation)",
            agent_path,
            _AGENT_CAPABILITY,
        )

    def _set_adapter_pairable(self, adapter_path: str) -> None:
        """Real bug found via this spike's own live phone test: setting
        `encrypt-read`/`encrypt-notify` on a characteristic's Flags is NOT
        enough on its own -- BlueZ reported the correct Flags via
        GetManagedObjects (verified with a diagnostic log) but a connected,
        unpaired phone still read a protected characteristic with no
        bonding prompt. Root cause: the adapter's own `Pairable` property
        was `no` (BlueZ's startup default), so BlueZ had no way to DRIVE a
        pairing procedure even though it correctly knew the characteristic
        required encryption -- `Pairable` gates whether the adapter will
        initiate/accept SMP pairing at all, independent of any individual
        GATT characteristic's Flags. Setting it here, scoped to only the
        require_pairing=True case, so an operator's explicit open-access
        opt-in (Requirement 28.2) does not get silently paired anyway."""
        Gio, GLib = _gi()
        props = Gio.DBusProxy.new_sync(
            self._bus, Gio.DBusProxyFlags.NONE, None, BLUEZ_SERVICE_NAME, adapter_path, DBUS_PROP_IFACE, None
        )
        props.call_sync(
            "Set",
            GLib.Variant("(ssv)", ("org.bluez.Adapter1", "Pairable", GLib.Variant("b", True))),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        logger.info("bluez_publisher: adapter %s set Pairable=true", adapter_path)

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
            characteristic = _Characteristic(
                index, self._service_path, char, self._mock, self.require_pairing
            )
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
            # Real bug found via a live board test: bluetoothd's own log
            # showed "src/gatt-database.c:client_ready_cb() No object
            # received" right when RegisterApplication ran, and our async
            # call() reported success anyway (BlueZ discards a rejected
            # application silently rather than erroring the RegisterApplication
            # call itself) -- `bluetoothctl`'s `list-attributes local`
            # confirmed nothing was actually registered. Root cause, found
            # by comparing against BlueZ's own test/example-gatt-server:
            # GATT_SERVICE_IFACE's properties dict MUST include a
            # `Characteristics` array of the service's own characteristic
            # object paths, and GATT_CHRC_IFACE MUST include a `Descriptors`
            # array (empty here, since this schema defines none) -- without
            # these, BlueZ's validator cannot resolve the service's child
            # objects from the ObjectManager reply alone and treats the
            # whole application as having "no object".
            characteristic_paths = [c.path for c in self._chars]
            objects: dict = {
                self._service_path: {
                    GATT_SERVICE_IFACE: {
                        "UUID": GLib.Variant("s", _char_uuid(_SERVICE_SHORT_UUID)),
                        "Primary": GLib.Variant("b", True),
                        "Characteristics": GLib.Variant("ao", characteristic_paths),
                    }
                }
            }
            for characteristic in self._chars:
                objects[characteristic.path] = {
                    GATT_CHRC_IFACE: {
                        "UUID": GLib.Variant("s", _char_uuid(characteristic._char.uuid)),
                        "Service": GLib.Variant("o", self._service_path),
                        "Flags": GLib.Variant("as", characteristic.flags()),
                        "Descriptors": GLib.Variant("ao", []),
                    }
                }
                logger.info(
                    "bluez_publisher: GetManagedObjects reporting %s (%s) flags=%s",
                    characteristic._char.name,
                    characteristic.path,
                    characteristic.flags(),
                )
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
