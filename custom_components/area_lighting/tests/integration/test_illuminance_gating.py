"""Integration tests: motion lighting gated by aggregated illuminance."""

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


def _gated_config() -> dict:
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "test_yard",
                    "name": "Test Yard",
                    "event_handlers": True,
                    "lights": [
                        {"id": "light.test_yard_flood", "roles": ["color"]},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.test_yard_motion",
                    ],
                    "motion_light_conditions": [
                        {
                            "entity_ids": [
                                "sensor.test_yard_lux_a",
                                "sensor.test_yard_lux_b",
                            ],
                            "aggregate": "average",
                            "below": 100,
                        },
                    ],
                },
            ],
        },
    }


@pytest.mark.integration
async def test_motion_activates_when_average_lux_below_threshold(
    hass: HomeAssistant,
    helper_entities,
) -> None:
    """Avg lux = 50 -> below 100 -> motion fires a scene."""
    hass.states.async_set("sensor.test_yard_lux_a", "30")
    hass.states.async_set("sensor.test_yard_lux_b", "70")
    hass.states.async_set("binary_sensor.test_yard_motion", "off")

    await _setup(hass, _gated_config())
    ctrl = hass.data["area_lighting"]["controllers"]["test_yard"]
    ctrl.motion_light_enabled = True
    assert ctrl._state.is_off

    hass.states.async_set("binary_sensor.test_yard_motion", "on")
    await hass.async_block_till_done()

    assert not ctrl._state.is_off
    assert ctrl._state.source == ActivationSource.MOTION


@pytest.mark.integration
async def test_motion_blocked_when_average_lux_at_or_above_threshold(
    hass: HomeAssistant,
    helper_entities,
) -> None:
    """Avg lux = 125 -> not below 100 -> motion does not fire."""
    hass.states.async_set("sensor.test_yard_lux_a", "50")
    hass.states.async_set("sensor.test_yard_lux_b", "200")
    hass.states.async_set("binary_sensor.test_yard_motion", "off")

    await _setup(hass, _gated_config())
    ctrl = hass.data["area_lighting"]["controllers"]["test_yard"]
    ctrl.motion_light_enabled = True
    assert ctrl._state.is_off

    hass.states.async_set("binary_sensor.test_yard_motion", "on")
    await hass.async_block_till_done()

    assert ctrl._state.is_off  # unchanged


@pytest.mark.integration
async def test_motion_activates_with_one_sensor_unavailable(
    hass: HomeAssistant,
    helper_entities,
) -> None:
    """One sensor unavailable: aggregate uses the remaining one (50 < 100)."""
    hass.states.async_set("sensor.test_yard_lux_a", "50")
    hass.states.async_set("sensor.test_yard_lux_b", "unavailable")
    hass.states.async_set("binary_sensor.test_yard_motion", "off")

    await _setup(hass, _gated_config())
    ctrl = hass.data["area_lighting"]["controllers"]["test_yard"]
    ctrl.motion_light_enabled = True

    hass.states.async_set("binary_sensor.test_yard_motion", "on")
    await hass.async_block_till_done()

    assert not ctrl._state.is_off
    assert ctrl._state.source == ActivationSource.MOTION


@pytest.mark.integration
async def test_motion_blocked_when_all_sensors_unavailable(
    hass: HomeAssistant,
    helper_entities,
) -> None:
    """All sensors unavailable -> condition fails -> motion does not fire."""
    hass.states.async_set("sensor.test_yard_lux_a", "unavailable")
    hass.states.async_set("sensor.test_yard_lux_b", "unknown")
    hass.states.async_set("binary_sensor.test_yard_motion", "off")

    await _setup(hass, _gated_config())
    ctrl = hass.data["area_lighting"]["controllers"]["test_yard"]
    ctrl.motion_light_enabled = True

    hass.states.async_set("binary_sensor.test_yard_motion", "on")
    await hass.async_block_till_done()

    assert ctrl._state.is_off  # unchanged
