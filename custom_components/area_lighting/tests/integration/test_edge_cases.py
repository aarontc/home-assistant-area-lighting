"""Integration-layer edge cases migrated from the deleted tests/test_edge_cases.py."""

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


# ── Holiday mode edge cases ─────────────────────────────────────────────


@pytest.mark.integration
async def test_holiday_change_christmas_to_halloween(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Switching holiday mode while showing holiday-sourced holiday scene."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_select.holiday_mode", "christmas")
    await ctrl.handle_holiday_changed("christmas")
    assert ctrl._state.scene_slug == "christmas"
    hass.states.async_set("input_select.holiday_mode", "halloween")
    await ctrl.handle_holiday_changed("halloween")
    assert ctrl._state.scene_slug == "halloween"


@pytest.mark.integration
async def test_holiday_disabled_when_remote_set_holiday_leaves_alone(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """User-owned (REMOTE) holiday scene should be untouched by holiday mode going off."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("christmas", ActivationSource.REMOTE)
    await ctrl.handle_holiday_changed("none")
    assert ctrl._state.scene_slug == "christmas"


# ── Ambience source flag persistence ────────────────────────────────────


@pytest.mark.integration
async def test_user_overrides_ambient_then_disables_ambience_does_not_turn_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    await ctrl.handle_ambient_enabled()
    assert ctrl._state.was_ambient_activated
    await ctrl.lighting_favorite()
    assert ctrl._state.scene_slug == "night"
    assert ctrl._state.source == ActivationSource.USER
    await ctrl.async_set_ambience_enabled(False)
    assert ctrl._state.scene_slug == "night"


# ── Motion + ambience interaction ───────────────────────────────────────


@pytest.mark.integration
async def test_motion_in_ambient_with_override_uses_circadian_not_holiday(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    ctrl.motion_override_ambient = True
    # Flush the state_changed event from async_set so the ambient zone
    # handler's async_create_task completes before we proceed.
    await hass.async_block_till_done()
    await ctrl.handle_ambient_enabled()
    assert ctrl._state.scene_slug == "ambient"
    await ctrl.handle_motion_on()
    assert ctrl._state.is_circadian
    assert ctrl._state.source == ActivationSource.MOTION
    await ctrl.async_set_ambience_enabled(False)
    assert ctrl._state.is_circadian


@pytest.mark.integration
async def test_motion_timer_after_motion_in_ambient_returns_to_ambient(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    # Flush the state_changed event from async_set so the ambient zone
    # handler's async_create_task completes before we proceed.
    await hass.async_block_till_done()
    ctrl.ambience_enabled = True
    ctrl.motion_override_ambient = True
    await ctrl.handle_ambient_enabled()
    await ctrl.handle_motion_on()
    assert ctrl._state.is_circadian
    await ctrl._on_motion_timer()
    assert ctrl._state.scene_slug == "ambient"
    assert ctrl._state.source == ActivationSource.AMBIENCE


# ── Dimmed flow edge cases ──────────────────────────────────────────────


@pytest.mark.integration
async def test_raise_then_lower_then_on_restores_original_scene(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 150})
    hass.states.async_set("light.network_room_overhead_2", "on", {"brightness": 150})
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    await ctrl.lighting_raise()
    await ctrl.lighting_lower()
    assert ctrl._state.previous_scene == "evening"
    await ctrl.lighting_on()
    assert ctrl._state.scene_slug == "evening"
    assert ctrl._state.dimmed is False


@pytest.mark.integration
async def test_dimmed_in_circadian_then_on_restores_circadian(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.lighting_on()
    assert ctrl._state.is_circadian
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 150})
    hass.states.async_set("light.network_room_overhead_2", "on", {"brightness": 150})
    await ctrl.lighting_lower()
    assert ctrl._state.dimmed
    assert ctrl._state.previous_scene == "circadian"
    await ctrl.lighting_on()
    assert ctrl._state.is_circadian


# ── Persistence round-trip ──────────────────────────────────────────────


@pytest.mark.integration
async def test_persistence_full_round_trip(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.night_mode = True
    ctrl.ambience_enabled = True
    ctrl.manual_fadeout_seconds = 7.5
    ctrl.motion_fadeout_seconds = 4.0
    ctrl._state.transition_to_scene("christmas", ActivationSource.HOLIDAY)
    saved = ctrl.state_dict()

    from custom_components.area_lighting.controller import AreaLightingController

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    fresh.load_persisted_state(saved)
    assert fresh._state.scene_slug == "christmas"
    assert fresh._state.source == ActivationSource.HOLIDAY
    assert fresh.night_mode is True
    assert fresh.ambience_enabled is True
    assert fresh.manual_fadeout_seconds == 7.5
    assert fresh.motion_fadeout_seconds == 4.0


@pytest.mark.integration
async def test_persistence_dimmed_round_trip(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 150})
    hass.states.async_set("light.network_room_overhead_2", "on", {"brightness": 150})
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    await ctrl.lighting_lower()
    saved = ctrl.state_dict()

    from custom_components.area_lighting.controller import AreaLightingController

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    fresh.load_persisted_state(saved)
    assert fresh._state.dimmed is True
    assert fresh._state.previous_scene == "evening"


# ── Lights-off detection edge cases ─────────────────────────────────────


@pytest.mark.integration
async def test_external_lights_off_during_circadian_transitions_to_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.lighting_on()
    assert ctrl._state.is_circadian
    await ctrl.handle_lights_all_off()
    assert ctrl._state.is_off


# ── Source-tracking through fade ────────────────────────────────────────


@pytest.mark.integration
async def test_motion_timer_fade_when_no_ambience_marks_motion_source(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)
    await ctrl._on_motion_timer()
    assert ctrl._state.is_off
    assert ctrl._state.source == ActivationSource.MOTION


# ── lighting_circadian called twice doesn't break state ─────────────────


@pytest.mark.integration
async def test_circadian_called_twice_remains_consistent(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.lighting_circadian()
    snap1 = ctrl.diagnostic_snapshot()
    await ctrl.lighting_circadian()
    snap2 = ctrl.diagnostic_snapshot()
    assert snap1["state"] == snap2["state"] == "circadian"
    assert snap1["source"] == snap2["source"]


# ── Night mode toggle ──────────────────────────────────────────────────


@pytest.mark.integration
async def test_night_mode_toggle_changes_default_pick(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.night_mode = True
    await ctrl.lighting_on()
    assert ctrl._state.scene_slug == "night"
    await ctrl.lighting_off()
    ctrl.night_mode = False
    await ctrl.lighting_on()
    assert ctrl._state.is_circadian


# ── External scene activation clears dimmed ────────────────────────────


@pytest.mark.integration
async def test_external_scene_activation_clears_dimmed(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 150})
    hass.states.async_set("light.network_room_overhead_2", "on", {"brightness": 150})
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    await ctrl.lighting_lower()
    assert ctrl._state.dimmed
    await ctrl.handle_scene_activated("daylight")
    assert ctrl._state.scene_slug == "daylight"
    assert not ctrl._state.dimmed
    assert ctrl._state.previous_scene is None


# ── Manual then on ──────────────────────────────────────────────────────


@pytest.mark.integration
async def test_manual_change_then_on_uses_default(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    await ctrl.handle_manual_light_change()
    assert ctrl._state.is_manual
    await ctrl.lighting_on()
    assert ctrl._state.is_circadian


# ── Off when already off is safe ────────────────────────────────────────


@pytest.mark.integration
async def test_off_when_already_off_is_safe(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.lighting_off()
    assert ctrl._state.is_off


# ── Sun position sensitivity ────────────────────────────────────────────


@pytest.mark.integration
async def test_circadian_to_evening_when_sun_below(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set(
        "input_boolean.lighting_circadian_daylight_lights_enabled",
        "off",
    )
    await ctrl.lighting_on()
    await ctrl.lighting_on()
    assert ctrl._state.scene_slug == "daylight"


@pytest.mark.integration
async def test_circadian_to_daylight_when_sun_above(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set(
        "input_boolean.lighting_circadian_daylight_lights_enabled",
        "on",
    )
    await ctrl.lighting_on()
    await ctrl.lighting_on()
    assert ctrl._state.scene_slug == "evening"
