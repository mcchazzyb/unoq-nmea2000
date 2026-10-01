"""Stage 7.3 Gate -- Correlation_Mode: candidates exactly in-window
changers, strength bounds, confirmation threshold, repetition-based
confirmation.

Property tags follow .kiro/steering/09-property-test-tagging.md exactly.
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from service.correlation import ActionWindow, CorrelationMode, Observation


def _window(label="trim_down", before=(0.0, 10.0), action=(10.0, 20.0), after=(20.0, 30.0)):
    return ActionWindow(
        action_label=label,
        baseline_before_start=before[0],
        baseline_before_end=before[1],
        action_start=action[0],
        action_end=action[1],
        after_start=after[0],
        after_end=after[1],
    )


# --- Property 20: candidates exactly in-window changers, bounded strength --


@given(
    change_time=st.floats(min_value=0.0, max_value=30.0),
    base_value=st.integers(min_value=0, max_value=100),
    new_value=st.integers(min_value=0, max_value=100),
)
@settings(max_examples=100)
def test_candidate_exactly_when_change_is_within_action_window(change_time, base_value, new_value):
    """Feature: nmea2000-diagnostic-platform, Property 20: candidate fields
    are exactly those whose value changed within the window; all other
    changes are classified unrelated, and every candidate's strength lies
    in [0.0, 1.0]."""
    if base_value == new_value:
        new_value = base_value + 1  # ensure a genuine change happens

    window = _window()
    observations = {
        "field_a": [
            Observation(timestamp=0.0, value=base_value),
            Observation(timestamp=change_time, value=new_value),
        ]
    }
    result = CorrelationMode().record_action(window, observations)
    is_in_window = window.action_start <= change_time <= window.action_end
    candidate_names = {c.field_name for c in result.candidates}

    if is_in_window:
        assert "field_a" in candidate_names
    else:
        assert "field_a" not in candidate_names

    for c in result.candidates:
        assert 0.0 <= c.correlation_strength <= 1.0


def test_change_outside_window_is_unrelated_not_a_candidate():
    window = _window()
    observations = {
        "field_a": [
            Observation(timestamp=5.0, value=10),  # changes in baseline-before window
            Observation(timestamp=25.0, value=20),  # changes again in after window
        ]
    }
    result = CorrelationMode().record_action(window, observations)
    assert result.candidates == []
    assert result.no_correlation_found is True


# --- Requirement 16.5: zero-candidate report path --------------------------


def test_zero_candidates_reports_no_correlation_found():
    """Feature: nmea2000-diagnostic-platform -- Requirement 16.5: no
    candidate changes within the window reports zero candidates and
    no-correlation-found, with no confirmed label."""
    window = _window()
    observations = {"field_a": [Observation(timestamp=0.0, value=42)]}  # never changes
    mode = CorrelationMode()
    result = mode.record_action(window, observations)
    assert result.no_correlation_found is True
    assert result.candidates == []
    assert mode.is_confirmed("field_a") is False


# --- Property 21: confirmation requires meeting the threshold --------------


def test_strength_below_threshold_is_candidate_not_confirmed():
    """Feature: nmea2000-diagnostic-platform, Property 21: a candidate below
    the confirmation threshold is presented as unconfirmed/candidate, never
    confirmed, regardless of threshold value."""
    window = _window()
    # Field changes both inside AND outside the window -> diluted strength.
    observations = {
        "field_a": [
            Observation(timestamp=5.0, value=1),  # before-window change
            Observation(timestamp=15.0, value=2),  # in-window change
            Observation(timestamp=25.0, value=3),  # after-window change
        ]
    }
    mode = CorrelationMode(confirmation_threshold=0.9)
    result = mode.record_action(window, observations)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.correlation_strength < 0.9
    assert mode.status_for("field_a", candidate.correlation_strength) != "confirmed"


def test_strength_above_threshold_single_action_is_candidate_not_confirmed():
    """Requirement 16.4/16.6: even a strong single-action correlation stays
    a candidate -- confirmation needs REPETITION (Requirement 16.8), not
    just a high strength score."""
    window = _window()
    observations = {
        "field_a": [Observation(timestamp=5.0, value=0), Observation(timestamp=15.0, value=99)]
    }
    mode = CorrelationMode(confirmation_threshold=0.5)
    result = mode.record_action(window, observations)
    assert result.candidates[0].correlation_strength == 1.0
    assert mode.is_confirmed("field_a") is False
    assert mode.status_for("field_a", 1.0) == "candidate"


# --- Property 36: confirmation requires >=2 consistent repeats ------------


def test_two_consistent_repeats_confirm_the_field():
    """Feature: nmea2000-diagnostic-platform, Property 36: confirmation
    requires at least two repeated actions with a consistent candidate
    change."""
    mode = CorrelationMode()
    window1 = _window(label="trim_down_1")
    obs1 = {"trim": [Observation(timestamp=0.0, value=0), Observation(timestamp=15.0, value=-5)]}
    mode.record_action(window1, obs1)
    assert mode.is_confirmed("trim") is False  # one action: not yet confirmed

    window2 = _window(label="trim_down_2")
    obs2 = {"trim": [Observation(timestamp=0.0, value=-5), Observation(timestamp=15.0, value=-10)]}
    mode.record_action(window2, obs2)
    assert mode.is_confirmed("trim") is True  # two consistent decreases: confirmed


def test_single_action_never_confirms():
    """Requirement 16.9: a single recorded action leaves the field an
    unconfirmed candidate, never confirmed."""
    mode = CorrelationMode()
    window = _window()
    obs = {"trim": [Observation(timestamp=0.0, value=0), Observation(timestamp=15.0, value=-5)]}
    mode.record_action(window, obs)
    assert mode.is_confirmed("trim") is False


def test_inconsistent_repeats_never_confirm():
    """Requirement 16.9: repeated actions producing INCONSISTENT candidate
    changes (one increase, one decrease) leave the field unconfirmed."""
    mode = CorrelationMode()
    window1 = _window(label="action_1")
    obs1 = {"trim": [Observation(timestamp=0.0, value=0), Observation(timestamp=15.0, value=-5)]}  # decrease
    mode.record_action(window1, obs1)

    window2 = _window(label="action_2")
    obs2 = {"trim": [Observation(timestamp=0.0, value=-5), Observation(timestamp=15.0, value=10)]}  # increase
    mode.record_action(window2, obs2)

    assert mode.is_confirmed("trim") is False


def test_three_repeats_two_consistent_one_inconsistent_never_confirms():
    """A field is never confirmed while ANY recorded repeat disagrees with
    the others, even if two of three happen to agree."""
    mode = CorrelationMode()
    for i, delta in enumerate([-5, -5, +5]):  # two decreases, one increase
        window = _window(label=f"action_{i}")
        obs = {"trim": [Observation(timestamp=0.0, value=0), Observation(timestamp=15.0, value=delta)]}
        mode.record_action(window, obs)
    assert mode.is_confirmed("trim") is False


@given(
    deltas=st.lists(st.sampled_from([-5, -3, -1]), min_size=2, max_size=6)  # all consistent decreases
)
@settings(max_examples=30)
def test_any_number_of_consistent_repeats_confirms(deltas):
    """Feature: nmea2000-diagnostic-platform, Property 36: ANY number of
    repeats >= 2, all consistent, confirms the field."""
    mode = CorrelationMode()
    for i, delta in enumerate(deltas):
        window = _window(label=f"action_{i}", action=(10.0 * i + 10, 10.0 * i + 20))
        obs = {"trim": [
            Observation(timestamp=10.0 * i, value=0),
            Observation(timestamp=10.0 * i + 15, value=delta),
        ]}
        mode.record_action(window, obs)
    assert mode.is_confirmed("trim") is True


# --- Requirement 16.4: plausible-but-uncorrelated field stays unconfirmed --


def test_field_with_no_recorded_action_never_confirmed():
    mode = CorrelationMode()
    assert mode.is_confirmed("rpm") is False  # never recorded at all -> never confirmed
