"""Unit tests for alert module helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import State

from custom_components.area_lighting.alert import (
    capture_light_states,
    filter_lights_by_target,
    restore_light_states,
)


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
        "light.color": _make_state("light.color", attributes={"supported_color_modes": ["hs", "color_temp"]}),
        "light.white": _make_state("light.white", attributes={"supported_color_modes": ["brightness"]}),
        "light.ct": _make_state("light.ct", attributes={"supported_color_modes": ["color_temp"]}),
    }
    result = filter_lights_by_target(light_ids, "color", states.get)
    assert result == ["light.color"]


@pytest.mark.unit
def test_filter_white_returns_non_color_capable() -> None:
    light_ids = ["light.color", "light.white", "light.ct"]
    states = {
        "light.color": _make_state("light.color", attributes={"supported_color_modes": ["hs", "color_temp"]}),
        "light.white": _make_state("light.white", attributes={"supported_color_modes": ["brightness"]}),
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
        "light.a": _make_state("light.a", state="on", attributes={
            "brightness": 200, "color_mode": "hs", "hs_color": (30.0, 80.0),
            "supported_color_modes": ["hs"],
        }),
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
    on_call = [c for c in calls if c.kwargs.get("entity_id") == "light.a"][0]
    assert on_call.args == ("light", "turn_on")
    assert on_call.kwargs["brightness"] == 200
    off_call = [c for c in calls if c.kwargs.get("entity_id") == "light.b"][0]
    assert off_call.args == ("light", "turn_off")
