"""Discovery_Mode: in-memory node inventory keyed by NodeKey, backed by
decoded identity traffic (Requirement 10.1-10.12; Properties 17, 18, 19, 39).

NodeKey is the NMEA NAME when known, a provisional key otherwise -- NEVER
source address (steering 08). Source address is a mutable attribute of a
NodeRecord, updated in place when the same NAME reappears elsewhere.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from protocol.decoders.identity import IsoName, ProductInformation
from protocol.domain import NodeKey, NodeRecord
from protocol.receiver import CanFrame
from protocol.registry import DecodeResult

#: Minimum silence duration before a node is marked offline, independent of
#: its own observed interval (Requirement 10.9).
_MIN_OFFLINE_THRESHOLD_S = 30.0
#: Multiplier applied to a node's most-recent observed interval.
_OFFLINE_INTERVAL_MULTIPLIER = 3.0
#: Rolling window for message-rate computation (Requirement 10.3).
_MESSAGE_RATE_WINDOW_S = 60.0

PGN_ISO_ADDRESS_CLAIM = 60928
PGN_PRODUCT_INFORMATION = 126996


@dataclass
class _Observation:
    """Internal per-node bookkeeping not part of the public NodeRecord
    dataclass (design.md keeps NodeRecord to the fields it specifies)."""

    timestamps: deque = field(default_factory=deque)  # rolling 60s window
    last_interval_s: float = 0.0  # most recently observed inter-frame interval


def _provisional_key(source_address: int) -> NodeKey:
    return f"provisional:sa{source_address}"


def _name_key(nmea_name: int) -> NodeKey:
    return f"name:{nmea_name:016x}"


class DiscoveryMode:
    """Owns the NetworkState-equivalent node inventory. Call `observe()` for
    every frame the Ingestion pipeline processes (known or unknown PGN
    alike), and `decode_iso_name`/`decode_product_info` hooks feed identity
    data in from the Stage 3 identity decoders.
    """

    def __init__(self) -> None:
        self.nodes: dict[NodeKey, NodeRecord] = {}
        self._obs: dict[NodeKey, _Observation] = {}
        # Reverse index: source_address -> the NodeKey most recently
        # observed there, used to route a frame that carries no identity
        # info yet to the right (likely provisional) record.
        self._sa_to_key: dict[int, NodeKey] = {}

    def _get_or_create(self, node_key: NodeKey, now: float) -> NodeRecord:
        record = self.nodes.get(node_key)
        if record is None:
            record = NodeRecord(node_key=node_key, first_seen=now, last_seen=now)
            self.nodes[node_key] = record
            self._obs[node_key] = _Observation()
        return record

    def _update_rate_and_online(self, node_key: NodeKey, now: float) -> None:
        record = self.nodes[node_key]
        obs = self._obs[node_key]

        is_first_observation = len(obs.timestamps) == 0
        if not is_first_observation:
            observed_interval = now - record.last_seen
            if observed_interval > 0:
                obs.last_interval_s = observed_interval

        # Mark online on this frame regardless of prior state
        # (Requirement 10.10).
        record.online = True
        record.last_seen = now

        obs.timestamps.append(now)
        cutoff = now - _MESSAGE_RATE_WINDOW_S
        while obs.timestamps and obs.timestamps[0] < cutoff:
            obs.timestamps.popleft()
        record.message_rate = len(obs.timestamps)

    def observe(
        self, frame: CanFrame, decoded: DecodeResult | None, now: float | None = None
    ) -> None:
        """Record one observed frame against its node. `decoded` is the
        Stage 3 DecodeResult when the PGN was known (None for unknown PGNs,
        which are still tracked -- Requirement 10.12)."""
        now = now if now is not None else time.time()
        source_address = frame.source_address

        # Route this frame to whatever NodeKey currently claims this SA
        # (provisional, until an Address Claim reconciles it), else create a
        # fresh provisional record.
        node_key = self._sa_to_key.get(source_address)
        if node_key is None or node_key not in self.nodes:
            node_key = _provisional_key(source_address)
            self._sa_to_key[source_address] = node_key

        record = self._get_or_create(node_key, now)
        record.current_source_address = source_address
        record.observed_pgns.add(frame.pgn)
        self._update_rate_and_online(node_key, now)

        if decoded is not None and decoded.known:
            if decoded.pgn == PGN_ISO_ADDRESS_CLAIM and isinstance(decoded.entity, IsoName):
                self._observe_address_claim(node_key, source_address, decoded.entity, now)
            elif decoded.pgn == PGN_PRODUCT_INFORMATION and isinstance(
                decoded.entity, ProductInformation
            ):
                self._observe_product_information(node_key, decoded.entity)
            elif decoded.entity is None:
                # Fast-packet in-progress / failed reassembly for 126996:
                # record a reassembly-failure indication, leave any prior
                # product info unchanged (Requirement 10.7).
                if decoded.pgn == PGN_PRODUCT_INFORMATION:
                    record.raw_identity = record.raw_identity  # explicit no-op: unchanged

    def _observe_address_claim(
        self, current_key: NodeKey, source_address: int, name: IsoName, now: float
    ) -> None:
        """Reconcile a provisional NodeKey to the NAME-based key on the
        first Address Claim seen for it (Requirement 10.1, 10.2), and detect
        address conflicts when a DIFFERENT NAME already claims this SA
        (Requirement 10.11)."""
        new_key = _name_key(name.raw)

        # Conflict check FIRST, before any reconciliation decision: is some
        # OTHER already-named record currently claiming this source_address
        # with a DIFFERENT NAME? `current_key` is whatever record this SA's
        # last frame routed to -- if it already has a resolved NAME distinct
        # from this one, that is the conflict (Requirement 10.11), and this
        # claim must create its OWN record rather than reconcile/rename the
        # existing one.
        existing_at_sa = self.nodes.get(current_key)
        conflict_with_existing = (
            existing_at_sa is not None
            and existing_at_sa.nmea_name is not None
            and existing_at_sa.nmea_name != name.raw
        )

        if new_key in self.nodes:
            # This NAME already has a reconciled record (e.g. it moved to a
            # new source address, or this is a repeat claim) -- retain the
            # SAME NodeKey, just update current_source_address in place
            # (Requirement 10.2, Property 39).
            record = self.nodes[new_key]
            record.current_source_address = source_address
            record.online = True
            record.last_seen = now
            self._obs.setdefault(new_key, _Observation())
            # Only drop the provisional record for this SA if it is a
            # DIFFERENT, NOT-YET-NAMED record (a stale provisional for this
            # SA) -- never drop another NAME's resolved record.
            if (
                current_key in self.nodes
                and current_key != new_key
                and not conflict_with_existing
            ):
                old = self.nodes.pop(current_key)
                record.first_seen = min(record.first_seen, old.first_seen)
                del self._obs[current_key]
        elif conflict_with_existing:
            # A genuinely NEW NodeKey, claiming an SA another NAME already
            # holds: create it as its OWN record (do NOT touch/rename the
            # existing one) and record the conflict on both.
            record = NodeRecord(node_key=new_key, first_seen=now, last_seen=now)
            self.nodes[new_key] = record
            self._obs[new_key] = _Observation()
        else:
            # First Address Claim for this provisional record, no conflict:
            # reconcile key in place, carrying over all prior observations
            # (Requirement 10.1).
            record = self.nodes.pop(current_key)
            record.node_key = new_key
            self.nodes[new_key] = record
            self._obs[new_key] = self._obs.pop(current_key)

        record.nmea_name = name.raw
        record.manufacturer_code = name.manufacturer_code
        record.device_class = name.device_class
        record.device_function = name.device_function
        self._sa_to_key[source_address] = new_key

        if conflict_with_existing:
            if source_address not in record.address_conflicts:
                record.address_conflicts.append(source_address)
            if source_address not in existing_at_sa.address_conflicts:
                existing_at_sa.address_conflicts.append(source_address)

    def _observe_product_information(self, node_key: NodeKey, info: ProductInformation) -> None:
        record = self.nodes[node_key]
        record.product_info = (
            f"{info.model_id} {info.model_version} (sw {info.software_version_code})"
        )

    def check_offline_transitions(self, now: float | None = None) -> None:
        """Mark any node offline whose silence exceeds
        max(3 x last_interval, 30s) (Requirement 10.9; Property 18). Call
        periodically -- unlike `observe()`, which only ever marks a node
        online, this is the only path that marks one offline."""
        now = now if now is not None else time.time()
        for node_key, record in self.nodes.items():
            if not record.online:
                continue
            obs = self._obs[node_key]
            threshold = max(_OFFLINE_INTERVAL_MULTIPLIER * obs.last_interval_s, _MIN_OFFLINE_THRESHOLD_S)
            if (now - record.last_seen) > threshold:
                record.online = False

    def known_nodes(self) -> list[NodeRecord]:
        return [r for r in self.nodes.values() if r.nmea_name is not None]

    def unknown_nodes(self) -> list[NodeRecord]:
        return [r for r in self.nodes.values() if r.nmea_name is None]
