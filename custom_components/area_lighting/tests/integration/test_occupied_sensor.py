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
async def test_no_occupancy_sensors_always_off(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.shed_main", "off")
    hass.states.async_set("binary_sensor.shed_motion_1", "off")
    await _setup(hass, _config_without_occupancy())

    state = hass.states.get("binary_sensor.shed_occupied")
    assert state is not None
    assert state.state == STATE_OFF


@pytest.mark.integration
async def test_occupancy_sensor_on_sets_occupied(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.office_overhead", "off")
    hass.states.async_set("binary_sensor.office_occupancy_1", "off")
    hass.states.async_set("binary_sensor.office_motion_1", "off")
    await _setup(hass, _config_with_occupancy())

    assert hass.states.get("binary_sensor.office_occupied").state == STATE_OFF

    ctrl = hass.data["area_lighting"]["controllers"]["office"]
    hass.states.async_set("binary_sensor.office_occupancy_1", "on")
    await ctrl.handle_occupancy_on()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.office_occupied").state == STATE_ON


@pytest.mark.integration
async def test_occupancy_sensor_off_timer_keeps_occupied(
    hass: HomeAssistant, helper_entities
) -> None:
    hass.states.async_set("light.office_overhead", "on")
    hass.states.async_set("binary_sensor.office_occupancy_1", "on")
    hass.states.async_set("binary_sensor.office_motion_1", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["office"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    hass.states.async_set("binary_sensor.office_occupancy_1", "off")
    await ctrl.handle_occupancy_off()
    await hass.async_block_till_done()

    assert ctrl._occupancy_timer.is_active
    assert hass.states.get("binary_sensor.office_occupied").state == STATE_ON


@pytest.mark.integration
async def test_occupancy_timer_expires_clears_occupied(
    hass: HomeAssistant, helper_entities
) -> None:
    hass.states.async_set("light.office_overhead", "on")
    hass.states.async_set("binary_sensor.office_occupancy_1", "on")
    hass.states.async_set("binary_sensor.office_motion_1", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["office"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)

    hass.states.async_set("binary_sensor.office_occupancy_1", "off")
    await ctrl.handle_occupancy_off()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.office_occupied").state == STATE_ON

    # Fire the timer through its _fire() method so is_active clears properly
    await ctrl._occupancy_timer._fire()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.office_occupied").state == STATE_OFF


@pytest.mark.integration
async def test_multiple_sensors_one_still_on(hass: HomeAssistant, helper_entities) -> None:
    cfg = _config_with_occupancy()
    cfg["area_lighting"]["areas"][0]["occupancy_light_sensor_ids"].append(
        "binary_sensor.office_occupancy_2"
    )
    hass.states.async_set("light.office_overhead", "off")
    hass.states.async_set("binary_sensor.office_occupancy_1", "on")
    hass.states.async_set("binary_sensor.office_occupancy_2", "on")
    hass.states.async_set("binary_sensor.office_motion_1", "off")
    await _setup(hass, cfg)

    ctrl = hass.data["area_lighting"]["controllers"]["office"]
    assert hass.states.get("binary_sensor.office_occupied").state == STATE_ON

    hass.states.async_set("binary_sensor.office_occupancy_1", "off")
    ctrl._notify_state_change()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.office_occupied").state == STATE_ON
