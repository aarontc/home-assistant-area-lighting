"""Global master switches: motion-on and occupancy-timeout kill switches."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.global_state import GlobalToggles


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


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
                    "occupancy_light_timer_durations": {"off": "00:30:00"},
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.media_room_presence",
                    ],
                    "motion_light_timer_durations": {"off": "00:08:00"},
                }
            ]
        }
    }


@pytest.mark.integration
async def test_holder_created_and_defaults_on(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    toggles = _toggles(hass)
    assert isinstance(toggles, GlobalToggles)
    assert toggles.motion_lights_enabled is True
    assert toggles.occupancy_timeout_enabled is True
