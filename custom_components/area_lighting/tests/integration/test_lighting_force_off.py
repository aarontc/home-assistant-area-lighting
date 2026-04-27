"""Tests for lighting_force_off — unconditional off bypassing ambient fallback."""

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
async def test_force_off_bypasses_ambient_fallback(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """With ambience active and a user-owned scene running, lighting_off would
    fall back to the ambient scene. lighting_force_off must go fully off
    instead."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)

    await ctrl.lighting_force_off()

    assert ctrl._state.is_off
    assert ctrl._state.scene_slug != "ambient"


@pytest.mark.integration
async def test_force_off_works_when_already_in_ambient(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """If the area is already in an ambience-owned ambient scene,
    lighting_force_off still results in a true off (off_internal)."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)

    await ctrl.lighting_force_off()

    assert ctrl._state.is_off
