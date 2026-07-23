"""Demand-response shedding: circadian activation and dark bring-up."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _config() -> dict:
    # 6 lights, all bound to one circadian switch -> circadian on-set = 6.
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "study",
                    "name": "Study",
                    "event_handlers": True,
                    "circadian_switches": [
                        {"name": "Main", "max_brightness": 100, "min_brightness": 40},
                    ],
                    "lights": [
                        {
                            "id": f"light.study_{i}",
                            "circadian_switch": "Main",
                            "circadian_type": "ct",
                            "roles": ["dimming"],
                        }
                        for i in range(1, 7)
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    for i in range(1, 7):
        hass.states.async_set(f"light.study_{i}", "off", {})
    hass.states.async_set(
        "switch.circadian_lighting_study_main_circadian",
        "on",
        {"brightness": 80.0, "colortemp": 3500},
    )
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_circadian_sheds_to_keep_two(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    off = {
        c.data["entity_id"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    assert on == {"light.study_1", "light.study_2"}
    assert {f"light.study_{i}" for i in (3, 4, 5, 6)} <= off
    assert ctrl.dr_shed_ids == frozenset({f"light.study_{i}" for i in (3, 4, 5, 6)})


@pytest.mark.integration
async def test_dark_bring_up_sheds(hass: HomeAssistant, helper_entities, service_calls) -> None:
    # raise from a fully-dark area brings all lights to min; under DR only the
    # kept lights come up.
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._set_all_lights_to_pct(12)
    await hass.async_block_till_done()

    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    assert on == {"light.study_1", "light.study_2"}
