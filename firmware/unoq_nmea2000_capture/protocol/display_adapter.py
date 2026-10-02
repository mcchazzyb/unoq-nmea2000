"""DisplayAdapter extensibility stub (Requirement 18.1).

A `DisplayAdapter` consumes live-state/Domain_Model events ONLY -- the same
contract every other transport (BLE_Publisher, Web_API) consumes from the
Live_State_Cache. It is wired into the Ingestion_Service's fan-out step as a
no-op reference implementation, proving the fan-out point is generic enough
for a future transport (e.g. a dedicated hardware display, or the iOS app
described in docs/future-ios.md) without any change to Layer 2/3 code.

This module carries NO hardware or UI import (steering 05) -- it is still
Layer 3 (service-adjacent), consuming only protocol.domain types.
"""
from __future__ import annotations

from typing import Protocol

from protocol.domain import VesselState
from protocol.registry import DecodeResult


class DisplayAdapter(Protocol):
    """Structural contract for anything that renders live Domain_Model state.

    Receives the same `DecodeResult` the Live_State_Cache is updated with,
    plus periodic access to the full `VesselState` snapshot -- NOTHING
    transport-specific (no CAN frame, no BLE characteristic, no HTTP
    request) ever crosses this boundary.
    """

    def on_update(self, result: DecodeResult) -> None:
        """Called once per decoded value, same as Live_State_Cache.update()."""
        ...

    def on_snapshot(self, vessel: VesselState) -> None:
        """Called periodically (or on demand) with the full current state,
        for an adapter that renders a dashboard rather than reacting to
        individual field changes."""
        ...


class NoOpDisplayAdapter:
    """Reference implementation: does nothing. Proves `DisplayAdapter` is
    satisfiable and wireable into the fan-out without requiring any real
    display hardware or UI framework."""

    def on_update(self, result: DecodeResult) -> None:  # pragma: no cover - trivial
        pass

    def on_snapshot(self, vessel: VesselState) -> None:  # pragma: no cover - trivial
        pass
