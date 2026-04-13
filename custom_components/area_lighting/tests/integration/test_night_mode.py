"""Night mode scoping tests (D6)."""

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
async def test_remote_on_with_night_mode_from_off_picks_night(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.night_mode = True
    await ctrl.lighting_on()
    assert ctrl._state.scene_slug == "night"


@pytest.mark.integration
async def test_second_on_while_in_night_cycles_to_circadian(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.night_mode = True
    await ctrl.lighting_on()
    assert ctrl._state.scene_slug == "night"
    await ctrl.lighting_on()
    assert ctrl._state.is_circadian


@pytest.mark.integration
async def test_night_mode_uses_motion_night_timer_for_motion_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.night_mode = True
    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)
    await ctrl.handle_motion_off()
    assert ctrl._motion_night_timer.is_active
    assert not ctrl._motion_timer.is_active


@pytest.mark.integration
async def test_night_mode_uses_occupancy_night_duration(
    hass: HomeAssistant, helper_entities
) -> None:
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "test_area",
                    "name": "Test Area",
                    "event_handlers": True,
                    "lights": [{"id": "light.test_area_a", "roles": ["color", "dimming"]}],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "night", "name": "Night"},
                    ],
                    "occupancy_light_timer_durations": {
                        "off": "00:30:00",
                        "night_off": "00:10:00",
                    },
                    "occupancy_light_sensor_ids": ["binary_sensor.test_area_occupancy"],
                }
            ]
        }
    }
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    ctrl = hass.data["area_lighting"]["controllers"]["test_area"]
    ctrl.night_mode = True
    assert ctrl._occupancy_off_duration() == 600  # 10 minutes

    ctrl.night_mode = False
    assert ctrl._occupancy_off_duration() == 1800  # 30 minutes


@pytest.mark.integration
async def test_night_fadeout_seconds_override(
    hass: HomeAssistant, helper_entities
) -> None:
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "test_area",
                    "name": "Test Area",
                    "event_handlers": False,
                    "night_fadeout_seconds": 1.5,
                    "lights": [{"id": "light.test_area_a", "roles": ["color"]}],
                    "scenes": [{"id": "circadian", "name": "Circadian"}],
                }
            ]
        }
    }
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    ctrl = hass.data["area_lighting"]["controllers"]["test_area"]
    ctrl.night_mode = True
    assert ctrl._motion_fade_seconds() == 1.5
    ctrl.night_mode = False
    assert ctrl._motion_fade_seconds() == ctrl.motion_fadeout_seconds


@pytest.mark.integration
async def test_night_mode_falls_back_to_normal_when_no_override(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """With no night_off / night_fadeout_seconds config, night mode reuses normal values."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.night_mode = True
    assert ctrl._occupancy_off_duration() == 1800
    assert ctrl._motion_fade_seconds() == ctrl.motion_fadeout_seconds
