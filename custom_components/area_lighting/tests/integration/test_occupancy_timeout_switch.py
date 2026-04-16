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
