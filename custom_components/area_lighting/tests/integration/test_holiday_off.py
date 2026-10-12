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


def _without_ambient_scene(cfg: dict) -> dict:
    area = cfg["area_lighting"]["areas"][0]
    area["scenes"] = [s for s in area["scenes"] if s["id"] != "ambient"]
    return cfg


@pytest.mark.integration
async def test_off_with_ambience_and_no_ambient_scene_turns_fully_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, _without_ambient_scene(network_room_config))
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    await ctrl.lighting_off()
    assert ctrl._state.is_off


@pytest.mark.integration
async def test_off_with_holiday_ambience_and_no_ambient_scene_activates_holiday(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, _without_ambient_scene(network_room_config))
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_select.holiday_mode", "christmas")
    hass.states.async_set("input_select.ambient_scene", "holiday")
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    await ctrl.lighting_off()
    assert ctrl._state.scene_slug == "christmas"
    assert ctrl._state.source == ActivationSource.AMBIENCE


@pytest.mark.integration
async def test_ambient_enabled_with_no_ambient_scene_stays_off(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    await _setup(hass, _without_ambient_scene(network_room_config))
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl._state.is_off
    service_calls.clear()
    await ctrl.handle_ambient_enabled()
    await hass.async_block_till_done()
    assert ctrl._state.is_off
    assert not [c for c in service_calls if c.service == "turn_on"]


@pytest.mark.integration
async def test_motion_fade_with_ambience_and_no_ambient_scene_turns_fully_off(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    await _setup(hass, _without_ambient_scene(network_room_config))
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    service_calls.clear()
    await ctrl.lighting_off_fade(source=ActivationSource.MOTION)
    await hass.async_block_till_done()
    assert ctrl._state.is_off
    assert ctrl._state.source == ActivationSource.MOTION
    assert not [c for c in service_calls if c.service == "turn_on"]
    assert [c for c in service_calls if c.service == "turn_off"]
