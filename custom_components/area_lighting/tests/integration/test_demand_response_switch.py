"""Demand-response owned master switch: registration and end-to-end."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


def _config() -> dict:
    entities = {f"light.loft_{i}": {"state": "on", "brightness": 200} for i in range(1, 7)}
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "loft",
                    "name": "Loft",
                    "event_handlers": True,
                    "lights": [
                        {"id": f"light.loft_{i}", "roles": ["dimming"]} for i in range(1, 7)
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
        hass.states.async_set(f"light.loft_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_switch_registered_default_off_with_icon(
    hass: HomeAssistant, helper_entities
) -> None:
    await _setup(hass, _config())
    st = hass.states.get("switch.area_lighting_demand_response_active")
    assert st is not None
    assert st.state == "off"
    assert st.attributes["friendly_name"] == "Area Lighting Demand Response (Global)"
    assert st.attributes["icon"] == "mdi:transmission-tower"


@pytest.mark.integration
async def test_switch_service_call_sheds_then_restores(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    from custom_components.area_lighting.area_state import ActivationSource

    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["loft"]
    for i in range(1, 7):
        hass.states.async_set(f"light.loft_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    service_calls.clear()
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.area_lighting_demand_response_active"},
        blocking=True,
    )
    await hass.async_block_till_done()
    off = {
        c.data["entity_id"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    assert off == {f"light.loft_{i}" for i in (3, 4, 5, 6)}

    # Simulate the shed bulbs now being physically off, then clear DR.
    # Clearing re-drives the area through its normal activation path,
    # replaying the full unfiltered scene: the shed bulbs are relit and
    # nothing is turned off.
    for i in (3, 4, 5, 6):
        hass.states.async_set(f"light.loft_{i}", "off", {})
    service_calls.clear()
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.area_lighting_demand_response_active"},
        blocking=True,
    )
    await hass.async_block_till_done()
    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    off = {
        c.data["entity_id"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    assert {f"light.loft_{i}" for i in (3, 4, 5, 6)} <= on
    assert off == set()


@pytest.mark.integration
async def test_demand_response_flag_persists_across_reload(
    hass: HomeAssistant, helper_entities
) -> None:
    await _setup(hass, _config())
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.area_lighting_demand_response_active"},
        blocking=True,
    )
    await hass.async_block_till_done()
    persisted = hass.data["area_lighting"]["state_storage"].get_global_state()
    assert persisted["demand_response_active"] is True
