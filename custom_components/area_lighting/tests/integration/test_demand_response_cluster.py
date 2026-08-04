"""Demand response must not use cluster (Hue Zone) entities as scene targets.

A stored snapshot captures `area.all_lights`, which includes the cluster
entity (e.g. `light.zone_all`) with state `on`. `apply_demand_response`
only sheds individual lights, so without a dedicated filter the zone
survives as an `on` target: applying it turns the whole zone on,
relighting shed members (and any later replay of the targets repeats
that). Under DR, the individual members are the targets; clusters remain
a pure batching optimization, so a cluster whose members are all kept
still coalesces into one zone command.
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


async def _setup(
    hass: HomeAssistant,
    config: dict | None = None,
    extra_entities: tuple[str, ...] = (),
) -> None:
    for entity_id in [*MEMBERS, ZONE, *extra_entities]:
        hass.states.async_set(entity_id, "off", {})
    assert await async_setup_component(hass, "area_lighting", config or _config())
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
    # Tracking must drop the zone key entirely, not retain it as `off`.
    assert ZONE not in ctrl._active_scene_targets

    # Idempotency: with the physical states matching the DR outcome
    # (kept on, shed off, zone aggregate off), re-driving the area through
    # its activation path replays the same filtered commands: shed members
    # and the zone must not be turned on.
    for entity_id in KEPT:
        hass.states.async_set(entity_id, "on", {"brightness": 200})
    for entity_id in SHED:
        hass.states.async_set(entity_id, "off", {})
    hass.states.async_set(ZONE, "off", {})

    service_calls.clear()
    await ctrl.reactivate_for_demand_response()
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert on == KEPT
    assert ZONE not in on
    assert off >= SHED


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
    # zone entry, also addressed to the zone), so the zone is the ONLY
    # turn_on target: no member is driven individually, none turned off.
    await _setup(hass)
    ctrl = hass.data["area_lighting"]["controllers"]["zone_room"]

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert on == {ZONE}
    assert off == set()


SUBCLUSTER = "light.zone_front"
SUBCLUSTER_MEMBERS = ["light.zone_a", "light.zone_b"]


def _config_with_subcluster() -> dict:
    """Base config plus a two-member subcluster over the first-declared
    lights, mirrored into the bright scene's entities like a snapshot
    would capture it."""
    config = _config()
    area = config["area_lighting"]["areas"][0]
    area["light_clusters"].append({"id": SUBCLUSTER, "members": list(SUBCLUSTER_MEMBERS)})
    area["scenes"][1]["entities"][SUBCLUSTER] = {"state": "on"}
    return config


@pytest.mark.integration
async def test_dr_all_kept_subcluster_batches_into_single_zone_command(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    # Six on-bulbs shed 80%, keeping exactly the two first-declared lights,
    # which are precisely the subcluster's membership. The dispatcher must
    # coalesce that fully-kept cohort into ONE turn_on for the subcluster
    # zone entity; neither kept member may be driven individually, and the
    # all-member zone must stay untouched.
    await _setup(hass, _config_with_subcluster(), extra_entities=(SUBCLUSTER,))
    ctrl = hass.data["area_lighting"]["controllers"]["zone_room"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert on == {SUBCLUSTER}
    assert off >= SHED
    assert not off & KEPT


@pytest.mark.integration
async def test_dr_brightness_step_excludes_cluster_entity(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """Stepping brightness under DR must not drive the zone aggregate: a
    cluster command would step shed members back on through the zone."""
    await _setup(hass)
    ctrl = hass.data["area_lighting"]["controllers"]["zone_room"]
    _toggles(hass)._demand_response_active = True

    # Members and the zone aggregate all read "on" (e.g. pre-DR lighting).
    for entity_id in [*MEMBERS, ZONE]:
        hass.states.async_set(entity_id, "on", {"brightness": 200})

    service_calls.clear()
    await ctrl.lighting_raise()
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert ZONE not in on
    assert on == set(MEMBERS)
    assert off == set()


# ── cluster entities must not inflate the shed tier ────────────────────────
#
# Undeclared `on` targets now count toward n and are shed first, so a scene's
# cluster entry must be removed BEFORE the shed is computed. A cluster
# addresses the same physical bulbs as its members; counting it too pushes n
# across the 5/6 tier boundary and over-sheds real bulbs.
#
# Five members is the case that distinguishes the two: 5 on-bulbs is the 50%
# tier (keep 3), while 6 (members + zone) is the 80% tier (keep 2). With six
# members both readings happen to keep 2, which is why this needs its own
# fixture.

FIVE = [f"light.five_{suffix}" for suffix in "abcde"]
FIVE_ZONE = "light.five_all"


def _five_config() -> dict:
    entities: dict = {FIVE_ZONE: {"state": "on"}}
    entities.update({m: {"state": "on", "brightness": 200} for m in FIVE})
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "five_room",
                    "name": "Five Room",
                    "event_handlers": True,
                    "lights": [{"id": m, "roles": ["dimming"]} for m in FIVE],
                    "light_clusters": [{"id": FIVE_ZONE, "members": list(FIVE)}],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "bright", "name": "Bright", "entities": entities},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


@pytest.mark.integration
async def test_cluster_entity_does_not_inflate_shed_tier(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    for entity_id in [*FIVE, FIVE_ZONE]:
        hass.states.async_set(entity_id, "off", {})
    assert await async_setup_component(hass, "area_lighting", _five_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    ctrl = hass.data["area_lighting"]["controllers"]["five_room"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, _off = _on_off(service_calls)
    # 5 real bulbs -> 50% tier -> keep 3. If the zone were counted as a
    # sixth on-bulb it would be the 80% tier and only 2 would survive.
    assert on == {"light.five_a", "light.five_b", "light.five_c"}
    assert FIVE_ZONE not in on
