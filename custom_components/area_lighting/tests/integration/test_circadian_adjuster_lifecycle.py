"""The circadian adjuster runs exactly while the area is in circadian.

Leaving circadian by any path (scene cycling, favorite, external scene
activation, manual detection, off, the select entity) must stop it, or
the next tick would push circadian values over the new scene.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource

LIGHTS = ("light.network_room_overhead_1", "light.network_room_overhead_2")


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


async def _circadian_area(hass: HomeAssistant, cfg: dict, pin_circadian):
    """Set up network_room in circadian with its adjuster running."""
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, cfg)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl.circadian_running
    return ctrl


def _light_turn_ons(service_calls) -> list:
    return [c for c in service_calls if c.domain == "light" and c.service == "turn_on"]


async def _assert_tick_sends_nothing(hass, ctrl, service_calls, pin_circadian) -> None:
    assert not ctrl.circadian_running
    for entity_id in LIGHTS:
        hass.states.async_set(entity_id, "on", {"brightness": 100})
    ctrl._circadian_commanded_at.clear()
    await hass.async_block_till_done()
    service_calls.clear()
    await pin_circadian(50, 4000)
    assert _light_turn_ons(service_calls) == []


@pytest.mark.integration
async def test_lighting_on_from_circadian_stops_adjuster(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    ctrl = await _circadian_area(hass, network_room_config, pin_circadian)

    await ctrl.lighting_on()
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug in ("daylight", "evening")
    await _assert_tick_sends_nothing(hass, ctrl, service_calls, pin_circadian)


@pytest.mark.integration
async def test_lighting_favorite_from_circadian_stops_adjuster(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    ctrl = await _circadian_area(hass, network_room_config, pin_circadian)

    await ctrl.lighting_favorite()
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "night"
    await _assert_tick_sends_nothing(hass, ctrl, service_calls, pin_circadian)


@pytest.mark.integration
async def test_external_scene_activation_non_circadian_stops_adjuster(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    ctrl = await _circadian_area(hass, network_room_config, pin_circadian)

    await ctrl.handle_scene_activated("daylight")
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "daylight"
    await _assert_tick_sends_nothing(hass, ctrl, service_calls, pin_circadian)


@pytest.mark.integration
async def test_manual_light_change_from_circadian_stops_adjuster(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    ctrl = await _circadian_area(hass, network_room_config, pin_circadian)

    await ctrl.handle_manual_light_change()
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    await _assert_tick_sends_nothing(hass, ctrl, service_calls, pin_circadian)


@pytest.mark.integration
async def test_holiday_change_from_circadian_keeps_adjuster(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    """A holiday mode change leaves a circadian area alone, so the adjuster
    keeps running and the next tick still updates the lights."""
    ctrl = await _circadian_area(hass, network_room_config, pin_circadian)

    hass.states.async_set("input_select.holiday_mode", "christmas")
    await ctrl.handle_holiday_changed("christmas")
    await hass.async_block_till_done()

    assert ctrl._state.is_circadian
    assert ctrl.circadian_running
    for entity_id in LIGHTS:
        hass.states.async_set(entity_id, "on", {"brightness": 100})
    ctrl._circadian_commanded_at.clear()
    await hass.async_block_till_done()
    service_calls.clear()
    await pin_circadian(50, 4000)
    assert {c.data["entity_id"] for c in _light_turn_ons(service_calls)} == set(LIGHTS)


@pytest.mark.integration
async def test_off_from_circadian_stops_adjuster(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    ctrl = await _circadian_area(hass, network_room_config, pin_circadian)

    await ctrl.lighting_off()
    await hass.async_block_till_done()

    assert ctrl._state.is_off
    await _assert_tick_sends_nothing(hass, ctrl, service_calls, pin_circadian)


@pytest.mark.integration
async def test_activating_non_circadian_scene_stops_adjuster(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    ctrl = await _circadian_area(hass, network_room_config, pin_circadian)

    await ctrl._activate_scene("evening", ActivationSource.USER)
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "evening"
    await _assert_tick_sends_nothing(hass, ctrl, service_calls, pin_circadian)


@pytest.mark.integration
async def test_select_leaving_circadian_stops_adjuster(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    ctrl = await _circadian_area(hass, network_room_config, pin_circadian)

    ctrl.current_scene = "evening"
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "evening"
    await _assert_tick_sends_nothing(hass, ctrl, service_calls, pin_circadian)


@pytest.mark.integration
async def test_select_entering_circadian_starts_adjuster_without_commands(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    """The select entity's setter is state-only: lights follow at the next tick."""
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    for entity_id in LIGHTS:
        hass.states.async_set(entity_id, "on", {"brightness": 100})
    await hass.async_block_till_done()
    service_calls.clear()

    ctrl.current_scene = "circadian"
    await hass.async_block_till_done()

    assert ctrl.circadian_running
    assert list(service_calls) == []

    await pin_circadian(50, 4000)
    assert {c.data["entity_id"] for c in _light_turn_ons(service_calls)} == set(LIGHTS)


@pytest.mark.integration
async def test_external_circadian_scene_starts_adjuster(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert not ctrl.circadian_running

    await ctrl.handle_scene_activated("circadian")
    await hass.async_block_till_done()

    assert ctrl.circadian_running


@pytest.mark.integration
async def test_circadian_never_calls_switch_services(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    ctrl = await _circadian_area(hass, network_room_config, pin_circadian)
    for entity_id in LIGHTS:
        hass.states.async_set(entity_id, "on", {"brightness": 100})
    ctrl._circadian_commanded_at.clear()
    await pin_circadian(-20, 2500)
    await ctrl.lighting_raise()
    await ctrl.lighting_circadian()
    await ctrl.lighting_off()
    await hass.async_block_till_done()

    assert _light_turn_ons(service_calls)
    assert [c for c in service_calls if c.domain == "switch"] == []
