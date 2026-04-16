"""Pure condition evaluator for motion_light_conditions.

Factored out of event_handlers._check_conditions so the logic can be
unit-tested without spinning up Home Assistant. The helper takes a
resolver callable rather than the hass object directly, keeping it
free of HA-core dependencies.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .models import MotionLightCondition


class _StateLike(Protocol):
    """The subset of homeassistant.core.State used by this module."""

    state: str
    attributes: dict


StateResolver = Callable[[str], _StateLike | None]


_UNAVAILABLE_STATES = {"unavailable", "unknown"}


def evaluate_motion_condition(
    cond: MotionLightCondition,
    get_state: StateResolver,
) -> bool:
    """Return True iff the condition is currently satisfied."""
    if cond.entity_ids is not None:
        return _evaluate_aggregated(cond, get_state)
    return _evaluate_single(cond, get_state)


def _evaluate_single(cond: MotionLightCondition, get_state: StateResolver) -> bool:
    state = get_state(cond.entity_id or "")
    if state is None or state.state in _UNAVAILABLE_STATES:
        return False
    if cond.state is not None:
        return state.state == cond.state
    value = _resolve_numeric_value(state, cond.attribute)
    if value is None:
        return False
    return _threshold_check(value, cond.above, cond.below)


def _evaluate_aggregated(cond: MotionLightCondition, get_state: StateResolver) -> bool:
    values: list[float] = []
    for eid in cond.entity_ids or []:
        state = get_state(eid)
        if state is None or state.state in _UNAVAILABLE_STATES:
            continue
        value = _resolve_numeric_value(state, cond.attribute)
        if value is None:
            continue
        values.append(value)

    if not values:
        return False  # all sensors unavailable/unparseable

    aggregate = _apply_aggregate(values, cond.aggregate)
    return _threshold_check(aggregate, cond.above, cond.below)


def _apply_aggregate(values: list[float], mode: str | None) -> float:
    if mode == "average":
        return sum(values) / len(values)
    if mode == "min":
        return min(values)
    if mode == "max":
        return max(values)
    raise ValueError(f"unknown aggregate mode: {mode!r}")


def _resolve_numeric_value(state: _StateLike, attribute: str | None) -> float | None:
    """Return the numeric value for threshold comparison, or None if unresolvable."""
    if attribute is not None:
        raw = state.attributes.get(attribute)
        if raw is None:
            return None
    else:
        raw = state.state
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _threshold_check(
    value: float,
    above: float | None,
    below: float | None,
) -> bool:
    """Apply above/below comparisons. Both None = vacuously true."""
    if above is not None and value <= above:
        return False
    return not (below is not None and value >= below)
