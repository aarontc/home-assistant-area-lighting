"""Diagnostic snapshot must expose remaining seconds for each active timer."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_snapshot_motion_timer_remaining_none_when_inactive(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    snap = ctrl.diagnostic_snapshot()
    assert "motion_timer_remaining_seconds" in snap
    assert snap["motion_timer_remaining_seconds"] is None
    assert "motion_night_timer_remaining_seconds" in snap
    assert snap["motion_night_timer_remaining_seconds"] is None
    assert "occupancy_timer_remaining_seconds" in snap
    assert snap["occupancy_timer_remaining_seconds"] is None


@pytest.mark.integration
async def test_snapshot_motion_timer_remaining_when_active(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)
    await ctrl.handle_motion_off()  # starts normal motion timer (8 min = 480s)

    snap = ctrl.diagnostic_snapshot()
    remaining = snap["motion_timer_remaining_seconds"]
    assert remaining is not None
    # Default motion off timer is 8 min = 480s; allow small slack
    assert 478 <= remaining <= 481, f"expected ~480s remaining, got {remaining}"
    # Not night mode → night timer should still be inactive
    assert snap["motion_night_timer_remaining_seconds"] is None


@pytest.mark.integration
async def test_snapshot_motion_night_timer_remaining_when_active(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.night_mode = True
    ctrl._state.transition_to_scene("night", ActivationSource.MOTION)
    await ctrl.handle_motion_off()  # starts night timer (5 min = 300s)

    snap = ctrl.diagnostic_snapshot()
    remaining = snap["motion_night_timer_remaining_seconds"]
    assert remaining is not None
    assert 298 <= remaining <= 301
    assert snap["motion_timer_remaining_seconds"] is None


@pytest.mark.integration
async def test_snapshot_occupancy_timer_remaining_when_active(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    await ctrl.handle_occupancy_off()  # starts timer (30 min = 1800s)

    snap = ctrl.diagnostic_snapshot()
    remaining = snap["occupancy_timer_remaining_seconds"]
    assert remaining is not None
    assert 1798 <= remaining <= 1801


@pytest.mark.integration
async def test_diagnostic_state_text_shows_remaining(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The human-readable state_text exposed by the sensor should render
    the remaining seconds alongside the active flag so users can see
    it at a glance in the UI."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)
    await ctrl.handle_motion_off()

    sensor_state = hass.states.get("sensor.area_lighting_diagnostics")
    assert sensor_state is not None
    text = sensor_state.attributes.get("state_text", "")
    assert "motion_timer_remaining_seconds" in text
    # Should show an integer-ish number of seconds (not just None)
    # The format is "  motion_timer_remaining_seconds: 480" (approx)
    import re

    match = re.search(r"motion_timer_remaining_seconds:\s*(\d+(?:\.\d+)?)", text)
    assert match, f"expected a numeric value in state_text, got: {text}"
    val = float(match.group(1))
    assert 478 <= val <= 481
