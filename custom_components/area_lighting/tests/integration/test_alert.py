"""Integration tests for alert mode."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting import alert
from custom_components.area_lighting.alert import execute_alert
from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.models import AlertPattern, AlertStep

NETWORK_LIGHTS = ("light.network_room_overhead_1", "light.network_room_overhead_2")


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
async def test_selfcheck_armed_by_the_scene_does_not_fight_an_alert(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """Walking into a room and firing an alert must not be healed away.

    Motion activates a scene, which arms a post-settle self-check a few
    seconds out. An automation then raises an alert inside that window. The
    check has to stand down instead of re-asserting scene brightness over
    the alert pattern.

    The scene carries a concrete brightness on purpose: a role-skeleton scene
    resolves to a bare `{"state": "on"}` target with nothing to compare, so it
    could never detect drift and would pass this test for the wrong reason.
    """
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "den",
                    "name": "Den",
                    "event_handlers": True,
                    "lights": [{"id": "light.den_a", "roles": ["dimming"]}],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {
                            "id": "daylight",
                            "name": "Daylight",
                            "entities": {"light.den_a": {"state": "on", "brightness": 200}},
                        },
                    ],
                }
            ]
        }
    }
    hass.states.async_set("light.den_a", "off", {})
    await _setup(hass, cfg)
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    await ctrl._activate_scene("daylight", ActivationSource.MOTION)
    await hass.async_block_till_done()
    assert ctrl._heal_selfcheck_handle is not None, "the scene should arm a check"

    # The alert takes the light somewhere the scene never asked for.
    ctrl._alert_active = True
    hass.states.async_set("light.den_a", "on", {"brightness": 255})
    await hass.async_block_till_done()

    service_calls.clear()
    # Fire the armed check now rather than waiting for its timer.
    ctrl._heal_selfcheck_handle.cancel()
    ctrl._run_post_settle_selfcheck()
    await hass.async_block_till_done()

    turn_ons = [c for c in service_calls if c.domain == "light" and c.service == "turn_on"]
    assert not turn_ons, f"self-check fought the alert: {turn_ons}"
    ctrl._alert_active = False


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


async def _run_alert(hass: HomeAssistant, ctrl, monkeypatch, during=None) -> None:
    """Run an alert whose steps send nothing, calling `during` while it owns the area."""

    async def steps(*_args) -> None:
        if during is not None:
            during()

    monkeypatch.setattr(alert, "_execute_steps", steps)
    pattern = AlertPattern(steps=[AlertStep(target="all", state="on")], restore=False)
    await execute_alert(hass, ctrl, pattern)
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_alert_end_resumes_a_stopped_circadian_adjuster(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian, monkeypatch
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, _config_with_alert_patterns())
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.lighting_circadian()
    for entity_id in NETWORK_LIGHTS:
        hass.states.async_set(entity_id, "on", {"brightness": 255})
    await hass.async_block_till_done()

    # A reload stops the adjuster while the alert owns the area.
    await _run_alert(hass, ctrl, monkeypatch, during=ctrl._stop_circadian)

    assert ctrl.circadian_running
    ctrl._circadian_commanded_at.clear()
    service_calls.clear()
    await pin_circadian(60, 4500)
    turn_ons = {
        c.data["entity_id"]: c.data["color_temp_kelvin"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_on"
    }
    assert turn_ons == dict.fromkeys(NETWORK_LIGHTS, 4500)


@pytest.mark.integration
async def test_alert_end_leaves_non_circadian_area_alone(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian, monkeypatch
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, _config_with_alert_patterns())
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("daylight", ActivationSource.USER)
    for entity_id in NETWORK_LIGHTS:
        hass.states.async_set(entity_id, "on", {"brightness": 255})
    await hass.async_block_till_done()
    service_calls.clear()

    await _run_alert(hass, ctrl, monkeypatch)

    assert not ctrl.circadian_running
    assert ctrl.current_scene == "daylight"
    assert [c for c in service_calls if c.domain == "light"] == []
