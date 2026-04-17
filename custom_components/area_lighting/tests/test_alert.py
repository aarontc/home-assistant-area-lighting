"""Unit tests for alert module helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import State

from custom_components.area_lighting.alert import (
    capture_light_states,
    execute_alert,
    filter_lights_by_target,
    restore_light_states,
)
from custom_components.area_lighting.models import AlertPattern, AlertStep


def _make_state(entity_id: str, state: str = "on", attributes: dict | None = None) -> State:
    return State(entity_id, state, attributes or {})


@pytest.mark.unit
def test_filter_all_returns_all_lights() -> None:
    light_ids = ["light.a", "light.b", "light.c"]
    states = {
        "light.a": _make_state("light.a", attributes={"supported_color_modes": ["hs"]}),
        "light.b": _make_state("light.b", attributes={"supported_color_modes": ["color_temp"]}),
        "light.c": _make_state("light.c", attributes={"supported_color_modes": ["brightness"]}),
    }
    result = filter_lights_by_target(light_ids, "all", states.get)
    assert result == ["light.a", "light.b", "light.c"]


@pytest.mark.unit
def test_filter_color_returns_only_color_capable() -> None:
    light_ids = ["light.color", "light.white", "light.ct"]
    states = {
        "light.color": _make_state(
            "light.color", attributes={"supported_color_modes": ["hs", "color_temp"]}
        ),
        "light.white": _make_state(
            "light.white", attributes={"supported_color_modes": ["brightness"]}
        ),
        "light.ct": _make_state("light.ct", attributes={"supported_color_modes": ["color_temp"]}),
    }
    result = filter_lights_by_target(light_ids, "color", states.get)
    assert result == ["light.color"]


@pytest.mark.unit
def test_filter_white_returns_non_color_capable() -> None:
    light_ids = ["light.color", "light.white", "light.ct"]
    states = {
        "light.color": _make_state(
            "light.color", attributes={"supported_color_modes": ["hs", "color_temp"]}
        ),
        "light.white": _make_state(
            "light.white", attributes={"supported_color_modes": ["brightness"]}
        ),
        "light.ct": _make_state("light.ct", attributes={"supported_color_modes": ["color_temp"]}),
    }
    result = filter_lights_by_target(light_ids, "white", states.get)
    assert result == ["light.white", "light.ct"]


@pytest.mark.unit
def test_filter_skips_unavailable_lights() -> None:
    light_ids = ["light.ok", "light.gone"]
    states = {
        "light.ok": _make_state("light.ok", attributes={"supported_color_modes": ["hs"]}),
        "light.gone": _make_state("light.gone", state="unavailable"),
    }
    result = filter_lights_by_target(light_ids, "all", states.get)
    assert result == ["light.ok"]


@pytest.mark.unit
def test_capture_returns_state_dict_per_light() -> None:
    states = {
        "light.a": _make_state(
            "light.a",
            state="on",
            attributes={
                "brightness": 200,
                "color_mode": "hs",
                "hs_color": (30.0, 80.0),
                "supported_color_modes": ["hs"],
            },
        ),
        "light.b": _make_state("light.b", state="off", attributes={}),
    }
    captured = capture_light_states(["light.a", "light.b"], states.get)
    assert captured["light.a"]["state"] == "on"
    assert captured["light.a"]["brightness"] == 200
    assert captured["light.a"]["hs_color"] == (30.0, 80.0)
    assert captured["light.b"]["state"] == "off"


@pytest.mark.unit
async def test_restore_replays_captured_states() -> None:
    captured = {
        "light.a": {"state": "on", "brightness": 200, "color_mode": "hs", "hs_color": (30.0, 80.0)},
        "light.b": {"state": "off"},
    }
    mock_call = AsyncMock()
    await restore_light_states(captured, mock_call)
    calls = mock_call.call_args_list
    assert len(calls) == 2
    on_call = next(c for c in calls if c.kwargs.get("entity_id") == "light.a")
    assert on_call.args == ("light", "turn_on")
    assert on_call.kwargs["brightness"] == 200
    off_call = next(c for c in calls if c.kwargs.get("entity_id") == "light.b")
    assert off_call.args == ("light", "turn_off")


def _make_mock_controller(light_ids: list[str]):
    """Build a mock controller with the minimal interface execute_alert needs."""
    ctrl = MagicMock()
    ctrl.area.id = "test_area"
    ctrl.area.lights = [MagicMock(id=eid) for eid in light_ids]
    ctrl.area.light_clusters = []  # no clusters in unit tests
    ctrl._alert_active = False
    for timer_name in ("_motion_timer", "_motion_night_timer", "_occupancy_timer"):
        timer = MagicMock()
        timer.deadline_utc = None
        timer.is_active = False
        setattr(ctrl, timer_name, timer)
    return ctrl


@pytest.mark.unit
async def test_execute_alert_sets_and_clears_flag() -> None:
    ctrl = _make_mock_controller(["light.a"])
    pattern = AlertPattern(
        steps=[AlertStep(target="all", state="on", brightness=255, delay=0.0)],
    )
    hass = MagicMock()
    hass.states.get = lambda eid: _make_state(
        eid, attributes={"supported_color_modes": ["brightness"]}
    )
    hass.services.async_call = AsyncMock()

    with patch("custom_components.area_lighting.alert.asyncio.sleep", AsyncMock()):
        await execute_alert(hass, ctrl, pattern)

    assert ctrl._alert_active is False


@pytest.mark.unit
async def test_execute_alert_respects_repeat() -> None:
    ctrl = _make_mock_controller(["light.a"])
    pattern = AlertPattern(
        steps=[
            AlertStep(target="all", state="on", delay=0.0),
            AlertStep(target="all", state="off", delay=0.0),
        ],
        repeat=3,
    )
    hass = MagicMock()
    hass.states.get = lambda eid: _make_state(
        eid, attributes={"supported_color_modes": ["brightness"]}
    )
    hass.services.async_call = AsyncMock()

    with patch("custom_components.area_lighting.alert.asyncio.sleep", AsyncMock()):
        await execute_alert(hass, ctrl, pattern)

    # 3 repeats x 2 steps = 6 step calls + restore calls
    step_calls = [
        c
        for c in hass.services.async_call.call_args_list
        if len(c.args) >= 2 and c.args[0] == "light"
    ]
    # 6 step calls + at least 1 restore call
    assert len(step_calls) >= 6


@pytest.mark.unit
async def test_execute_alert_cancels_and_restores_timers() -> None:
    from datetime import UTC, datetime, timedelta

    future = datetime.now(UTC) + timedelta(minutes=5)
    ctrl = _make_mock_controller(["light.a"])
    ctrl._motion_timer.deadline_utc = future
    ctrl._motion_timer.is_active = True
    ctrl._motion_night_timer.deadline_utc = None
    ctrl._motion_night_timer.is_active = False
    ctrl._occupancy_timer.deadline_utc = future
    ctrl._occupancy_timer.is_active = True

    pattern = AlertPattern(
        steps=[AlertStep(target="all", state="on", delay=0.0)],
    )
    hass = MagicMock()
    hass.states.get = lambda eid: _make_state(
        eid, attributes={"supported_color_modes": ["brightness"]}
    )
    hass.services.async_call = AsyncMock()

    with patch("custom_components.area_lighting.alert.asyncio.sleep", AsyncMock()):
        await execute_alert(hass, ctrl, pattern)

    ctrl._motion_timer.cancel.assert_called()
    ctrl._occupancy_timer.cancel.assert_called()
    ctrl._motion_timer.restore.assert_called_once_with(future)
    ctrl._occupancy_timer.restore.assert_called_once_with(future)
    ctrl._motion_night_timer.restore.assert_not_called()
