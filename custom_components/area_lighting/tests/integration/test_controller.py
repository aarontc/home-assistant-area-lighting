"""Integration-layer controller tests.

Migrated from the deleted tests/test_controller.py, now running against a
real HA instance via pytest-homeassistant-custom-component.
"""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource, LightingState


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


# ── lighting_on / scene cycling ──────────────────────────────────────────


@pytest.mark.integration
async def test_initial_state_is_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl._state.state == LightingState.OFF


@pytest.mark.integration
async def test_lighting_on_from_off_activates_circadian(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.lighting_on()
    assert ctrl._state.is_circadian


@pytest.mark.integration
async def test_lighting_on_cycles_daylight_to_evening(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    await ctrl.lighting_on()
    assert ctrl._state.scene_slug == "evening"


@pytest.mark.integration
async def test_lighting_on_cycles_evening_to_daylight(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    await ctrl.lighting_on()
    assert ctrl._state.scene_slug == "daylight"


@pytest.mark.integration
async def test_lighting_on_from_night_returns_to_circadian(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("night", ActivationSource.USER)
    await ctrl.lighting_on()
    assert ctrl._state.is_circadian


@pytest.mark.integration
async def test_lighting_on_from_holiday_cycles_out(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("christmas", ActivationSource.USER)
    await ctrl.lighting_on()
    assert ctrl._state.is_circadian


# ── Holiday mode ─────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_lighting_on_from_off_with_holiday_uses_christmas(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    hass.states.async_set("input_select.holiday_mode", "christmas")
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.lighting_on()
    assert ctrl._state.scene_slug == "christmas"


# ── lighting_favorite ────────────────────────────────────────────────────


@pytest.mark.integration
async def test_favorite_no_holiday_picks_night(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.lighting_favorite()
    assert ctrl._state.scene_slug == "night"


@pytest.mark.integration
async def test_favorite_with_holiday_picks_christmas(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    hass.states.async_set("input_select.holiday_mode", "christmas")
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.lighting_favorite()
    assert ctrl._state.scene_slug == "christmas"


@pytest.mark.integration
async def test_favorite_already_in_christmas_picks_night(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    hass.states.async_set("input_select.holiday_mode", "christmas")
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("christmas", ActivationSource.USER)
    await ctrl.lighting_favorite()
    assert ctrl._state.scene_slug == "night"


# ── lighting_off ─────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_off_with_no_ambience_turns_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    await ctrl.lighting_off()
    assert ctrl._state.is_off


@pytest.mark.integration
async def test_off_with_ambience_falls_back_to_ambient(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    await ctrl.lighting_off()
    assert ctrl._state.scene_slug == "ambient"
    assert ctrl._state.source == ActivationSource.AMBIENCE


# ── Motion handling ─────────────────────────────────────────────────────


@pytest.mark.integration
async def test_motion_on_from_off_activates(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.handle_motion_on()
    assert ctrl._state.is_circadian
    assert ctrl._state.source == ActivationSource.MOTION


@pytest.mark.integration
async def test_motion_on_when_lights_on_is_noop(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    await ctrl.handle_motion_on()
    assert ctrl._state.scene_slug == "daylight"


@pytest.mark.integration
async def test_motion_off_starts_timer(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    await ctrl.handle_motion_off()
    assert ctrl._motion_timer.is_active


# ── Manual detection ────────────────────────────────────────────────────


@pytest.mark.integration
async def test_manual_change_when_not_dimmed_marks_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    await ctrl.handle_manual_light_change()
    assert ctrl._state.is_manual


# ── External scene activation ───────────────────────────────────────────


@pytest.mark.integration
async def test_handle_scene_activated_updates_state(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.handle_scene_activated("evening")
    assert ctrl._state.scene_slug == "evening"
