"""An area persisted in circadian resumes its adjuster when Home Assistant starts."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.state_storage import GLOBAL_STATE_KEY, STORAGE_KEY

NETWORK_LIGHTS = ("light.network_room_overhead_1", "light.network_room_overhead_2")
TICK_PAYLOAD = {"brightness": 255, "color_temp_kelvin": 4000, "transition": 60}


def _persist(hass_storage: dict[str, Any], data: dict[str, Any]) -> None:
    hass_storage[STORAGE_KEY] = {"version": 1, "minor_version": 1, "key": STORAGE_KEY, "data": data}


def _area_state(state: str, scene_slug: str, *, dimmed: bool = False) -> dict[str, Any]:
    return {
        "area_state": {
            "state": state,
            "scene_slug": scene_slug,
            "source": "user",
            "dimmed": dimmed,
            "previous_scene": None,
        }
    }


async def _start(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, DOMAIN, cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _turn_ons(service_calls) -> dict[str, dict]:
    """entity_id -> data of the light.turn_on calls, without entity_id."""
    return {
        c.data["entity_id"]: {k: v for k, v in c.data.items() if k != "entity_id"}
        for c in service_calls
        if c.domain == "light" and c.service == "turn_on"
    }


def _set_on(hass: HomeAssistant, *entity_ids: str) -> None:
    for entity_id in entity_ids:
        hass.states.async_set(entity_id, "on", {"brightness": 100})


def _area(area_id: str, light_ids: list[str], **extra) -> dict:
    return {
        DOMAIN: {
            "areas": [
                {
                    "id": area_id,
                    "name": area_id.replace("_", " ").title(),
                    "event_handlers": True,
                    "circadian_groups": [{"name": "Main"}],
                    "lights": [
                        {"id": light_id, "circadian_group": "Main", "circadian_type": "ct"}
                        for light_id in light_ids
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                    **extra,
                }
            ]
        }
    }


@pytest.mark.integration
async def test_started_resumes_persisted_circadian_area(
    hass: HomeAssistant,
    hass_storage,
    helper_entities,
    network_room_config,
    service_calls,
    pin_circadian,
) -> None:
    _persist(hass_storage, {"network_room": _area_state("circadian", "circadian")})
    _set_on(hass, *NETWORK_LIGHTS)
    await pin_circadian(50, 4000, tick=False)

    await _start(hass, network_room_config)

    ctrl = hass.data[DOMAIN]["controllers"]["network_room"]
    assert ctrl.circadian_running
    assert _turn_ons(service_calls) == dict.fromkeys(NETWORK_LIGHTS, TICK_PAYLOAD)


@pytest.mark.integration
async def test_resume_updates_only_lights_that_are_on(
    hass: HomeAssistant,
    hass_storage,
    helper_entities,
    network_room_config,
    service_calls,
    pin_circadian,
) -> None:
    _persist(hass_storage, {"network_room": _area_state("circadian", "circadian")})
    _set_on(hass, "light.network_room_overhead_1")
    await pin_circadian(50, 4000, tick=False)

    await _start(hass, network_room_config)

    assert [c.service for c in service_calls if c.domain == "light"] == ["turn_on"]
    assert _turn_ons(service_calls) == {"light.network_room_overhead_1": TICK_PAYLOAD}


@pytest.mark.integration
async def test_resume_skips_dimmed_circadian_area(
    hass: HomeAssistant,
    hass_storage,
    helper_entities,
    network_room_config,
    service_calls,
    pin_circadian,
) -> None:
    _persist(hass_storage, {"network_room": _area_state("circadian", "circadian", dimmed=True)})
    _set_on(hass, *NETWORK_LIGHTS)
    await pin_circadian(50, 4000, tick=False)

    await _start(hass, network_room_config)

    assert not hass.data[DOMAIN]["controllers"]["network_room"].circadian_running
    assert _turn_ons(service_calls) == {}


@pytest.mark.integration
@pytest.mark.parametrize(
    "persisted",
    [_area_state("manual", "manual"), _area_state("scene", "evening")],
    ids=["manual", "scene"],
)
async def test_resume_skips_manual_and_scene_areas(
    hass: HomeAssistant,
    hass_storage,
    helper_entities,
    network_room_config,
    service_calls,
    pin_circadian,
    persisted: dict[str, Any],
) -> None:
    _persist(hass_storage, {"network_room": persisted})
    _set_on(hass, *NETWORK_LIGHTS)
    await pin_circadian(50, 4000, tick=False)

    await _start(hass, network_room_config)

    assert not hass.data[DOMAIN]["controllers"]["network_room"].circadian_running
    assert _turn_ons(service_calls) == {}


TUBE = "light.kitchen_fluorescent"
STRIPS = ("light.kitchen_strip_1", "light.kitchen_strip_2")


def _kitchen(*, tube_grouped: bool) -> dict:
    """Kitchen with a banded fluorescent route and a fallback strips route."""
    cfg = _area(
        "kitchen",
        [TUBE, *STRIPS],
        circadian_kelvin_routes={
            "crossfade_seconds": 1.0,
            "routes": [
                {"kelvin_range": [4500, 5500], "lights": [TUBE]},
                {"lights": list(STRIPS)},
            ],
        },
    )
    if not tube_grouped:
        cfg[DOMAIN]["areas"][0]["lights"][0] = {"id": TUBE}
    return cfg


@pytest.mark.integration
async def test_resume_syncs_kelvin_router(
    hass: HomeAssistant, hass_storage, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set(TUBE, "off", {})
    _set_on(hass, *STRIPS)
    _persist(hass_storage, {"kitchen": _area_state("circadian", "circadian")})
    # 4000 K puts the router on its fallback (strips) route.
    await pin_circadian(50, 4000, tick=False)

    await _start(hass, _kitchen(tube_grouped=True))

    ctrl = hass.data[DOMAIN]["controllers"]["kitchen"]
    assert ctrl._kelvin_router.current_index == 1
    assert _turn_ons(service_calls) == dict.fromkeys(STRIPS, TICK_PAYLOAD)


@pytest.mark.integration
@pytest.mark.parametrize("tube_grouped", [True, False], ids=["grouped", "ungrouped"])
async def test_resume_leaves_off_route_lights_off(
    hass: HomeAssistant,
    hass_storage,
    helper_entities,
    service_calls,
    pin_circadian,
    tube_grouped: bool,
) -> None:
    for entity_id in (TUBE, *STRIPS):
        hass.states.async_set(entity_id, "off", {})
    _persist(hass_storage, {"kitchen": _area_state("circadian", "circadian")})
    await pin_circadian(50, 5000, tick=False)

    await _start(hass, _kitchen(tube_grouped=tube_grouped))

    ctrl = hass.data[DOMAIN]["controllers"]["kitchen"]
    assert ctrl.circadian_running
    assert ctrl._kelvin_router.current_index == 0
    assert [c for c in service_calls if c.domain == "light"] == []


@pytest.mark.integration
async def test_resume_adopts_the_route_that_is_on(
    hass: HomeAssistant, hass_storage, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set(TUBE, "on", {})
    for entity_id in STRIPS:
        hass.states.async_set(entity_id, "off", {})
    _persist(hass_storage, {"kitchen": _area_state("circadian", "circadian")})
    # 5510 K is outside the fluorescent's band but inside its hysteresis. A
    # router with no route would pick the strips, and one that adopts the
    # fluorescent keeps it.
    await pin_circadian(50, 5510, tick=False)

    await _start(hass, _kitchen(tube_grouped=True))

    ctrl = hass.data[DOMAIN]["controllers"]["kitchen"]
    assert ctrl._kelvin_router.current_index == 0
    assert [c.service for c in service_calls if c.domain == "light"] == ["turn_on"]
    assert _turn_ons(service_calls) == {TUBE: {**TICK_PAYLOAD, "color_temp_kelvin": 5510}}


@pytest.mark.integration
async def test_resume_crossfades_when_the_route_changed_while_down(
    hass: HomeAssistant, hass_storage, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set(TUBE, "off", {})
    _set_on(hass, *STRIPS)
    _persist(hass_storage, {"kitchen": _area_state("circadian", "circadian")})
    await pin_circadian(50, 5000, tick=False)

    await _start(hass, _kitchen(tube_grouped=True))

    ctrl = hass.data[DOMAIN]["controllers"]["kitchen"]
    assert ctrl._kelvin_router.current_index == 0
    turn_offs = {
        c.data["entity_id"]: c.data["transition"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    assert turn_offs == dict.fromkeys(STRIPS, 1)
    assert _turn_ons(service_calls) == {
        TUBE: {"brightness": 255, "color_temp_kelvin": 5000, "transition": 1}
    }


@pytest.mark.integration
async def test_resume_recomputes_shed_set_without_routes(
    hass: HomeAssistant, hass_storage, helper_entities, service_calls, pin_circadian
) -> None:
    lights = [f"light.study_{i}" for i in range(1, 7)]
    _set_on(hass, *lights)
    _persist(
        hass_storage,
        {
            "study": _area_state("circadian", "circadian"),
            GLOBAL_STATE_KEY: {"demand_response_active": True},
        },
    )
    await pin_circadian(50, 4000, tick=False)
    await _start(hass, _area("study", lights))
    ctrl = hass.data[DOMAIN]["controllers"]["study"]
    assert set(_turn_ons(service_calls)) == {"light.study_1", "light.study_2"}
    # A shed set gone stale while the adjuster was away, as after a reload.
    ctrl._dr_shed_ids = frozenset()
    service_calls.clear()

    await ctrl.async_resume_circadian()
    await hass.async_block_till_done()

    shed = frozenset(lights[2:])
    assert ctrl.dr_shed_ids == shed
    turn_offs = {c.data["entity_id"] for c in service_calls if c.service == "turn_off"}
    assert turn_offs == shed


MEDIA_ZONE = "light.media_zone"
MEDIA_MEMBERS = ("light.media_1", "light.media_2", "light.media_3")


@pytest.mark.integration
async def test_resume_leaves_off_cluster_route_members_off_under_demand_response(
    hass: HomeAssistant, hass_storage, helper_entities, service_calls, pin_circadian
) -> None:
    cfg = _area(
        "media",
        [*MEDIA_MEMBERS, "light.media_lamp"],
        light_clusters=[{"id": MEDIA_ZONE, "members": list(MEDIA_MEMBERS)}],
        circadian_kelvin_routes={
            "crossfade_seconds": 1.0,
            "routes": [
                {"kelvin_range": [4500, 5500], "lights": [MEDIA_ZONE]},
                {"lights": ["light.media_lamp"]},
            ],
        },
    )
    # The shed member and one kept member are off, and the other kept member is on.
    for entity_id in ("light.media_2", "light.media_3", "light.media_lamp"):
        hass.states.async_set(entity_id, "off", {})
    _set_on(hass, "light.media_1", MEDIA_ZONE)
    _persist(
        hass_storage,
        {
            "media": _area_state("circadian", "circadian"),
            GLOBAL_STATE_KEY: {"demand_response_active": True},
        },
    )
    await pin_circadian(50, 5000, tick=False)

    await _start(hass, cfg)

    ctrl = hass.data[DOMAIN]["controllers"]["media"]
    assert ctrl.dr_shed_ids == frozenset({"light.media_3"})
    assert ctrl._kelvin_router.current_index == 0
    assert [c.service for c in service_calls if c.domain == "light"] == ["turn_on"]
    assert set(_turn_ons(service_calls)) == {"light.media_1"}

    # Later ticks on the same route and shed set leave it off too.
    ctrl._circadian_commanded_at.clear()
    service_calls.clear()
    await pin_circadian(50, 5000)
    assert set(_turn_ons(service_calls)) == {"light.media_1"}
