"""Demand response filters the HA Scene entity (external scene.turn_on)."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _config() -> dict:
    entities = {f"light.gallery_{i}": {"state": "on", "brightness": 200} for i in range(1, 7)}
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "gallery",
                    "name": "Gallery",
                    "event_handlers": True,
                    "lights": [
                        {"id": f"light.gallery_{i}", "roles": ["dimming"]} for i in range(1, 7)
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "bright", "name": "Bright", "entities": entities},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    for i in range(1, 7):
        hass.states.async_set(f"light.gallery_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_scene_turn_on_is_filtered_under_dr(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.gallery_bright"}, blocking=True
    )
    await hass.async_block_till_done()

    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    assert on == {"light.gallery_1", "light.gallery_2"}
