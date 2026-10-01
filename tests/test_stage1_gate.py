"""Stage 1 Gate — safety-critical types and domain model.

Validates:
  - Property 28 (structural half): ICanReceiver exposes no transmit-capable
    method, so the platform is receive-only by construction.
  - Property 13: UNSET is distinguishable from None and from 0 for every
    optional field on every domain entity.

Also pins CanFrame / CanHealth to the design.md shape, including the MVP
invariant that CanHealth.termination_on defaults False.
"""
from __future__ import annotations

import dataclasses

from protocol.receiver import CanFrame, CanHealth, ICanReceiver
from protocol.domain import (
    UNSET,
    ElectricalState,
    EngineState,
    NavigationState,
    TankState,
    DeviceHealth,
)


# --- Property 28 (structural): receive-only by construction ----------------


def test_ican_receiver_exposes_no_transmit_method():
    """Feature: nmea2000-diagnostic-platform, Property 28: the platform transmits
    zero frames — enforced structurally by ICanReceiver having no send/transmit/
    write member."""
    forbidden = {"send", "transmit", "write", "send_frame", "write_frame"}
    members = {name for name in dir(ICanReceiver) if not name.startswith("_")}
    assert forbidden.isdisjoint(members), (
        f"ICanReceiver exposes forbidden transmit-like member(s): "
        f"{forbidden & members}"
    )
    # Positive: the only two methods it DOES expose are the receive path.
    assert "frames" in members
    assert "health" in members


# --- CanFrame / CanHealth shape and the termination-off MVP invariant ------


def test_canframe_is_frozen_with_design_fields():
    f = CanFrame(
        can_id=0x19F21423,
        priority=6,
        pgn=127505,
        source_address=0x23,
        dest_address=None,
        dlc=8,
        payload=b"\x00\x01\x02\x03\x04\x05\x06\x07",
        capture_time=1.5,
    )
    assert f.capture_time == 1.5
    # frozen: mutation raises
    try:
        f.pgn = 0  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("CanFrame must be frozen")
    assert f.wall_clock_time is None
    assert f.malformed is False


def test_canhealth_termination_on_must_be_false_in_mvp():
    """MVP invariant (Requirement 1.5, 24.4): the device never enables its
    switchable termination. A CanHealth constructed for the MVP reports
    termination_on False."""
    h = CanHealth(
        connected=True,
        fps=12.0,
        dropped_frames=0,
        error_count=0,
        controller_state="listen-only",
        termination_on=False,
    )
    assert h.termination_on is False


# --- Property 13: UNSET distinguishable from None and 0 --------------------

# (entity_class, {optional_field: a non-UNSET decoded value to contrast}).
# Required (non-optional) fields are supplied so the entity can be constructed.
_ENTITY_OPTIONAL_FIELDS = {
    TankState: (
        {"tank_instance": 0},
        {
            "fluid_type": 1,
            "level_percent": 0.0,
            "capacity_litres": 0.0,
            "quantity_remaining_litres": 0.0,
        },
    ),
    EngineState: (
        {"engine_instance": 0},
        {
            "rpm": 0.0,
            "coolant_temp": 0.0,
            "oil_pressure": 0.0,
            "alternator_voltage": 0.0,
            "engine_hours": 0.0,
            "fuel_rate": 0.0,
            "trim": 0.0,
            "status": 0,
        },
    ),
    ElectricalState: (
        {},
        {
            "battery_voltage": 0.0,
            "charging_voltage": 0.0,
            "current": 0.0,
            "battery_state": 0,
            "dc_source": 0,
        },
    ),
    NavigationState: (
        {},
        {
            "position": (0.0, 0.0),
            "cog": 0.0,
            "sog": 0.0,
            "heading": 0.0,
            "depth": 0.0,
            "water_temp": 0.0,
            "wind": 0.0,
        },
    ),
    DeviceHealth: (
        {},
        {
            "product_name": "",
            "sw_version": "",
            "fw_version": "",
            "device_id": "",
            "uptime": 0.0,
        },
    ),
}


def test_unset_is_not_none_and_not_zero():
    """Feature: nmea2000-diagnostic-platform, Property 13: UNSET is a distinct
    sentinel — not None, not 0, not False-as-value."""
    assert UNSET is not None
    assert UNSET != None  # noqa: E711 — explicitly testing != None
    assert UNSET != 0
    assert UNSET != 0.0
    assert UNSET != False  # noqa: E712
    assert not UNSET  # falsy, but still distinct from the above
    # Idempotent singleton
    from protocol.domain import _UnsetType

    assert _UnsetType() is UNSET


def test_every_optional_field_defaults_unset_and_is_distinguishable():
    """Feature: nmea2000-diagnostic-platform, Property 13: every optional field on
    every domain entity defaults to UNSET and is distinguishable from None and 0,
    and can hold a decoded 0 that is NOT confused with UNSET."""
    for entity_cls, (required, optionals) in _ENTITY_OPTIONAL_FIELDS.items():
        default = entity_cls(**required)
        for field_name, zero_value in optionals.items():
            # 1. Default is exactly UNSET (not None, not 0).
            got = getattr(default, field_name)
            assert got is UNSET, (
                f"{entity_cls.__name__}.{field_name} defaults to {got!r}, "
                f"expected UNSET"
            )
            assert got is not None
            assert got != 0

            # 2. A decoded zero is stored as a real 0, distinguishable from UNSET.
            with_zero = entity_cls(**required, **{field_name: zero_value})
            stored = getattr(with_zero, field_name)
            assert stored == zero_value
            assert stored is not UNSET, (
                f"{entity_cls.__name__}.{field_name} decoded zero was confused "
                f"with UNSET"
            )

            # 3. A decoded None (where a field is explicitly 'not applicable') is
            #    distinguishable from UNSET too.
            with_none = entity_cls(**required, **{field_name: None})
            assert getattr(with_none, field_name) is None
            assert getattr(with_none, field_name) is not UNSET
