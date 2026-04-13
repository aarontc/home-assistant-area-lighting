"""Tests for occupancy timer enforcement.

The occupancy timer should run whenever an area is in a non-off,
non-ambient state and no occupancy sensor is currently on. This
ensures lights don't stay on forever when someone leaves the room
without triggering a sensor-off event.
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


def _config_without_occupancy() -> dict:
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "hallway",
                    "name": "Hallway",
                    "event_handlers": True,
                    "lights": [
                        {"id": "light.hallway_overhead", "roles": ["dimming"]},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                    ],
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.hallway_motion",
                    ],
                    "motion_light_timer_durations": {
                        "off": "00:08:00",
                    },
                }
            ]
        }
    }


# ── Timer starts on scene activation ─────────────────────────────────


@pytest.mark.integration
async def test_timer_starts_when_user_activates_scene_from_off(
    hass: HomeAssistant, helper_entities
) -> None:
    """User activates circadian from off, no occupancy sensor on → timer starts."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    assert not ctrl._occupancy_timer.is_active

    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    assert ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_timer_starts_when_transitioning_from_ambient_to_active(
    hass: HomeAssistant, helper_entities
) -> None:
    """Area goes from ambient to circadian, no sensor on → timer starts."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("ambient", ActivationSource.AMBIENCE)
    await hass.async_block_till_done()
    assert not ctrl._occupancy_timer.is_active  # ambient → no timer

    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_timer_does_not_start_when_sensor_is_on(
    hass: HomeAssistant, helper_entities
) -> None:
    """User activates circadian, occupancy sensor is on → no timer."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "on")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active


# ── Timer cancelled on off/ambient ───────────────────────────────────


@pytest.mark.integration
async def test_timer_cancelled_when_area_goes_off(
    hass: HomeAssistant, helper_entities
) -> None:
    """Area goes to off → timer cancelled."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active

    await ctrl._activate_scene("off_internal", ActivationSource.USER)
    await hass.async_block_till_done()
    assert not ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_timer_cancelled_when_area_goes_ambient(
    hass: HomeAssistant, helper_entities
) -> None:
    """Area goes to ambient → timer cancelled."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active

    await ctrl._activate_scene("ambient", ActivationSource.AMBIENCE)
    await hass.async_block_till_done()
    assert not ctrl._occupancy_timer.is_active


# ── Timer not reset on scene-to-scene changes ────────────────────────


@pytest.mark.integration
async def test_timer_not_reset_on_active_to_active_transition(
    hass: HomeAssistant, helper_entities
) -> None:
    """Changing from daylight to evening shouldn't reset the timer."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("daylight", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active

    # Record the deadline
    deadline_before = ctrl._occupancy_timer.deadline_utc

    await ctrl._activate_scene("evening", ActivationSource.USER)
    await hass.async_block_till_done()

    # Timer should still be active with the SAME deadline (not reset)
    assert ctrl._occupancy_timer.is_active
    assert ctrl._occupancy_timer.deadline_utc == deadline_before


# ── Sensor interaction ────────────────────────────────────────────────


@pytest.mark.integration
async def test_occupancy_on_cancels_timer(
    hass: HomeAssistant, helper_entities
) -> None:
    """Positive occupancy detection cancels the timer."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active

    hass.states.async_set("binary_sensor.media_room_presence", "on")
    await ctrl.handle_occupancy_on()
    await hass.async_block_till_done()
    assert not ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_occupancy_off_restarts_timer(
    hass: HomeAssistant, helper_entities
) -> None:
    """Occupancy sensor clearing restarts the timer with full duration."""
    hass.states.async_set("light.media_room_overhead", "on")
    hass.states.async_set("binary_sensor.media_room_presence", "on")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    # Sensor is on, so timer should NOT be active
    assert not ctrl._occupancy_timer.is_active

    # Sensor goes off → timer restarts
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await ctrl.handle_occupancy_off()
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active


# ── No occupancy sensors configured ──────────────────────────────────


@pytest.mark.integration
async def test_no_occupancy_sensors_no_timer(
    hass: HomeAssistant, helper_entities
) -> None:
    """Areas without occupancy sensors don't get an occupancy timer."""
    hass.states.async_set("light.hallway_overhead", "off")
    hass.states.async_set("binary_sensor.hallway_motion", "off")
    await _setup(hass, _config_without_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["hallway"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active


# ── Startup / state restore ───────────────────────────────────────────


@pytest.mark.integration
async def test_timer_starts_on_startup_when_area_is_active(
    hass: HomeAssistant, helper_entities
) -> None:
    """After HA restart, area restored to active state → timer starts."""
    hass.states.async_set("light.media_room_overhead", "on")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    # Simulate restored state: area is circadian, no timer was persisted
    ctrl._state.transition_to_circadian(ActivationSource.RESTORED)
    ctrl._pending_timer_restore = {}

    await ctrl.restore_timers()
    await hass.async_block_till_done()

    assert ctrl._occupancy_timer.is_active


# ── External / manual light changes ──────────────────────────────────


@pytest.mark.integration
async def test_timer_starts_on_manual_light_change(
    hass: HomeAssistant, helper_entities
) -> None:
    """External integration turns on a light → area goes manual → timer starts."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    assert not ctrl._occupancy_timer.is_active

    # Simulate external light change detected by area_lighting
    await ctrl.handle_manual_light_change()
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_timer_starts_on_occupancy_lights_on(
    hass: HomeAssistant, helper_entities
) -> None:
    """Lights aggregate transitions from off to on → timer starts."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    # Put area in an active state without going through _activate_scene
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    assert not ctrl._occupancy_timer.is_active

    # Simulate light aggregate on transition
    await ctrl.handle_occupancy_lights_on()
    await hass.async_block_till_done()

    assert ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_timer_cancelled_on_occupancy_lights_off(
    hass: HomeAssistant, helper_entities
) -> None:
    """All lights go off externally → timer cancelled."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active

    await ctrl.handle_occupancy_lights_off()
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active
