"""Diagnostic snapshot must expose current state and last_changed for all motion/occupancy sensors."""

from __future__ import annotations

from datetime import datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.fixture
def network_room_with_occupancy_config(network_room_config) -> dict:
    """Extend the standard network_room_config to also include occupancy sensors."""
    area = network_room_config["area_lighting"]["areas"][0]
    area["occupancy_light_sensor_ids"] = [
        "binary_sensor.network_room_occupancy",
    ]
    return network_room_config


@pytest.mark.integration
async def test_snapshot_includes_motion_sensors_key(
    hass: HomeAssistant,
    helper_entities,
    network_room_config,
) -> None:
    """diagnostic_snapshot() must contain a 'motion_sensors' key."""
    hass.states.async_set("binary_sensor.network_room_motion_sensor_motion", "off", {})
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    snap = ctrl.diagnostic_snapshot()
    assert "motion_sensors" in snap


@pytest.mark.integration
async def test_snapshot_motion_sensor_state_and_last_changed(
    hass: HomeAssistant,
    helper_entities,
    network_room_config,
) -> None:
    """Each motion sensor entry must have state and last_changed."""
    hass.states.async_set("binary_sensor.network_room_motion_sensor_motion", "on", {})
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    snap = ctrl.diagnostic_snapshot()
    sensors = snap["motion_sensors"]

    assert "binary_sensor.network_room_motion_sensor_motion" in sensors
    entry = sensors["binary_sensor.network_room_motion_sensor_motion"]
    assert entry["state"] == "on"
    assert entry["last_changed"] is not None
    # last_changed should be a parseable ISO timestamp
    dt = datetime.fromisoformat(entry["last_changed"])
    assert dt.tzinfo is not None


@pytest.mark.integration
async def test_snapshot_merges_motion_and_occupancy_sensors(
    hass: HomeAssistant,
    helper_entities,
    network_room_with_occupancy_config,
) -> None:
    """Both motion and occupancy sensors must appear in the merged dict."""
    hass.states.async_set("binary_sensor.network_room_motion_sensor_motion", "on", {})
    hass.states.async_set("binary_sensor.network_room_occupancy", "off", {})
    await _setup(hass, network_room_with_occupancy_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    snap = ctrl.diagnostic_snapshot()
    sensors = snap["motion_sensors"]

    assert "binary_sensor.network_room_motion_sensor_motion" in sensors
    assert sensors["binary_sensor.network_room_motion_sensor_motion"]["state"] == "on"

    assert "binary_sensor.network_room_occupancy" in sensors
    assert sensors["binary_sensor.network_room_occupancy"]["state"] == "off"


@pytest.mark.integration
async def test_snapshot_sensor_missing_from_ha(
    hass: HomeAssistant,
    helper_entities,
    network_room_config,
) -> None:
    """If a configured sensor has no HA state object, state and last_changed should be None."""
    # Deliberately do NOT set any state for the motion sensor entity
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    snap = ctrl.diagnostic_snapshot()
    sensors = snap["motion_sensors"]

    entry = sensors["binary_sensor.network_room_motion_sensor_motion"]
    assert entry["state"] is None
    assert entry["last_changed"] is None


@pytest.mark.integration
async def test_snapshot_empty_when_no_sensors_configured(
    hass: HomeAssistant,
    helper_entities,
) -> None:
    """An area with no motion or occupancy sensors should have an empty dict."""
    config = {
        "area_lighting": {
            "areas": [
                {
                    "id": "bare_room",
                    "name": "Bare Room",
                    "lights": [
                        {
                            "id": "light.bare_room_light",
                            "roles": ["dimming"],
                        },
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                    ],
                }
            ]
        }
    }
    hass.states.async_set("light.bare_room_light", "off", {})
    await _setup(hass, config)
    ctrl = hass.data["area_lighting"]["controllers"]["bare_room"]
    snap = ctrl.diagnostic_snapshot()
    assert snap["motion_sensors"] == {}


@pytest.mark.integration
async def test_diagnostic_state_text_shows_motion_sensors(
    hass: HomeAssistant,
    helper_entities,
    network_room_config,
) -> None:
    """The human-readable state_text should include the motion sensor states."""
    hass.states.async_set("binary_sensor.network_room_motion_sensor_motion", "off", {})
    await _setup(hass, network_room_config)

    sensor_state = hass.states.get("sensor.area_lighting_diagnostics")
    assert sensor_state is not None
    text = sensor_state.attributes.get("state_text", "")
    assert "motion_sensors" in text
    assert "binary_sensor.network_room_motion_sensor_motion" in text
