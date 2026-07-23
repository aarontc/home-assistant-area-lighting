"""Demand response: the kelvin router never lights a shed route bulb."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.global_state import GlobalToggles

_SWITCH = "switch.circadian_lighting_kitchen_kitchen_circadian"


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _kitchen_routes_config() -> dict:
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "event_handlers": True,
                    "circadian_switches": [
                        {"name": "Kitchen", "max_brightness": 100, "min_brightness": 20},
                    ],
                    "lights": [
                        {
                            "id": "light.kitchen_fluorescent",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_1",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_2",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_3",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                    "circadian_kelvin_routes": {
                        "crossfade_seconds": 1.0,
                        "routes": [
                            {"kelvin_range": [4500, 5500], "lights": ["light.kitchen_fluorescent"]},
                            {
                                "lights": [
                                    "light.kitchen_strip_1",
                                    "light.kitchen_strip_2",
                                    "light.kitchen_strip_3",
                                ]
                            },
                        ],
                    },
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, colortemp: int) -> None:
    for eid in (
        "light.kitchen_fluorescent",
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    ):
        hass.states.async_set(eid, "off", {})
    hass.states.async_set(_SWITCH, "on", {"brightness": 75.0, "colortemp": colortemp})
    assert await async_setup_component(hass, "area_lighting", _kitchen_routes_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_router_never_lights_shed_route_bulbs(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, colortemp=3000)  # fallback route (all 3 strips) active
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    assert ctrl.dr_shed_ids == frozenset({"light.kitchen_strip_2", "light.kitchen_strip_3"})
    on = {
        c.data.get("entity_id")
        for c in service_calls
        if c.domain == "light" and c.service == "turn_on"
    }
    assert "light.kitchen_strip_1" in on
    assert on.isdisjoint({"light.kitchen_strip_2", "light.kitchen_strip_3"})
