"""Demand response: the kelvin router never lights a shed route bulb."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.circadian_clock import CircadianClock
from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _light_calls(service_calls, service: str) -> set[str]:
    return {
        c.data.get("entity_id")
        for c in service_calls
        if c.domain == "light" and c.service == service
    }


async def _assert_tick_sends_no_route_command(service_calls, pin_circadian, kelvin: int) -> None:
    """A tick at a kelvin that would swap routes sends no light command."""
    service_calls.clear()
    await pin_circadian(50, kelvin)
    assert [c for c in service_calls if c.domain == "light"] == []


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


async def _setup(
    hass: HomeAssistant, pin_circadian, colortemp: int, cfg: dict | None = None
) -> None:
    for eid in (
        "light.kitchen_fluorescent",
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    ):
        hass.states.async_set(eid, "off", {})
    await pin_circadian(50, colortemp, tick=False)
    assert await async_setup_component(hass, "area_lighting", cfg or _kitchen_routes_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_router_sheds_route_bulbs_on_live_flag_flip(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """A live demand-response flip must shed route bulbs even when the route index is unchanged."""
    await _setup(hass, pin_circadian, colortemp=3000)  # fallback route (all 3 strips) active
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    # Mocked light services do not update hass.states: simulate the strips
    # being physically on after the circadian bring-up.
    for eid in ("light.kitchen_strip_1", "light.kitchen_strip_2", "light.kitchen_strip_3"):
        hass.states.async_set(eid, "on", {})
    hass.states.async_set("light.kitchen_fluorescent", "off", {})
    await hass.async_block_till_done()

    service_calls.clear()
    await _toggles(hass).async_set_demand_response_active(True)
    await hass.async_block_till_done()

    # The active on-set is the 3 fallback strips (n=3 -> keep 2): only the
    # config-order tail strip is shed on the flip.
    off = {
        c.data.get("entity_id")
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    on = {
        c.data.get("entity_id")
        for c in service_calls
        if c.domain == "light" and c.service == "turn_on"
    }
    assert "light.kitchen_strip_3" in off
    assert off.isdisjoint({"light.kitchen_strip_1", "light.kitchen_strip_2"})
    assert on.isdisjoint({"light.kitchen_strip_3"})
    assert ctrl.dr_shed_ids == frozenset({"light.kitchen_strip_3"})


@pytest.mark.integration
async def test_router_never_lights_shed_route_bulbs(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await _setup(hass, pin_circadian, colortemp=3000)  # fallback route (all 3 strips) active
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    # Shedding is sized over the ACTIVE route's lights (the 3 strips), not
    # every route light: n=3 -> keep 2 -> shed only the tail strip.
    assert ctrl.dr_shed_ids == frozenset({"light.kitchen_strip_3"})
    on = {
        c.data.get("entity_id")
        for c in service_calls
        if c.domain == "light" and c.service == "turn_on"
    }
    assert {"light.kitchen_strip_1", "light.kitchen_strip_2"} <= on
    assert "light.kitchen_strip_3" not in on


@pytest.mark.integration
async def test_route_change_recomputes_shed_set(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """A route change re-sizes the shed set over the NEW active route."""
    await _setup(hass, pin_circadian, colortemp=3000)  # fallback route (all 3 strips) active
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset({"light.kitchen_strip_3"})

    # Mocked light services do not update hass.states: simulate the routed
    # bring-up result (kept strips on, shed strip and fluorescent off).
    hass.states.async_set("light.kitchen_strip_1", "on", {})
    hass.states.async_set("light.kitchen_strip_2", "on", {})
    hass.states.async_set("light.kitchen_strip_3", "off", {})
    hass.states.async_set("light.kitchen_fluorescent", "off", {})
    await hass.async_block_till_done()

    service_calls.clear()
    await pin_circadian(50, 5000)

    on = {
        c.data.get("entity_id")
        for c in service_calls
        if c.domain == "light" and c.service == "turn_on"
    }
    off = {
        c.data.get("entity_id")
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    assert "light.kitchen_fluorescent" in on
    assert {"light.kitchen_strip_1", "light.kitchen_strip_2"} <= off
    # New active on-set is the single fluorescent: n=1 -> keep 1 -> shed none.
    assert ctrl.dr_shed_ids == frozenset()


@pytest.mark.integration
async def test_external_circadian_recomputes_shed_set(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """External scene.turn_on of circadian under DR refreshes the shed set."""
    await _setup(hass, pin_circadian, colortemp=3000)  # fallback route (all 3 strips) active
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl.handle_scene_activated("circadian")
    await hass.async_block_till_done()

    assert ctrl.dr_shed_ids == frozenset({"light.kitchen_strip_3"})
    on = {
        c.data.get("entity_id")
        for c in service_calls
        if c.domain == "light" and c.service == "turn_on"
    }
    assert "light.kitchen_strip_3" not in on


def _loft_mixed_config() -> dict:
    """MIXED area: two banded route lights + one fallback route light + two
    NON-route circadian-switch lamps. Config order: tracks, accent, lamps."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "loft",
                    "name": "Loft",
                    "event_handlers": True,
                    "circadian_switches": [
                        {"name": "Loft", "max_brightness": 100, "min_brightness": 20},
                    ],
                    "lights": [
                        {
                            "id": "light.loft_track_1",
                            "circadian_switch": "Loft",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.loft_track_2",
                            "circadian_switch": "Loft",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.loft_accent",
                            "circadian_switch": "Loft",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.loft_lamp_1",
                            "circadian_switch": "Loft",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.loft_lamp_2",
                            "circadian_switch": "Loft",
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
                            {
                                "kelvin_range": [4500, 5500],
                                "lights": ["light.loft_track_1", "light.loft_track_2"],
                            },
                            {"lights": ["light.loft_accent"]},
                        ],
                    },
                }
            ]
        }
    }


async def _setup_loft(hass: HomeAssistant, pin_circadian, colortemp: int) -> None:
    for eid in (
        "light.loft_track_1",
        "light.loft_track_2",
        "light.loft_accent",
        "light.loft_lamp_1",
        "light.loft_lamp_2",
    ):
        hass.states.async_set(eid, "off", {})
    await pin_circadian(50, colortemp, tick=False)
    assert await async_setup_component(hass, "area_lighting", _loft_mixed_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_mixed_area_unchanged_reconcile_is_noop(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """A reconcile whose recomputed shed set equals the old one must issue NO
    service calls, and must NOT relight a kept non-route lamp that is off."""
    await _setup_loft(hass, pin_circadian, colortemp=3000)  # fallback route (accent) active
    ctrl = hass.data["area_lighting"]["controllers"]["loft"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    # On-set = [accent, lamp_1, lamp_2]: n=3 -> keep 2 -> shed the tail lamp.
    assert ctrl.dr_shed_ids == frozenset({"light.loft_lamp_2"})

    # Mocked services do not update hass.states: every light, including the
    # KEPT lamp_1, is still physically off. A flip-only reconcile must leave
    # it alone; only an activation may bring it up.
    service_calls.clear()
    await pin_circadian(50, 3000)

    assert ctrl.dr_shed_ids == frozenset({"light.loft_lamp_2"})
    assert [c for c in service_calls if c.domain == "light"] == []


@pytest.mark.integration
async def test_mixed_area_route_change_redrives_only_flipped_lamps(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """A route change that grows the shed set re-drives ONLY the non-route
    lamp whose shed status flipped; the still-shed lamp is untouched."""
    await _setup_loft(hass, pin_circadian, colortemp=3000)  # fallback route (accent) active
    ctrl = hass.data["area_lighting"]["controllers"]["loft"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset({"light.loft_lamp_2"})

    # Simulate the physical result of the bring-up: kept lights on.
    hass.states.async_set("light.loft_accent", "on", {})
    hass.states.async_set("light.loft_lamp_1", "on", {})
    await hass.async_block_till_done()

    service_calls.clear()
    await pin_circadian(50, 5000)

    # New on-set = [track_1, track_2, lamp_1, lamp_2]: n=4 -> keep 2 -> shed
    # both lamps. Only lamp_1 flipped (kept -> shed): it is turned off. The
    # router swaps accent for the tracks; lamp_2 (unchanged) is untouched.
    assert ctrl.dr_shed_ids == frozenset({"light.loft_lamp_1", "light.loft_lamp_2"})
    assert _light_calls(service_calls, "turn_off") == {"light.loft_accent", "light.loft_lamp_1"}
    assert _light_calls(service_calls, "turn_on") == {"light.loft_track_1", "light.loft_track_2"}


def _kitchen_visual_config() -> dict:
    cfg = _kitchen_routes_config()
    cfg["area_lighting"]["areas"][0]["scenes"].append(
        {
            "id": "bright",
            "name": "Bright",
            "entities": {"light.kitchen_fluorescent": {"state": "on", "brightness": 200}},
        }
    )
    return cfg


@pytest.mark.integration
async def test_visual_scene_transition_deregisters_router(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """Leaving circadian for a visual scene deactivates the router, so a
    later tick cannot re-drive route lights against the incoming scene."""
    await _setup(hass, pin_circadian, colortemp=5000, cfg=_kitchen_visual_config())
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl._kelvin_router._active

    # Physical result of the bring-up: banded route (fluorescent) on.
    hass.states.async_set("light.kitchen_fluorescent", "on", {})
    await hass.async_block_till_done()

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    assert ctrl._kelvin_router.current_index is None
    # Nothing circadian fights the visual scene: the strips are never lit
    # and the scene's fluorescent is never turned off.
    on = _light_calls(service_calls, "turn_on")
    off = _light_calls(service_calls, "turn_off")
    assert on.isdisjoint(
        {"light.kitchen_strip_1", "light.kitchen_strip_2", "light.kitchen_strip_3"}
    )
    assert "light.kitchen_fluorescent" not in off
    await _assert_tick_sends_no_route_command(service_calls, pin_circadian, 3000)


@pytest.mark.integration
async def test_off_fade_from_circadian_deregisters_router(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """lighting_off_fade from circadian deactivates the router, so a later
    tick cannot re-drive route lights against the outgoing fade."""
    await _setup(hass, pin_circadian, colortemp=5000)  # banded route (fluorescent) active
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl._kelvin_router._active

    # Physical result of the bring-up: banded route (fluorescent) on.
    hass.states.async_set("light.kitchen_fluorescent", "on", {})
    await hass.async_block_till_done()

    service_calls.clear()
    await ctrl.lighting_off_fade(ActivationSource.MOTION)
    await hass.async_block_till_done()

    assert ctrl._kelvin_router.current_index is None
    # An off fade must never turn a light ON.
    assert _light_calls(service_calls, "turn_on") == set()
    await _assert_tick_sends_no_route_command(service_calls, pin_circadian, 3000)


@pytest.mark.integration
async def test_circadian_dim_deregisters_router(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """Dimming inside circadian deactivates the router, so a later tick
    cannot swap routes against the user's dim. The router comes back on
    the next circadian sync (e.g. the un-dim restore)."""
    await _setup(hass, pin_circadian, colortemp=5000)  # banded route (fluorescent) active
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl._kelvin_router._active

    hass.states.async_set("light.kitchen_fluorescent", "on", {})
    await hass.async_block_till_done()

    service_calls.clear()
    await ctrl.lighting_lower()
    await hass.async_block_till_done()

    assert ctrl._kelvin_router.current_index is None
    assert ctrl._state.is_circadian
    assert ctrl._state.dimmed
    # No fallback-route swap fights the dim: the strips are never lit and
    # the active fluorescent is never turned off.
    on = _light_calls(service_calls, "turn_on")
    assert on.isdisjoint(
        {"light.kitchen_strip_1", "light.kitchen_strip_2", "light.kitchen_strip_3"}
    )
    assert "light.kitchen_fluorescent" not in _light_calls(service_calls, "turn_off")
    await _assert_tick_sends_no_route_command(service_calls, pin_circadian, 3000)


@pytest.mark.integration
async def test_external_off_shed_clear_sticks(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """External 'off' under DR clears dr_shed_ids, and no later tick
    repopulates it."""
    await _setup(hass, pin_circadian, colortemp=3000)  # fallback route (strips) active
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset({"light.kitchen_strip_3"})

    service_calls.clear()
    await ctrl.handle_scene_activated("off")
    await hass.async_block_till_done()

    assert ctrl.dr_shed_ids == frozenset()
    assert ctrl._kelvin_router.current_index is None
    assert _light_calls(service_calls, "turn_on") == set()
    await _assert_tick_sends_no_route_command(service_calls, pin_circadian, 5000)
    assert ctrl.dr_shed_ids == frozenset()


def _den_config() -> dict:
    """Multi-bulb BANDED route (3 spots) + single-bulb fallback, so the
    hysteresis band around the banded route's edges is where controller and
    router route selection can disagree."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "den",
                    "name": "Den",
                    "event_handlers": True,
                    "circadian_switches": [
                        {"name": "Den", "max_brightness": 100, "min_brightness": 20},
                    ],
                    "lights": [
                        {
                            "id": f"light.den_spot_{i}",
                            "circadian_switch": "Den",
                            "circadian_type": "ct",
                        }
                        for i in (1, 2, 3)
                    ]
                    + [
                        {
                            "id": "light.den_lamp",
                            "circadian_switch": "Den",
                            "circadian_type": "ct",
                        }
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                    "circadian_kelvin_routes": {
                        "crossfade_seconds": 1.0,
                        "routes": [
                            {
                                "kelvin_range": [4500, 5500],
                                "lights": [
                                    "light.den_spot_1",
                                    "light.den_spot_2",
                                    "light.den_spot_3",
                                ],
                            },
                            {"lights": ["light.den_lamp"]},
                        ],
                    },
                }
            ]
        }
    }


async def _setup_den(hass: HomeAssistant, pin_circadian, colortemp: int) -> None:
    for eid in (
        "light.den_spot_1",
        "light.den_spot_2",
        "light.den_spot_3",
        "light.den_lamp",
    ):
        hass.states.async_set(eid, "off", {})
    await pin_circadian(50, colortemp, tick=False)
    assert await async_setup_component(hass, "area_lighting", _den_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_hysteresis_band_shed_sizing_matches_router(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """Inside the hysteresis band the router keeps the banded route active.
    The controller must size the shed set over that SAME route (hysteresis-
    consistent), not the strict-selection fallback, so the multi-bulb active
    route is never fully relit."""
    await _setup_den(hass, pin_circadian, colortemp=5000)  # banded route (3 spots) active
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    # Active route on-set = 3 spots: n=3 -> keep 2 -> shed the tail spot.
    assert ctrl.dr_shed_ids == frozenset({"light.den_spot_3"})

    # Physical result of the bring-up: kept spots on, shed spot and lamp off.
    hass.states.async_set("light.den_spot_1", "on", {})
    hass.states.async_set("light.den_spot_2", "on", {})
    hass.states.async_set("light.den_spot_3", "off", {})
    hass.states.async_set("light.den_lamp", "off", {})
    await hass.async_block_till_done()

    # 5510 is outside the strict [4500, 5500] band but inside the +25K
    # hysteresis grace: the router keeps the banded route active.
    service_calls.clear()
    await pin_circadian(50, 5510)

    assert ctrl._kelvin_router.current_index == 0
    assert ctrl.dr_shed_ids == frozenset({"light.den_spot_3"})
    # Route and shed set are both unchanged: the reconcile sends nothing.
    # In particular the shed spot must NOT be relit.
    assert _light_calls(service_calls, "turn_off") == set()
    assert _light_calls(service_calls, "turn_on") <= {"light.den_spot_1", "light.den_spot_2"}


@pytest.mark.integration
async def test_deactivate_during_controller_await_drops_route_dispatch(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """A deactivate() interleaved with the reconcile's controller await must
    drop the route dispatch: the area is leaving circadian and route
    commands would fight the incoming scene."""
    import asyncio

    await _setup(hass, pin_circadian, colortemp=5000)  # banded route (fluorescent) active
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    hass.states.async_set("light.kitchen_fluorescent", "on", {})
    await hass.async_block_till_done()

    entered = asyncio.Event()
    gate = asyncio.Event()

    async def _gated_recompute() -> None:
        entered.set()
        await gate.wait()

    ctrl.recompute_and_apply_circadian_dr = _gated_recompute

    # A tick at a kelvin that would swap routes (banded -> fallback).
    service_calls.clear()
    await pin_circadian(50, 3000, tick=False)
    hass.async_create_task(hass.data[DOMAIN]["circadian_clock"].async_tick())
    await asyncio.wait_for(entered.wait(), timeout=1)

    # The reconcile is parked on the controller await: deactivate now.
    ctrl._kelvin_router.deactivate()
    gate.set()
    await hass.async_block_till_done()

    assert [c for c in service_calls if c.domain == "light"] == []


_MEDIA_ZONE = "light.media_zone"
_MEDIA_MEMBERS = ["light.media_1", "light.media_2", "light.media_3"]


def _media_cluster_route_config() -> dict:
    """A banded route whose sole `lights` entry is a CLUSTER entity (allowed
    by the validator), plus a single-bulb fallback."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "media",
                    "name": "Media",
                    "event_handlers": True,
                    "lights": [{"id": eid, "roles": ["dimming"]} for eid in _MEDIA_MEMBERS]
                    + [{"id": "light.media_lamp", "roles": ["dimming"]}],
                    "light_clusters": [{"id": _MEDIA_ZONE, "members": list(_MEDIA_MEMBERS)}],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                    "circadian_kelvin_routes": {
                        "crossfade_seconds": 1.0,
                        "routes": [
                            {"kelvin_range": [4500, 5500], "lights": [_MEDIA_ZONE]},
                            {"lights": ["light.media_lamp"]},
                        ],
                    },
                }
            ]
        }
    }


async def _setup_media(hass: HomeAssistant, pin_circadian, colortemp: int) -> None:
    for eid in [*_MEDIA_MEMBERS, _MEDIA_ZONE, "light.media_lamp"]:
        hass.states.async_set(eid, "off", {})
    await pin_circadian(50, colortemp, tick=False)
    assert await async_setup_component(hass, "area_lighting", _media_cluster_route_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_cluster_route_expands_to_members_under_dr(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """Under DR a cluster route entity is driven as its individual members
    (kept on, shed off); the cluster entity itself is never commanded."""
    await _setup_media(hass, pin_circadian, colortemp=5000)  # banded route (zone) active
    ctrl = hass.data["area_lighting"]["controllers"]["media"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    # Expanded active route on-set = 3 members: n=3 -> keep 2 -> shed tail.
    assert ctrl.dr_shed_ids == frozenset({"light.media_3"})
    on = _light_calls(service_calls, "turn_on")
    assert on == {"light.media_1", "light.media_2"}
    assert _MEDIA_ZONE not in _light_calls(service_calls, "turn_off")

    # Physical result: kept members on, everything else off.
    hass.states.async_set("light.media_1", "on", {})
    hass.states.async_set("light.media_2", "on", {})
    await hass.async_block_till_done()

    # Route change to the fallback lamp: the members are turned off
    # individually; the cluster entity is still never commanded.
    service_calls.clear()
    await pin_circadian(50, 3000)

    assert ctrl.dr_shed_ids == frozenset()
    assert _light_calls(service_calls, "turn_on") == {"light.media_lamp"}
    assert _light_calls(service_calls, "turn_off") == {"light.media_1", "light.media_2"}


@pytest.mark.integration
async def test_cluster_route_stable_reconcile_converges_member_drift(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """A stable reconcile (same route index, same shed set) must still
    converge cluster-route MEMBER physical state under DR: a member left in
    the wrong state (e.g. relit through the aggregate zone) is corrected,
    and a shed member that is off is never relit."""
    await _setup_media(hass, pin_circadian, colortemp=5000)  # banded route (zone) active
    ctrl = hass.data["area_lighting"]["controllers"]["media"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset({"light.media_3"})

    # Physical result of the bring-up: kept members on, shed member off.
    hass.states.async_set("light.media_1", "on", {})
    hass.states.async_set("light.media_2", "on", {})
    hass.states.async_set("light.media_3", "off", {})
    await hass.async_block_till_done()

    # Consistent stable reconcile first: no calls at all, and in particular
    # the shed member is NOT relit.
    service_calls.clear()
    await pin_circadian(50, 5000)
    assert [c for c in service_calls if c.domain == "light"] == []

    # Drift while route and shed set stay stable: the SHED member is relit
    # outside the router (through the zone aggregate) and a KEPT member
    # drops off.
    hass.states.async_set("light.media_3", "on", {})
    hass.states.async_set("light.media_2", "off", {})
    await hass.async_block_till_done()

    service_calls.clear()
    await pin_circadian(50, 5000)

    # Same route, same shed set, but the members are converged: the shed
    # member goes back off, the kept member comes back on, and neither the
    # cluster entity nor the non-member fallback lamp is ever commanded.
    assert _light_calls(service_calls, "turn_off") == {"light.media_3"}
    assert _light_calls(service_calls, "turn_on") == {"light.media_2"}


@pytest.mark.integration
async def test_cluster_route_dr_clear_restores_shed_member(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """Clearing demand response must restore a previously shed cluster-route
    member. The aggregate zone still reads "on" from its kept members, so
    aggregate-level diffing alone would skip the turn_on and leave the shed
    member physically off until the next route change."""
    await _setup_media(hass, pin_circadian, colortemp=5000)  # banded route (zone) active
    ctrl = hass.data["area_lighting"]["controllers"]["media"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset({"light.media_3"})

    # Physical result of the DR bring-up: kept members on, shed member off,
    # and the aggregate zone reporting "on" from its kept members.
    hass.states.async_set("light.media_1", "on", {})
    hass.states.async_set("light.media_2", "on", {})
    hass.states.async_set("light.media_3", "off", {})
    hass.states.async_set(_MEDIA_ZONE, "on", {})
    await hass.async_block_till_done()

    service_calls.clear()
    await _toggles(hass).async_set_demand_response_active(False)
    await hass.async_block_till_done()

    assert ctrl.dr_shed_ids == frozenset()
    on = _light_calls(service_calls, "turn_on")
    # The previously shed member is restored...
    assert "light.media_3" in on
    # ...without re-commanding the already-on kept members or the aggregate.
    assert on.isdisjoint({"light.media_1", "light.media_2", _MEDIA_ZONE})
    # The restore turns nothing off.
    assert _light_calls(service_calls, "turn_off") == set()


@pytest.mark.integration
async def test_cluster_route_dr_clear_leaves_manually_off_kept_member(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """The DR-clear one-shot member restore relights ONLY previously shed
    members. A KEPT member the user manually turned off during demand
    response was never shed, so clearing the flag must leave it off instead
    of force-relighting every off member of the active route."""
    await _setup_media(hass, pin_circadian, colortemp=5000)  # banded route (zone) active
    ctrl = hass.data["area_lighting"]["controllers"]["media"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset({"light.media_3"})

    # Physical result of the DR bring-up, then the user manually turns the
    # KEPT member media_2 off; the shed member media_3 is off as shed.
    hass.states.async_set("light.media_1", "on", {})
    hass.states.async_set("light.media_2", "off", {})
    hass.states.async_set("light.media_3", "off", {})
    hass.states.async_set(_MEDIA_ZONE, "on", {})
    await hass.async_block_till_done()

    service_calls.clear()
    await _toggles(hass).async_set_demand_response_active(False)
    await hass.async_block_till_done()

    assert ctrl.dr_shed_ids == frozenset()
    on = _light_calls(service_calls, "turn_on")
    # The previously shed member is restored...
    assert "light.media_3" in on
    # ...but the manually-off KEPT member is left alone, as are the already-on
    # kept member and the aggregate zone.
    assert on.isdisjoint({"light.media_1", "light.media_2", _MEDIA_ZONE})
    assert _light_calls(service_calls, "turn_off") == set()


def _unrouted_cluster_config() -> dict:
    """A cluster that is NOT used in any route, plus an individual light
    (a member of that cluster) routed directly. The cluster must not drag
    its members into the stable-path convergence."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "media",
                    "name": "Media",
                    "event_handlers": True,
                    "lights": [{"id": eid, "roles": ["dimming"]} for eid in _MEDIA_MEMBERS]
                    + [{"id": "light.media_lamp", "roles": ["dimming"]}],
                    "light_clusters": [{"id": _MEDIA_ZONE, "members": list(_MEDIA_MEMBERS)}],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                    "circadian_kelvin_routes": {
                        "crossfade_seconds": 1.0,
                        "routes": [
                            {"kelvin_range": [4500, 5500], "lights": ["light.media_1"]},
                            {"lights": ["light.media_lamp"]},
                        ],
                    },
                }
            ]
        }
    }


@pytest.mark.integration
async def test_unrouted_cluster_does_not_redrive_routed_member_on_stable(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """A cluster absent from every route must not pull its members into the
    stable-path convergence: a manual change to a directly-routed light that
    happens to be a member of that unrelated cluster survives stable
    reconciles (same route index, same shed set) under DR."""
    for eid in [*_MEDIA_MEMBERS, _MEDIA_ZONE, "light.media_lamp"]:
        hass.states.async_set(eid, "off", {})
    await pin_circadian(50, 5000, tick=False)
    assert await async_setup_component(hass, "area_lighting", _unrouted_cluster_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    ctrl = hass.data["area_lighting"]["controllers"]["media"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset()  # single-light on-set keeps itself

    # Physical result of the bring-up, plus one unrouted cluster member lit
    # outside the router so the area never reads all-off during the drift.
    hass.states.async_set("light.media_1", "on", {})
    hass.states.async_set("light.media_2", "on", {})
    await hass.async_block_till_done()

    # Consistent stable reconcile: silent.
    service_calls.clear()
    await pin_circadian(50, 5000)
    assert [c for c in service_calls if c.domain == "light"] == []

    # The routed member is manually turned off while route and shed set
    # stay stable.
    hass.states.async_set("light.media_1", "off", {})
    await hass.async_block_till_done()
    assert ctrl._state.is_circadian  # drift did not knock the area out

    service_calls.clear()
    await pin_circadian(50, 5000)

    # The zone cluster is not a route light, so the stable path leaves the
    # directly-routed member (and everything else) alone.
    assert [c for c in service_calls if c.domain == "light"] == []


@pytest.mark.integration
async def test_cluster_route_batches_without_dr(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """Without DR the cluster entity stays the route target (batching)."""
    await _setup_media(hass, pin_circadian, colortemp=5000)  # banded route (zone) active
    ctrl = hass.data["area_lighting"]["controllers"]["media"]

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    on = _light_calls(service_calls, "turn_on")
    assert on == {_MEDIA_ZONE}


def _study_config() -> dict:
    """Explicit source, single-bulb BANDED route + MULTI-bulb (3 lamp)
    fallback route: the shape where a controller/router divergence on the
    active route leaves the fallback lamps fully unshed under DR."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "study",
                    "name": "Study",
                    "event_handlers": True,
                    "lights": [{"id": "light.study_spot", "roles": ["dimming"]}]
                    + [{"id": f"light.study_lamp_{i}", "roles": ["dimming"]} for i in (1, 2, 3)],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                    "circadian_kelvin_routes": {
                        "crossfade_seconds": 1.0,
                        "routes": [
                            {"kelvin_range": [4500, 5500], "lights": ["light.study_spot"]},
                            {
                                "lights": [
                                    "light.study_lamp_1",
                                    "light.study_lamp_2",
                                    "light.study_lamp_3",
                                ]
                            },
                        ],
                    },
                }
            ]
        }
    }


@pytest.mark.integration
async def test_missing_clock_values_controller_agrees_with_router_fallback(
    hass: HomeAssistant, helper_entities, service_calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no clock values the router selects the FALLBACK route, and the
    controller must size the shed over that same route. Sizing it over the
    banded single-spot route would leave the multi-bulb fallback fully
    unshed."""
    for eid in (
        "light.study_spot",
        "light.study_lamp_1",
        "light.study_lamp_2",
        "light.study_lamp_3",
    ):
        hass.states.async_set(eid, "off", {})
    monkeypatch.setattr(CircadianClock, "values", lambda _self, _now=None: None)
    assert await async_setup_component(hass, "area_lighting", _study_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    assert ctrl._kelvin_router.current_index == 1
    # 3 lamps -> keep 2, shed the config-order tail lamp.
    assert ctrl.dr_shed_ids == frozenset({"light.study_lamp_3"})
    on = _light_calls(service_calls, "turn_on")
    assert {"light.study_lamp_1", "light.study_lamp_2"} <= on
    assert "light.study_lamp_3" not in on


@pytest.mark.integration
async def test_dim_from_dark_disables_circadian_and_unarms_router(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """Dimming a fully-dark circadian area must suspend circadian.

    _adjust_brightness stops the circadian adjuster before stepping, but
    it returns early into _bring_dark_area_to_min when nothing is on, and
    that path never did. The area was left nominally circadian with the
    router still armed, so the router's next reconcile drove route lights
    with no user action behind it, and circadian re-pushed full brightness
    over the dim the user had just asked for.
    """
    await _setup(hass, pin_circadian, colortemp=5000)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    await ctrl._activate_circadian(ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._kelvin_router._active, "precondition: router armed"

    # Fully dark: every light off, so _adjust_brightness takes the
    # _bring_dark_area_to_min branch.
    for eid in (
        "light.kitchen_fluorescent",
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    ):
        hass.states.async_set(eid, "off", {})
    await hass.async_block_till_done()

    service_calls.clear()
    await ctrl._adjust_brightness(-1)
    await hass.async_block_till_done()

    assert ctrl._state.dimmed is True
    assert ctrl._kelvin_router.current_index is None
    await _assert_tick_sends_no_route_command(service_calls, pin_circadian, 3000)


@pytest.mark.integration
async def test_dim_from_dark_does_not_leave_circadian_armed(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """Dimming a dark area back into circadian must suspend circadian.

    _adjust_brightness stops the circadian adjuster before stepping, but
    returns early into _bring_dark_area_to_min when nothing is on, and that
    path did not. It restores scene context first, so when the remembered
    scene IS circadian it re-arms the kelvin router and then dims underneath
    it: the area ends up dimmed with circadian live. The router's next
    reconcile then drives route lights with no user action behind it, and
    circadian re-pushes brightness over the dim just asked for.

    Reachable by dimming a circadian area (which records previous_scene =
    circadian), letting the lights time out, then dimming again.
    """
    await _setup(hass, pin_circadian, colortemp=5000)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    await ctrl._activate_circadian(ActivationSource.USER)
    await hass.async_block_till_done()

    for eid in (
        "light.kitchen_fluorescent",
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    ):
        hass.states.async_set(eid, "off", {})
    await hass.async_block_till_done()
    ctrl._state.previous_scene = "circadian"

    await ctrl._adjust_brightness(-1)
    await hass.async_block_till_done()

    assert ctrl._state.dimmed is True
    assert ctrl._kelvin_router.current_index is None
    await _assert_tick_sends_no_route_command(service_calls, pin_circadian, 3000)
