"""Unit tests for startup state reconciliation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import State

from custom_components.area_lighting.area_state import ActivationSource, AreaState, LightingState
from custom_components.area_lighting.controller import AreaLightingController


def _make_light(entity_id: str) -> MagicMock:
    light = MagicMock()
    light.id = entity_id
    return light


def _make_controller(
    light_ids: list[str],
    state: AreaState | None = None,
    persisted: bool = True,
) -> MagicMock:
    ctrl = MagicMock(spec=AreaLightingController)
    ctrl.area = MagicMock()
    ctrl.area.id = "test_area"
    ctrl.area.lights = [_make_light(eid) for eid in light_ids]
    ctrl._state = state or AreaState()
    ctrl._state_was_persisted = persisted
    ctrl._notify_state_change = MagicMock()
    ctrl.hass = MagicMock()
    ctrl.reconcile_startup_state = AreaLightingController.reconcile_startup_state.__get__(ctrl)
    return ctrl


@pytest.mark.unit
def test_reconcile_off_with_lights_on_transitions_to_manual() -> None:
    ctrl = _make_controller(["light.a", "light.b"])
    ctrl.hass.states.get = lambda eid: State(eid, "on")

    ctrl.reconcile_startup_state()

    assert ctrl._state.state == LightingState.MANUAL
    assert ctrl._state.scene_slug == "manual"
    assert ctrl._state.source == ActivationSource.MANUAL
    ctrl._notify_state_change.assert_called_once()


@pytest.mark.unit
def test_reconcile_off_with_all_lights_off_stays_off() -> None:
    ctrl = _make_controller(["light.a", "light.b"])
    ctrl.hass.states.get = lambda eid: State(eid, "off")

    ctrl.reconcile_startup_state()

    assert ctrl._state.is_off
    ctrl._notify_state_change.assert_not_called()


@pytest.mark.unit
def test_reconcile_off_with_mixed_lights_transitions_to_manual() -> None:
    states = {
        "light.a": State("light.a", "off"),
        "light.b": State("light.b", "on"),
    }
    ctrl = _make_controller(["light.a", "light.b"])
    ctrl.hass.states.get = lambda eid: states.get(eid)

    ctrl.reconcile_startup_state()

    assert ctrl._state.state == LightingState.MANUAL
    ctrl._notify_state_change.assert_called_once()


@pytest.mark.unit
def test_reconcile_skips_when_already_on() -> None:
    state = AreaState()
    state.transition_to_circadian(ActivationSource.USER)
    ctrl = _make_controller(["light.a"], state=state)
    ctrl.hass.states.get = lambda eid: State(eid, "on")

    ctrl.reconcile_startup_state()

    assert ctrl._state.is_circadian
    ctrl._notify_state_change.assert_not_called()


@pytest.mark.unit
def test_reconcile_off_with_unavailable_lights_stays_off() -> None:
    ctrl = _make_controller(["light.a"])
    ctrl.hass.states.get = lambda eid: State(eid, "unavailable")

    ctrl.reconcile_startup_state()

    assert ctrl._state.is_off
    ctrl._notify_state_change.assert_not_called()


@pytest.mark.unit
def test_reconcile_off_with_unknown_light_stays_off() -> None:
    ctrl = _make_controller(["light.a"])
    ctrl.hass.states.get = lambda eid: None

    ctrl.reconcile_startup_state()

    assert ctrl._state.is_off
    ctrl._notify_state_change.assert_not_called()


@pytest.mark.unit
def test_reconcile_skips_when_state_not_persisted() -> None:
    ctrl = _make_controller(["light.a"], persisted=False)
    ctrl.hass.states.get = lambda eid: State(eid, "on")

    ctrl.reconcile_startup_state()

    assert ctrl._state.is_off
    ctrl._notify_state_change.assert_not_called()
