"""Demand-response shedding: scene activations."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _bright_entities(n: int) -> dict:
    return {f"light.bright_room_{i}": {"state": "on", "brightness": 200} for i in range(1, n + 1)}


def _config(n_lights: int, scene_on: int) -> dict:
    """Area with `n_lights` bulbs and a 'bright' scene that turns `scene_on` on."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "bright_room",
                    "name": "Bright Room",
                    "event_handlers": True,
                    "lights": [
                        {"id": f"light.bright_room_{i}", "roles": ["dimming"]}
                        for i in range(1, n_lights + 1)
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "bright", "name": "Bright", "entities": _bright_entities(scene_on)},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict, n_lights: int) -> None:
    for i in range(1, n_lights + 1):
        hass.states.async_set(f"light.bright_room_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _on_off(service_calls):
    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    off = {
        c.data["entity_id"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    return on, off


@pytest.mark.integration
async def test_dr_sheds_six_light_scene_to_keep_two(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert on == {"light.bright_room_1", "light.bright_room_2"}
    assert {f"light.bright_room_{i}" for i in (3, 4, 5, 6)} <= off
    # Tracking marks shed bulbs off so self-heal / manual-detection leave them.
    assert ctrl._active_scene_targets["light.bright_room_4"]["state"] == "off"
    assert ctrl.dr_shed_ids == frozenset({f"light.bright_room_{i}" for i in (3, 4, 5, 6)})


@pytest.mark.integration
async def test_dr_sheds_two_light_scene_to_keep_one(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    # 25-bulb area, scene only turns on 2 -> n=2 -> keep 1.
    await _setup(hass, _config(25, 2), 25)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert on == {"light.bright_room_1"}
    assert "light.bright_room_2" in off


@pytest.mark.integration
async def test_no_shed_when_dr_inactive(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, _ = _on_off(service_calls)
    assert on == {f"light.bright_room_{i}" for i in range(1, 7)}
    assert ctrl.dr_shed_ids == frozenset()


@pytest.mark.integration
async def test_external_scene_tracking_is_filtered(hass: HomeAssistant, helper_entities) -> None:
    # handle_scene_activated tracks external scene.turn_on; under DR the
    # tracked targets must mark shed bulbs off (Task 8 filters the apply).
    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True

    await ctrl.handle_scene_activated("bright")
    await hass.async_block_till_done()

    assert ctrl._active_scene_targets["light.bright_room_1"]["state"] == "on"
    assert ctrl._active_scene_targets["light.bright_room_6"]["state"] == "off"


@pytest.mark.integration
async def test_diagnostics_expose_demand_response(hass: HomeAssistant, helper_entities) -> None:
    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    snap = ctrl.diagnostic_snapshot()
    assert snap["demand_response_active"] is True
    assert set(snap["demand_response_shed"]) == {f"light.bright_room_{i}" for i in (3, 4, 5, 6)}


@pytest.mark.integration
async def test_alert_bypasses_demand_response(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    cfg = _config(6, 6)
    cfg["area_lighting"]["alert_patterns"] = {
        "flash": {"steps": [{"target": "all", "state": "on", "brightness": 255}], "restore": False}
    }
    await _setup(hass, cfg, 6)
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await hass.services.async_call(
        "area_lighting", "alert", {"area_id": "bright_room", "pattern": "flash"}, blocking=True
    )
    await hass.async_block_till_done()

    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    # Alerts bypass DR: every bulb flashes, none are shed.
    assert on == {f"light.bright_room_{i}" for i in range(1, 7)}
