"""External lights-off cancels motion + occupancy timers (README §4)."""

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
async def test_lights_all_off_cancels_motion_timer(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)
    ctrl._motion_timer.start()
    assert ctrl._motion_timer.is_active
    await ctrl.handle_lights_all_off()
    assert not ctrl._motion_timer.is_active


@pytest.mark.integration
async def test_lights_all_off_cancels_motion_night_timer(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("night", ActivationSource.MOTION)
    ctrl._motion_night_timer.start()
    assert ctrl._motion_night_timer.is_active
    await ctrl.handle_lights_all_off()
    assert not ctrl._motion_night_timer.is_active


@pytest.mark.integration
async def test_lights_all_off_cancels_occupancy_timer(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    ctrl._occupancy_timer.start()
    assert ctrl._occupancy_timer.is_active
    await ctrl.handle_lights_all_off()
    assert not ctrl._occupancy_timer.is_active
