"""Holiday off ownership behavior tests (D7)."""

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
async def test_off_in_user_christmas_with_ambience_falls_back_to_ambient(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    ctrl._state.transition_to_scene("christmas", ActivationSource.REMOTE)
    await ctrl.lighting_off()
    assert ctrl._state.scene_slug == "ambient"
    assert ctrl._state.source == ActivationSource.AMBIENCE


@pytest.mark.integration
async def test_off_in_ambience_christmas_turns_fully_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    ctrl._state.transition_to_scene("christmas", ActivationSource.AMBIENCE)
    await ctrl.lighting_off()
    assert ctrl._state.is_off


@pytest.mark.integration
async def test_off_in_ambience_ambient_turns_fully_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    await ctrl.lighting_off()
    assert ctrl._state.is_off


@pytest.mark.integration
async def test_off_in_holiday_without_ambience_turns_fully_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("christmas", ActivationSource.REMOTE)
    await ctrl.lighting_off()
    assert ctrl._state.is_off
