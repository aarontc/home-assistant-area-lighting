"""An area treats a holiday it defines no scene for as no holiday.

The network_room fixture defines `christmas` but not `halloween`.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


async def _setup(hass: HomeAssistant, cfg: dict):
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    return hass.data["area_lighting"]["controllers"]["network_room"]


async def _set_holiday(hass: HomeAssistant, mode: str) -> None:
    hass.states.async_set("input_select.holiday_mode", mode)
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_undefined_holiday_leaves_off_area_off(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    ctrl = await _setup(hass, network_room_config)
    service_calls.clear()
    await _set_holiday(hass, "halloween")
    assert ctrl._state.is_off
    assert not [c for c in service_calls if c.service == "turn_on"]


@pytest.mark.integration
async def test_defined_holiday_still_lights_off_area(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config)
    await _set_holiday(hass, "christmas")
    assert ctrl._state.scene_slug == "christmas"
    assert ctrl._state.source == ActivationSource.HOLIDAY


@pytest.mark.integration
async def test_switch_to_undefined_holiday_turns_holiday_scene_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config)
    await _set_holiday(hass, "christmas")
    await _set_holiday(hass, "halloween")
    assert ctrl._state.is_off


@pytest.mark.integration
async def test_ambient_on_with_undefined_holiday_uses_ambient(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config)
    hass.states.async_set("input_select.ambient_scene", "holiday")
    await _set_holiday(hass, "halloween")
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    await ctrl.handle_ambient_enabled()
    assert ctrl._state.scene_slug == "ambient"


@pytest.mark.integration
async def test_off_fallback_with_undefined_holiday_uses_ambient(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config)
    hass.states.async_set("input_select.ambient_scene", "holiday")
    await _set_holiday(hass, "halloween")
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    ctrl._state.transition_to_scene("evening", ActivationSource.REMOTE)
    await ctrl.lighting_off()
    assert ctrl._state.scene_slug == "ambient"
    assert ctrl._state.source == ActivationSource.AMBIENCE


@pytest.mark.integration
async def test_favorite_with_undefined_holiday_goes_to_night(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config)
    ctrl._state.transition_to_scene("evening", ActivationSource.REMOTE)
    await _set_holiday(hass, "halloween")
    await ctrl.lighting_favorite()
    assert ctrl._state.scene_slug == "night"
