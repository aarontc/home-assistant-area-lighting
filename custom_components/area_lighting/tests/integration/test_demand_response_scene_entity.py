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


@pytest.mark.integration
async def test_external_visual_scene_records_shed_set(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """External visual-scene tracking under DR must record the shed set, and
    the DR-off re-activation must relight the shed bulbs."""
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["gallery"]
    _toggles(hass)._demand_response_active = True

    await ctrl.handle_scene_activated("bright")
    await hass.async_block_till_done()

    shed = {f"light.gallery_{i}" for i in (3, 4, 5, 6)}
    assert ctrl.dr_shed_ids == frozenset(shed)

    # Physical state matches the DR outcome: kept on, shed off. Clearing
    # the flag re-drives the area through its normal activation path,
    # which replays the full unfiltered scene: every shed bulb is relit
    # and nothing is turned off.
    for i in (1, 2):
        hass.states.async_set(f"light.gallery_{i}", "on", {"brightness": 200})
    for i in (3, 4, 5, 6):
        hass.states.async_set(f"light.gallery_{i}", "off", {})
    _toggles(hass)._demand_response_active = False

    service_calls.clear()
    await ctrl.reactivate_for_demand_response()
    await hass.async_block_till_done()

    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    off = {
        c.data["entity_id"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    assert shed <= on
    assert off == set()
    assert ctrl.dr_shed_ids == frozenset()
