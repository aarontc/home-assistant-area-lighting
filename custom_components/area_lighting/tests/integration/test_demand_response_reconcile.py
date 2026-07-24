"""Demand-response edge reconcile: already-lit areas on flag flip."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _config() -> dict:
    entities = {f"light.den_{i}": {"state": "on", "brightness": 200} for i in range(1, 7)}
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "den",
                    "name": "Den",
                    "event_handlers": True,
                    "lights": [{"id": f"light.den_{i}", "roles": ["dimming"]} for i in range(1, 7)],
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
        hass.states.async_set(f"light.den_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_reconcile_sheds_already_lit_kept_untouched(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    # Lights are physically on (simulate the scene being active pre-DR).
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    _toggles(hass)._demand_response_active = True
    service_calls.clear()
    await ctrl.async_reconcile_demand_response()
    await hass.async_block_till_done()

    off = {
        c.data["entity_id"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    assert off == {f"light.den_{i}" for i in (3, 4, 5, 6)}  # shed tail turned off
    assert on == set()  # kept bulbs already on -> untouched


@pytest.mark.integration
async def test_reconcile_restores_on_clear(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    _toggles(hass)._demand_response_active = True
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()
    # Kept on, shed off (as DR produced).
    for i in (1, 2):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    for i in (3, 4, 5, 6):
        hass.states.async_set(f"light.den_{i}", "off", {})

    _toggles(hass)._demand_response_active = False
    service_calls.clear()
    await ctrl.async_reconcile_demand_response()
    await hass.async_block_till_done()

    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    assert on == {f"light.den_{i}" for i in (3, 4, 5, 6)}  # shed bulbs restored


@pytest.mark.integration
async def test_reconcile_skips_manual_and_off(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    ctrl._state.transition_to_manual()
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl.async_reconcile_demand_response()
    await hass.async_block_till_done()

    assert len(service_calls) == 0


@pytest.mark.integration
async def test_concurrent_reconciles_serialize(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """Two overlapping reconciles converge to one consistent shed state."""
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    _toggles(hass)._demand_response_active = True
    service_calls.clear()
    await asyncio.gather(
        ctrl.async_reconcile_demand_response(),
        ctrl.async_reconcile_demand_response(),
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
    assert on == set()  # no double-relight of kept or shed bulbs
    assert off == {f"light.den_{i}" for i in (3, 4, 5, 6)}
    assert ctrl.dr_shed_ids == frozenset({f"light.den_{i}" for i in (3, 4, 5, 6)})
    assert ctrl._active_scene_targets["light.den_4"]["state"] == "off"


@pytest.mark.integration
async def test_scene_activation_mid_flip_ends_shed(
    hass: HomeAssistant, helper_entities, service_calls, monkeypatch
) -> None:
    """DR flipping on mid-activation still ends with the tail shed.

    The setter's reconcile fired before transition_to_scene (it saw the
    old state and skipped), so the end-of-activation check must catch
    the flag change and converge.
    """
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})

    original = ctrl._apply_scene_data

    async def flip_after_apply(scene_slug, transition=None):
        await original(scene_slug, transition)
        _toggles(hass)._demand_response_active = True

    monkeypatch.setattr(ctrl, "_apply_scene_data", flip_after_apply)

    # Chronological recorder: the service_calls fixture groups by service,
    # so it cannot assert which command landed last on a bulb.
    chrono: list[tuple[str, str]] = []

    async def _record(call) -> None:
        chrono.append((call.service, call.data["entity_id"]))

    hass.services.async_register("light", "turn_on", _record)
    hass.services.async_register("light", "turn_off", _record)

    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    final = {eid: svc for svc, eid in chrono}
    for i in (1, 2):
        assert final[f"light.den_{i}"] == "turn_on"
    for i in (3, 4, 5, 6):
        assert final[f"light.den_{i}"] == "turn_off"
    assert ctrl.dr_shed_ids == frozenset({f"light.den_{i}" for i in (3, 4, 5, 6)})
    assert ctrl._active_scene_targets["light.den_4"]["state"] == "off"
