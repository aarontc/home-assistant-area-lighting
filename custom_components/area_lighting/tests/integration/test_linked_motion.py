"""Tests for linked_motion cross-area coordination."""

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


def _two_area_config() -> dict:
    """Config with stairs (has linked_motion) and theater (target)."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "theater",
                    "name": "Theater",
                    "event_handlers": True,
                    "lights": [
                        {"id": "light.theater_overhead", "roles": ["dimming"]},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "movie", "name": "Movie"},
                        {"id": "stairs", "name": "Stairs"},
                    ],
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.theater_motion",
                    ],
                    "motion_light_timer_durations": {"off": "00:08:00"},
                },
                {
                    "id": "stairs",
                    "name": "Stairs",
                    "event_handlers": True,
                    "lights": [
                        {"id": "light.stairs_upper", "roles": ["dimming"]},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "movie", "name": "Movie"},
                    ],
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.stairs_motion",
                    ],
                    "motion_light_timer_durations": {"off": "00:02:00"},
                    "linked_motion": [
                        {
                            "remote_area": "theater",
                            "default": {
                                "local_scene": "circadian",
                                "remote_scene": None,
                            },
                            "when_remote_scene": {
                                "off": {
                                    "local_scene": "circadian",
                                    "remote_scene": "stairs",
                                },
                                "movie": {
                                    "local_scene": "movie",
                                    "remote_scene": None,
                                },
                            },
                        }
                    ],
                },
            ]
        }
    }


@pytest.mark.integration
async def test_linked_basic_activation_theater_off(hass: HomeAssistant, helper_entities) -> None:
    """Stairs motion + theater off -> stairs=circadian, theater=stairs."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    assert stairs_ctrl._state.scene_slug == "circadian" or stairs_ctrl._state.is_circadian
    assert theater_ctrl._state.scene_slug == "stairs"
    assert theater_ctrl._state.source == ActivationSource.LINKED


@pytest.mark.integration
async def test_linked_movie_mode(hass: HomeAssistant, helper_entities) -> None:
    """Stairs motion + theater in movie -> stairs=movie, theater untouched."""
    hass.states.async_set("light.theater_overhead", "on")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]
    theater_ctrl._state.transition_to_scene("movie", ActivationSource.USER)

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    assert stairs_ctrl._state.scene_slug == "movie"
    assert theater_ctrl._state.scene_slug == "movie"
    assert theater_ctrl._state.source == ActivationSource.USER


@pytest.mark.integration
async def test_linked_default_no_match(hass: HomeAssistant, helper_entities) -> None:
    """Stairs motion + theater in daylight -> stairs=circadian, theater untouched."""
    hass.states.async_set("light.theater_overhead", "on")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]
    theater_ctrl._state.transition_to_scene("daylight", ActivationSource.USER)

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    assert stairs_ctrl._state.scene_slug == "circadian" or stairs_ctrl._state.is_circadian
    assert theater_ctrl._state.scene_slug == "daylight"
    assert theater_ctrl._state.source == ActivationSource.USER


@pytest.mark.integration
async def test_linked_cleanup_normal(hass: HomeAssistant, helper_entities) -> None:
    """Stairs timer expires + theater still in 'stairs' -> theater goes off."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()
    assert theater_ctrl._state.scene_slug == "stairs"

    await stairs_ctrl.handle_motion_off()
    await hass.async_block_till_done()

    # Fire timer callback directly
    await stairs_ctrl._on_motion_timer()
    await hass.async_block_till_done()

    assert theater_ctrl._state.is_off


@pytest.mark.integration
async def test_linked_cleanup_manual_override(hass: HomeAssistant, helper_entities) -> None:
    """Theater changed manually during stairs activity -> cleanup skips theater."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()
    assert theater_ctrl._state.scene_slug == "stairs"

    # User manually changes theater to evening
    theater_ctrl._state.transition_to_scene("evening", ActivationSource.USER)

    await stairs_ctrl._on_motion_timer()
    await hass.async_block_till_done()

    # Theater should stay in evening — NOT forced to off
    assert theater_ctrl._state.scene_slug == "evening"


@pytest.mark.integration
async def test_linked_cleanup_null_remote_scene(hass: HomeAssistant, helper_entities) -> None:
    """When remote_scene was null (movie case), no cleanup needed."""
    hass.states.async_set("light.theater_overhead", "on")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]
    theater_ctrl._state.transition_to_scene("movie", ActivationSource.USER)

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    await stairs_ctrl._on_motion_timer()
    await hass.async_block_till_done()

    assert theater_ctrl._state.scene_slug == "movie"


@pytest.mark.integration
async def test_linked_retrigger_no_duplicate(hass: HomeAssistant, helper_entities) -> None:
    """Re-triggering stairs motion doesn't re-activate theater if it was changed."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()
    assert theater_ctrl._state.scene_slug == "stairs"

    # User changes theater while stairs is active
    theater_ctrl._state.transition_to_scene("evening", ActivationSource.USER)

    # Stairs re-triggers — theater is in "evening", not in "off" or "movie",
    # so default mapping applies (remote_scene=null), theater untouched
    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    assert theater_ctrl._state.scene_slug == "evening"


@pytest.mark.integration
async def test_linked_invalid_remote_area(hass: HomeAssistant, helper_entities) -> None:
    """linked_motion references non-existent area -> local area still works."""
    cfg = _two_area_config()
    cfg["area_lighting"]["areas"][1]["linked_motion"][0]["remote_area"] = "nonexistent"
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, cfg)

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    # Stairs still activates (falls back to default local_scene since resolve
    # can't find the remote controller, but doesn't crash)
    assert stairs_ctrl._state.scene_slug == "circadian" or stairs_ctrl._state.is_circadian
