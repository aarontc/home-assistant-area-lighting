"""Tests for binary_sensor.<area>_occupied."""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _config_with_occupancy() -> dict:
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "office",
                    "name": "Office",
                    "event_handlers": True,
                    "lights": [
                        {"id": "light.office_overhead", "roles": ["dimming"]},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "ambient", "name": "Ambient"},
                        {"id": "christmas", "name": "Christmas"},
                    ],
                    "occupancy_light_sensor_ids": [
                        "binary_sensor.office_occupancy_1",
                    ],
                    "occupancy_light_timer_durations": {
                        "off": "00:01:00",
                    },
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.office_motion_1",
                    ],
                    "motion_light_timer_durations": {
                        "off": "00:08:00",
                    },
                }
            ]
        }
    }


def _config_without_occupancy() -> dict:
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "shed",
                    "name": "Shed",
                    "event_handlers": True,
                    "lights": [
                        {"id": "light.shed_main", "roles": ["dimming"]},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                    ],
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.shed_motion_1",
                    ],
                    "motion_light_timer_durations": {"off": "00:08:00"},
                }
            ]
        }
    }


@pytest.mark.integration
async def test_off_state_not_occupied(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.shed_main", "off")
    hass.states.async_set("binary_sensor.shed_motion_1", "off")
    await _setup(hass, _config_without_occupancy())

    state = hass.states.get("binary_sensor.shed_occupied")
    assert state is not None
    assert state.state == STATE_OFF


@pytest.mark.integration
async def test_user_scene_sets_occupied(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.office_overhead", "off")
    hass.states.async_set("binary_sensor.office_occupancy_1", "off")
    hass.states.async_set("binary_sensor.office_motion_1", "off")
    await _setup(hass, _config_with_occupancy())

    assert hass.states.get("binary_sensor.office_occupied").state == STATE_OFF

    ctrl = hass.data["area_lighting"]["controllers"]["office"]
    ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    ctrl._notify_state_change()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.office_occupied").state == STATE_ON


@pytest.mark.integration
async def test_motion_scene_sets_occupied(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.office_overhead", "off")
    hass.states.async_set("binary_sensor.office_occupancy_1", "off")
    hass.states.async_set("binary_sensor.office_motion_1", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["office"]
    ctrl._state.transition_to_scene("circadian", ActivationSource.MOTION)
    ctrl._notify_state_change()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.office_occupied").state == STATE_ON


@pytest.mark.integration
async def test_ambient_source_not_occupied(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.office_overhead", "off")
    hass.states.async_set("binary_sensor.office_occupancy_1", "off")
    hass.states.async_set("binary_sensor.office_motion_1", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["office"]
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    ctrl._notify_state_change()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.office_occupied").state == STATE_OFF


@pytest.mark.integration
async def test_holiday_source_not_occupied(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.office_overhead", "off")
    hass.states.async_set("binary_sensor.office_occupancy_1", "off")
    hass.states.async_set("binary_sensor.office_motion_1", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["office"]
    ctrl._state.transition_to_scene("christmas", ActivationSource.HOLIDAY)
    ctrl._notify_state_change()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.office_occupied").state == STATE_OFF


@pytest.mark.integration
async def test_holiday_scene_manual_trigger_is_occupied(
    hass: HomeAssistant, helper_entities
) -> None:
    """A holiday scene triggered by a human (remote press) IS occupied."""
    hass.states.async_set("light.office_overhead", "off")
    hass.states.async_set("binary_sensor.office_occupancy_1", "off")
    hass.states.async_set("binary_sensor.office_motion_1", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["office"]
    ctrl._state.transition_to_scene("christmas", ActivationSource.USER)
    ctrl._notify_state_change()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.office_occupied").state == STATE_ON


@pytest.mark.integration
async def test_scene_off_clears_occupied(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.office_overhead", "on")
    hass.states.async_set("binary_sensor.office_occupancy_1", "off")
    hass.states.async_set("binary_sensor.office_motion_1", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["office"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    ctrl._notify_state_change()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.office_occupied").state == STATE_ON

    ctrl._state.transition_to_off()
    ctrl._notify_state_change()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.office_occupied").state == STATE_OFF


@pytest.mark.integration
async def test_motion_sensor_alone_not_occupied(hass: HomeAssistant, helper_entities) -> None:
    """Raw motion sensor activity without a scene change does not set occupied."""
    hass.states.async_set("light.office_overhead", "off")
    hass.states.async_set("binary_sensor.office_occupancy_1", "off")
    hass.states.async_set("binary_sensor.office_motion_1", "off")
    await _setup(hass, _config_with_occupancy())

    # Simulate motion sensor going on without area_lighting acting on it
    hass.states.async_set("binary_sensor.office_occupancy_1", "on")
    ctrl = hass.data["area_lighting"]["controllers"]["office"]
    await ctrl.handle_occupancy_on()
    await hass.async_block_till_done()

    # Area is still off, so not occupied even though sensor fired
    assert hass.states.get("binary_sensor.office_occupied").state == STATE_OFF


@pytest.mark.integration
async def test_manual_source_is_occupied(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.office_overhead", "off")
    hass.states.async_set("binary_sensor.office_occupancy_1", "off")
    hass.states.async_set("binary_sensor.office_motion_1", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["office"]
    ctrl._state.transition_to_scene("manual", ActivationSource.MANUAL)
    ctrl._notify_state_change()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.office_occupied").state == STATE_ON
