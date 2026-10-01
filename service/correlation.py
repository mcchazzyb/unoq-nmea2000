"""Correlation_Mode: guided workflow to confirm decoded fields match
physical behaviour (Requirement 16.1-16.9).

Each recorded physical action captures three windows -- baseline-before,
action, after (Requirement 16.7) -- against a stream of per-field value
observations (field_name -> list of (timestamp, value)). A field is a
CANDIDATE for this action iff its value changed at least once strictly
within the action window; every change outside the action window is
unrelated traffic, never a candidate (Requirement 16.1-16.3).

A field becomes CONFIRMED only after >=2 repeated actions produce a
CONSISTENT candidate change for that field (Requirement 16.8); a single
action, or inconsistent repeats, leaves it an unconfirmed candidate
(Requirement 16.9). A field in a plausible range with no correlating action
at all is marked unconfirmed, never confirmed (Requirement 16.4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Observation:
    timestamp: float
    value: Any


@dataclass(frozen=True)
class ActionWindow:
    """The three windows a recorded physical action spans (Requirement
    16.7): baseline_before precedes the action, action is when the operator
    performed it, after follows it. Only `action_start`/`action_end` decide
    candidacy (Requirement 16.1-16.3); the baseline/after windows exist so a
    caller can show the operator the before/after context, and so this
    module's scoring can penalize a field that was ALSO drifting outside the
    action window (see `_strength` below)."""

    action_label: str
    baseline_before_start: float
    baseline_before_end: float
    action_start: float
    action_end: float
    after_start: float
    after_end: float


@dataclass
class CandidateResult:
    field_name: str
    correlation_strength: float  # [0.0, 1.0]
    changes_in_window: int


@dataclass
class RecordedActionResult:
    action_label: str
    window: ActionWindow
    candidates: list[CandidateResult]
    no_correlation_found: bool  # True iff candidates is empty (Requirement 16.5)


def _changes_within(
    observations: list[Observation], start: float, end: float
) -> list[Observation]:
    """Observations whose timestamp falls strictly within [start, end] AND
    whose value differs from the immediately preceding observation (a
    repeated identical value is not a "change")."""
    changes = []
    prev_value = None
    have_prev = False
    for obs in observations:
        is_change = have_prev and obs.value != prev_value
        if start <= obs.timestamp <= end and is_change:
            changes.append(obs)
        prev_value = obs.value
        have_prev = True
    return changes


def _strength(
    in_window_changes: list[Observation],
    outside_window_changes: list[Observation],
) -> float:
    """Correlation strength in [0.0, 1.0] (Requirement 16.1).

    Judgment call (no exact formula is specified in design.md/requirements.md):
    strength scales with how EXCLUSIVELY the field's changes are confined to
    the action window. A field that changed only during the action scores
    1.0; a field that also drifted outside the window (unrelated traffic
    coinciding with the same field) scores lower in proportion to how much
    of its total observed change activity fell outside the window. This
    rewards fields a physical action plausibly controls and penalizes
    coincidental correlation, without needing domain-specific calibration
    data this MVP does not have.
    """
    total = len(in_window_changes) + len(outside_window_changes)
    if total == 0:
        return 0.0
    return len(in_window_changes) / total


class CorrelationMode:
    """Accumulates recorded actions and their results, and tracks
    confirmation state across repeated actions for the same field."""

    def __init__(self, confirmation_threshold: float = 0.7) -> None:
        self.confirmation_threshold = confirmation_threshold
        # field_name -> list of (action_label, was_candidate, change_direction)
        # across every recorded action, used to decide confirmation.
        self._history: dict[str, list[tuple[str, bool, Any]]] = {}
        self.confirmed_fields: set[str] = set()

    def record_action(
        self,
        window: ActionWindow,
        observations: dict[str, list[Observation]],
    ) -> RecordedActionResult:
        """Record one physical action against per-field observation
        streams. Returns the candidates found and updates confirmation
        state for fields with a consistent repeated history.
        """
        candidates: list[CandidateResult] = []

        for field_name, obs_list in observations.items():
            in_window = _changes_within(obs_list, window.action_start, window.action_end)
            if not in_window:
                # No change in the action window -- not a candidate
                # (Requirement 16.1-16.3). Still record a non-candidate
                # history entry so inconsistent-repeat detection has data.
                self._history.setdefault(field_name, []).append(
                    (window.action_label, False, None)
                )
                continue

            before_window_changes = _changes_within(
                obs_list, window.baseline_before_start, window.baseline_before_end
            )
            after_window_changes = _changes_within(obs_list, window.after_start, window.after_end)
            outside = before_window_changes + after_window_changes
            strength = _strength(in_window, outside)

            candidates.append(
                CandidateResult(
                    field_name=field_name,
                    correlation_strength=strength,
                    changes_in_window=len(in_window),
                )
            )

            # Direction of change (sign of numeric delta, or the raw value
            # for non-numeric fields) is what "consistent" means across
            # repeats (Requirement 16.8/16.9) -- the SAME kind of change,
            # not necessarily the identical value. Computed from the value
            # immediately before the window to the last in-window value, so
            # a window with only one recorded change still has a real
            # before/after pair to compare (not just the single in-window
            # point compared to itself).
            direction = _change_direction(obs_list, window.action_start, window.action_end)
            self._history.setdefault(field_name, []).append(
                (window.action_label, True, direction)
            )

        self._update_confirmations()

        return RecordedActionResult(
            action_label=window.action_label,
            window=window,
            candidates=candidates,
            no_correlation_found=(len(candidates) == 0),
        )

    def _update_confirmations(self) -> None:
        """A field is CONFIRMED only once >=2 recorded actions produced a
        candidate change for it, and those changes are CONSISTENT (same
        direction) across every one of those repeats (Requirement 16.8).
        A single action, or any inconsistency among the repeats, leaves it
        unconfirmed (Requirement 16.9) -- confirmation, once granted, is not
        revoked by a LATER inconsistent action either; re-evaluate from
        full history each time so a genuinely inconsistent field never
        becomes confirmed in the first place.
        """
        for field_name, entries in self._history.items():
            candidate_entries = [(label, direction) for label, was_candidate, direction in entries if was_candidate]
            if len(candidate_entries) < 2:
                continue
            directions = {direction for _, direction in candidate_entries}
            if len(directions) == 1:
                self.confirmed_fields.add(field_name)
            else:
                # Inconsistent repeats -- never confirmed, even if an
                # earlier pair looked consistent before this entry arrived.
                self.confirmed_fields.discard(field_name)

    def is_confirmed(self, field_name: str) -> bool:
        return field_name in self.confirmed_fields

    def status_for(self, field_name: str, latest_strength: float) -> str:
        """Human-facing status for a field after a recorded action:
        "confirmed", "candidate" (above threshold but not yet repeated/
        consistent enough), or "unconfirmed" (Requirement 16.4, 16.6)."""
        if self.is_confirmed(field_name):
            return "confirmed"
        if latest_strength >= self.confirmation_threshold:
            return "candidate"
        return "unconfirmed"


def _change_direction(observations: list[Observation], window_start: float, window_end: float) -> Any:
    """A stable summary of "which way did this field move" during the
    action window, for consistency checking across repeats. Compares the
    LAST value observed before the window started against the LAST value
    observed by the window's end -- not merely the in-window points
    compared to each other, since a window can contain only one recorded
    change with nothing else in it to diff against.

    Numeric values: "increase" / "decrease" / "unchanged" by sign of delta.
    Non-numeric: the final value reached by the window's end.
    """
    before_value = None
    have_before = False
    end_value = None
    have_end = False
    for obs in observations:
        if obs.timestamp < window_start:
            before_value = obs.value
            have_before = True
        if obs.timestamp <= window_end:
            end_value = obs.value
            have_end = True

    if not have_end:
        return None
    if not have_before:
        # No observation before the window -- compare against the window's
        # own first observation instead, since that is the best available
        # "before" reference.
        in_window = [o for o in observations if window_start <= o.timestamp <= window_end]
        before_value = in_window[0].value if in_window else end_value

    if isinstance(before_value, (int, float)) and isinstance(end_value, (int, float)):
        delta = end_value - before_value
        if delta > 0:
            return "increase"
        if delta < 0:
            return "decrease"
        return "unchanged"
    return end_value
