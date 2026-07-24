"""Demand response must never drive cluster (Hue Zone) entities.

A stored snapshot captures `area.all_lights`, which includes the cluster
entity (e.g. `light.zone_all`) with state `on`. `apply_demand_response`
only sheds individual lights, so without a dedicated filter the zone
survives as an `on` target: applying it turns the whole zone on,
relighting shed members, and a later reconcile sees the zone `off` and
turns it back on (idempotency break). Under DR, only individual members
may be driven; clusters remain a pure batching optimization.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles

MEMBERS = [f"light.zone_{suffix}" for suffix in "abcdef"]
ZONE = "light.zone_all"
KEPT = {"light.zone_a", "light.zone_b"}
SHED = {"light.zone_c", "light.zone_d", "light.zone_e", "light.zone_f"}


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _config() -> dict:
    """Six members plus a Hue-Zone cluster over all of them.

    The bright scene's `entities` mirror what `snapshot_scene` captures:
    every member AND the cluster entity itself, all `on`.
    """
    entities: dict = {ZONE: {"state": "on"}}
    entities.update({m: {"state": "on", "brightness": 200} for m in MEMBERS})
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "zone_room",
                    "name": "Zone Room",
                    "event_handlers": True,
                    "lights": [{"id": m, "roles": ["dimming"]} for m in MEMBERS],
                    "light_clusters": [{"id": ZONE, "members": list(MEMBERS)}],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "bright", "name": "Bright", "entities": entities},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant) -> None:
    for entity_id in [*MEMBERS, ZONE]:
        hass.states.async_set(entity_id, "off", {})
    assert await async_setup_component(hass, "area_lighting", _config())
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
async def test_dr_scene_activation_never_drives_cluster_entity(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass)
    ctrl = hass.data["area_lighting"]["controllers"]["zone_room"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    # The zone must receive NO turn_on: it would relight the shed members.
    assert ZONE not in on
    assert on == KEPT
    assert off >= SHED
    # Tracking must not carry a stale `on` target for the zone either.
    zone_target = ctrl._active_scene_targets.get(ZONE)
    assert zone_target is None or zone_target.get("state") != "on"

    # Idempotency: with the physical states matching the DR outcome
    # (kept on, shed off, zone aggregate off), a reconcile must not turn
    # anything back on, neither shed members nor the zone.
    for entity_id in KEPT:
        hass.states.async_set(entity_id, "on", {"brightness": 200})
    for entity_id in SHED:
        hass.states.async_set(entity_id, "off", {})
    hass.states.async_set(ZONE, "off", {})

    service_calls.clear()
    await ctrl.async_reconcile_demand_response()
    await hass.async_block_till_done()

    on, _ = _on_off(service_calls)
    assert on == set()


@pytest.mark.integration
async def test_dr_scene_entity_never_drives_cluster_entity(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    # Same bypass through the HA scene entity path (scene.py _apply_stored).
    await _setup(hass)
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.zone_room_bright"}, blocking=True
    )
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert ZONE not in on
    assert on == KEPT
    assert off >= SHED


@pytest.mark.integration
async def test_cluster_batching_unchanged_without_dr(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    # Non-DR path stays as-is: the dispatcher coalesces the six identical
    # member targets into a single zone command (plus the snapshot's own
    # zone entry), and no member is turned off.
    await _setup(hass)
    ctrl = hass.data["area_lighting"]["controllers"]["zone_room"]

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert ZONE in on
    assert off == set()
