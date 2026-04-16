"""Tests for the Occupancy Timeout Enabled per-area switch.

The switch defaults to on and gates the occupancy-off timer:
  - On: existing enforcement logic applies unchanged.
  - Off: _start_occupancy_timer() is a no-op.
  - On->Off transitions cancel a running timer without firing lights-off.
  - Off->On transitions re-arm via _enforce_occupancy_timer.
"""

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


def _config_with_occupancy() -> dict:
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "media_room",
                    "name": "Media Room",
                    "event_handlers": True,
                    "lights": [
                        {"id": "light.media_room_overhead", "roles": ["dimming"]},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "ambient", "name": "Ambient"},
                    ],
                    "occupancy_light_sensor_ids": [
                        "binary_sensor.media_room_presence",
                    ],
                    "occupancy_light_timer_durations": {
                        "off": "00:30:00",
                    },
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.media_room_presence",
                    ],
                    "motion_light_timer_durations": {
                        "off": "00:08:00",
                    },
                }
            ]
        }
    }


@pytest.mark.integration
async def test_defaults_to_enabled(hass: HomeAssistant, helper_entities) -> None:
    """Fresh controller has occupancy_timeout_enabled == True."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    assert ctrl.occupancy_timeout_enabled is True


@pytest.mark.integration
async def test_start_suppressed_when_disabled(
    hass: HomeAssistant, helper_entities
) -> None:
    """With the flag off, activating a scene does not arm the occupancy timer."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    # Directly mutate the flag; the public async setter is added in Task 3.
    ctrl._occupancy_timeout_enabled = False

    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_handle_occupancy_off_suppressed_when_disabled(
    hass: HomeAssistant, helper_entities
) -> None:
    """With the flag off, a sensor-clear event also does not arm the timer."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "on")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert not ctrl._occupancy_timer.is_active  # sensor on → no timer

    ctrl._occupancy_timeout_enabled = False
    # Simulate sensor going clear
    await ctrl.handle_occupancy_off()
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_on_to_off_cancels_running_timer_without_firing(
    hass: HomeAssistant, helper_entities
) -> None:
    """Switching off mid-countdown cancels the timer; lights stay on."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active
    assert ctrl._state.is_on  # lights on, area in active scene

    await ctrl.async_set_occupancy_timeout_enabled(False)
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active
    # The lights-off callback (_on_occupancy_timer) must NOT have fired
    assert ctrl._state.is_on
    assert ctrl._state.scene_slug == "circadian"


@pytest.mark.integration
async def test_off_to_on_rearms_when_area_occupied_and_sensors_clear(
    hass: HomeAssistant, helper_entities
) -> None:
    """Turning the flag back on while area is occupied re-arms the timer."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    # Pre-disable so the scene activation doesn't arm the timer
    await ctrl.async_set_occupancy_timeout_enabled(False)
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert not ctrl._occupancy_timer.is_active

    # Now flip it on — area is occupied, sensor is clear → should arm.
    await ctrl.async_set_occupancy_timeout_enabled(True)
    await hass.async_block_till_done()

    assert ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_set_is_idempotent(hass: HomeAssistant, helper_entities) -> None:
    """Setting the flag to its current value is a no-op."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    deadline_before = ctrl._occupancy_timer.deadline_utc

    # Flag is already True by default — this call must not reset the deadline.
    await ctrl.async_set_occupancy_timeout_enabled(True)
    await hass.async_block_till_done()

    assert ctrl._occupancy_timer.is_active
    assert ctrl._occupancy_timer.deadline_utc == deadline_before
