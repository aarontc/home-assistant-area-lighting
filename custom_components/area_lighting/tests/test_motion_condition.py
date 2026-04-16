"""Unit tests for the pure motion condition evaluator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from custom_components.area_lighting.models import MotionLightCondition
from custom_components.area_lighting.motion_condition import evaluate_motion_condition


@dataclass
class FakeState:
    state: str
    attributes: dict = field(default_factory=dict)


def make_resolver(states: dict[str, FakeState]) -> Callable[[str], FakeState | None]:
    return lambda eid: states.get(eid)


# ── Single-entity form (regression) ─────────────────────────────────────


def test_single_entity_state_match_passes():
    cond = MotionLightCondition(entity_id="lock.front", state="unlocked")
    resolver = make_resolver({"lock.front": FakeState("unlocked")})
    assert evaluate_motion_condition(cond, resolver) is True


def test_single_entity_state_mismatch_fails():
    cond = MotionLightCondition(entity_id="lock.front", state="unlocked")
    resolver = make_resolver({"lock.front": FakeState("locked")})
    assert evaluate_motion_condition(cond, resolver) is False


def test_single_entity_missing_fails():
    cond = MotionLightCondition(entity_id="lock.front", state="unlocked")
    resolver = make_resolver({})
    assert evaluate_motion_condition(cond, resolver) is False


def test_single_entity_attribute_below_passes():
    cond = MotionLightCondition(
        entity_id="sun.sun",
        attribute="elevation",
        below=0,
    )
    resolver = make_resolver(
        {
            "sun.sun": FakeState("below_horizon", {"elevation": -5.0}),
        }
    )
    assert evaluate_motion_condition(cond, resolver) is True


def test_single_entity_attribute_below_fails_when_above_threshold():
    cond = MotionLightCondition(
        entity_id="sun.sun",
        attribute="elevation",
        below=0,
    )
    resolver = make_resolver(
        {
            "sun.sun": FakeState("above_horizon", {"elevation": 30.0}),
        }
    )
    assert evaluate_motion_condition(cond, resolver) is False


def test_single_entity_attribute_missing_fails():
    cond = MotionLightCondition(
        entity_id="sun.sun",
        attribute="elevation",
        below=0,
    )
    resolver = make_resolver({"sun.sun": FakeState("above_horizon", {})})
    assert evaluate_motion_condition(cond, resolver) is False


def test_state_numeric_above_no_attribute_passes():
    """Bug fix: above/below on no-attribute condition reads state.state."""
    cond = MotionLightCondition(entity_id="sensor.lux", above=100.0)
    resolver = make_resolver({"sensor.lux": FakeState("250")})
    assert evaluate_motion_condition(cond, resolver) is True


def test_state_numeric_above_no_attribute_fails():
    cond = MotionLightCondition(entity_id="sensor.lux", above=100.0)
    resolver = make_resolver({"sensor.lux": FakeState("50")})
    assert evaluate_motion_condition(cond, resolver) is False


# ── Aggregated form: average ────────────────────────────────────────────


def test_average_below_threshold_passes():
    cond = MotionLightCondition(
        entity_ids=["sensor.a", "sensor.b"],
        aggregate="average",
        below=100,
    )
    resolver = make_resolver(
        {
            "sensor.a": FakeState("40"),
            "sensor.b": FakeState("60"),
        }
    )
    # average = 50, below 100 -> passes
    assert evaluate_motion_condition(cond, resolver) is True


def test_average_at_or_above_threshold_fails():
    cond = MotionLightCondition(
        entity_ids=["sensor.a", "sensor.b"],
        aggregate="average",
        below=100,
    )
    resolver = make_resolver(
        {
            "sensor.a": FakeState("80"),
            "sensor.b": FakeState("120"),
        }
    )
    # average = 100, not strictly below 100 -> fails
    assert evaluate_motion_condition(cond, resolver) is False


def test_average_with_attribute_path():
    cond = MotionLightCondition(
        entity_ids=["sun.sun_east", "sun.sun_west"],
        aggregate="average",
        attribute="elevation",
        below=0,
    )
    resolver = make_resolver(
        {
            "sun.sun_east": FakeState("below_horizon", {"elevation": -2.0}),
            "sun.sun_west": FakeState("below_horizon", {"elevation": -8.0}),
        }
    )
    # average elevation = -5, below 0 -> passes
    assert evaluate_motion_condition(cond, resolver) is True


# ── Aggregated form: min / max ──────────────────────────────────────────


def test_min_aggregate_below_uses_lowest_value():
    cond = MotionLightCondition(
        entity_ids=["sensor.a", "sensor.b", "sensor.c"],
        aggregate="min",
        below=50,
    )
    resolver = make_resolver(
        {
            "sensor.a": FakeState("30"),
            "sensor.b": FakeState("100"),
            "sensor.c": FakeState("200"),
        }
    )
    # min = 30, below 50 -> passes
    assert evaluate_motion_condition(cond, resolver) is True


def test_min_aggregate_above_threshold_fails():
    cond = MotionLightCondition(
        entity_ids=["sensor.a", "sensor.b"],
        aggregate="min",
        below=50,
    )
    resolver = make_resolver(
        {
            "sensor.a": FakeState("60"),
            "sensor.b": FakeState("80"),
        }
    )
    # min = 60, not below 50 -> fails
    assert evaluate_motion_condition(cond, resolver) is False


def test_max_aggregate_uses_highest_value():
    cond = MotionLightCondition(
        entity_ids=["sensor.a", "sensor.b"],
        aggregate="max",
        above=100,
    )
    resolver = make_resolver(
        {
            "sensor.a": FakeState("50"),
            "sensor.b": FakeState("150"),
        }
    )
    # max = 150, above 100 -> passes
    assert evaluate_motion_condition(cond, resolver) is True


def test_max_aggregate_below_threshold_fails():
    cond = MotionLightCondition(
        entity_ids=["sensor.a", "sensor.b"],
        aggregate="max",
        above=100,
    )
    resolver = make_resolver(
        {
            "sensor.a": FakeState("50"),
            "sensor.b": FakeState("90"),
        }
    )
    # max = 90, not above 100 -> fails
    assert evaluate_motion_condition(cond, resolver) is False


# ── Aggregated form: availability filtering ─────────────────────────────


def test_aggregate_skips_unavailable_sensor():
    cond = MotionLightCondition(
        entity_ids=["sensor.a", "sensor.b"],
        aggregate="average",
        below=100,
    )
    resolver = make_resolver(
        {
            "sensor.a": FakeState("40"),
            "sensor.b": FakeState("unavailable"),
        }
    )
    # Only sensor.a contributes; average over [40] = 40, below 100 -> passes
    assert evaluate_motion_condition(cond, resolver) is True


def test_aggregate_skips_unknown_sensor():
    cond = MotionLightCondition(
        entity_ids=["sensor.a", "sensor.b"],
        aggregate="average",
        below=100,
    )
    resolver = make_resolver(
        {
            "sensor.a": FakeState("200"),
            "sensor.b": FakeState("unknown"),
        }
    )
    # Only sensor.a contributes; average = 200, not below 100 -> fails
    assert evaluate_motion_condition(cond, resolver) is False


def test_aggregate_skips_missing_sensor():
    cond = MotionLightCondition(
        entity_ids=["sensor.a", "sensor.b"],
        aggregate="average",
        below=100,
    )
    resolver = make_resolver({"sensor.a": FakeState("50")})  # sensor.b absent
    # Only sensor.a contributes; average = 50, below 100 -> passes
    assert evaluate_motion_condition(cond, resolver) is True


def test_aggregate_skips_non_numeric_state():
    cond = MotionLightCondition(
        entity_ids=["sensor.a", "sensor.b"],
        aggregate="average",
        below=100,
    )
    resolver = make_resolver(
        {
            "sensor.a": FakeState("50"),
            "sensor.b": FakeState("garbage"),
        }
    )
    # Only sensor.a contributes; average = 50, below 100 -> passes
    assert evaluate_motion_condition(cond, resolver) is True


def test_aggregate_all_unavailable_fails():
    cond = MotionLightCondition(
        entity_ids=["sensor.a", "sensor.b"],
        aggregate="average",
        below=100,
    )
    resolver = make_resolver(
        {
            "sensor.a": FakeState("unavailable"),
            "sensor.b": FakeState("unknown"),
        }
    )
    # No contributing values -> condition fails
    assert evaluate_motion_condition(cond, resolver) is False


def test_aggregate_all_missing_fails():
    cond = MotionLightCondition(
        entity_ids=["sensor.a", "sensor.b"],
        aggregate="average",
        below=100,
    )
    resolver = make_resolver({})
    assert evaluate_motion_condition(cond, resolver) is False
