"""Integration tests for alert mode."""

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
async def test_alert_active_defaults_false(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Controller starts with _alert_active == False."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl._alert_active is False


@pytest.mark.integration
async def test_alert_active_in_diagnostic_snapshot(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """diagnostic_snapshot includes alert_active."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    snap = ctrl.diagnostic_snapshot()
    assert "alert_active" in snap
    assert snap["alert_active"] is False


@pytest.mark.integration
async def test_manual_detection_suppressed_when_alert_active(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Light state changes during an alert do not trigger manual detection."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("daylight", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl.current_scene == "daylight"

    ctrl._alert_active = True

    hass.states.async_set(
        "light.network_room_overhead_1",
        "on",
        {"brightness": 50, "color_mode": "color_temp", "color_temp_kelvin": 4000},
    )
    await hass.async_block_till_done()

    assert ctrl.current_scene == "daylight"
    ctrl._alert_active = False


def _config_with_alert_patterns() -> dict:
    return {
        "area_lighting": {
            "alert_patterns": {
                "test_flash": {
                    "steps": [
                        {"target": "all", "state": "on", "brightness": 255, "delay": 0.0},
                        {"target": "all", "state": "off", "delay": 0.0},
                    ],
                    "repeat": 1,
                    "restore": True,
                },
            },
            "areas": [
                {
                    "id": "network_room",
                    "name": "Network Room",
                    "event_handlers": True,
                    "ambient_lighting_zone": "upstairs",
                    "circadian_switches": [
                        {"name": "Overhead", "max_brightness": 100, "min_brightness": 65},
                    ],
                    "lights": [
                        {
                            "id": "light.network_room_overhead_1",
                            "circadian_switch": "Overhead",
                            "circadian_type": "ct",
                            "roles": ["color", "dimming", "night", "white"],
                        },
                        {
                            "id": "light.network_room_overhead_2",
                            "circadian_switch": "Overhead",
                            "circadian_type": "ct",
                            "roles": ["color", "dimming", "night", "white"],
                        },
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "night", "name": "Night"},
                        {"id": "ambient", "name": "Ambient"},
                        {"id": "christmas", "name": "Christmas"},
                    ],
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.network_room_motion_sensor_motion",
                    ],
                    "motion_light_timer_durations": {
                        "off": "00:08:00",
                        "night_off": "00:05:00",
                    },
                    "occupancy_light_sensor_ids": [
                        "binary_sensor.network_room_motion_sensor_motion",
                    ],
                    "occupancy_light_timer_durations": {
                        "off": "00:30:00",
                    },
                },
            ],
        }
    }


@pytest.mark.integration
async def test_alert_service_triggers_alert(hass: HomeAssistant, helper_entities) -> None:
    """Calling area_lighting.alert executes the named pattern."""
    hass.states.async_set(
        "light.network_room_overhead_1",
        "on",
        {"brightness": 150, "supported_color_modes": ["color_temp"]},
    )
    hass.states.async_set(
        "light.network_room_overhead_2",
        "off",
        {"supported_color_modes": ["color_temp"]},
    )
    await _setup(hass, _config_with_alert_patterns())

    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl._alert_active is False

    await hass.services.async_call(
        "area_lighting",
        "alert",
        {"area_id": "network_room", "pattern": "test_flash"},
        blocking=True,
    )

    assert ctrl._alert_active is False


@pytest.mark.integration
async def test_alert_service_unknown_pattern_logs_warning(
    hass: HomeAssistant, helper_entities
) -> None:
    """Calling with a nonexistent pattern name logs a warning."""
    await _setup(hass, _config_with_alert_patterns())

    await hass.services.async_call(
        "area_lighting",
        "alert",
        {"area_id": "network_room", "pattern": "nonexistent"},
        blocking=True,
    )


@pytest.mark.integration
async def test_alert_service_all_areas(hass: HomeAssistant, helper_entities) -> None:
    """area_id 'all' dispatches to every controller."""
    hass.states.async_set(
        "light.network_room_overhead_1",
        "on",
        {"brightness": 150, "supported_color_modes": ["color_temp"]},
    )
    hass.states.async_set(
        "light.network_room_overhead_2",
        "off",
        {"supported_color_modes": ["color_temp"]},
    )
    await _setup(hass, _config_with_alert_patterns())

    await hass.services.async_call(
        "area_lighting",
        "alert",
        {"area_id": "all", "pattern": "test_flash"},
        blocking=True,
    )
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl._alert_active is False


@pytest.mark.integration
async def test_alert_preserves_timer_deadline(hass: HomeAssistant, helper_entities) -> None:
    """An active occupancy timer's deadline survives an alert."""
    hass.states.async_set(
        "light.network_room_overhead_1",
        "on",
        {"brightness": 150, "supported_color_modes": ["color_temp"]},
    )
    hass.states.async_set(
        "light.network_room_overhead_2",
        "off",
        {"supported_color_modes": ["color_temp"]},
    )
    hass.states.async_set("binary_sensor.network_room_motion_sensor_motion", "off")
    await _setup(hass, _config_with_alert_patterns())

    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("daylight", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active
    deadline_before = ctrl._occupancy_timer.deadline_utc

    await hass.services.async_call(
        "area_lighting",
        "alert",
        {"area_id": "network_room", "pattern": "test_flash"},
        blocking=True,
    )

    assert ctrl._occupancy_timer.is_active
    assert ctrl._occupancy_timer.deadline_utc == deadline_before
