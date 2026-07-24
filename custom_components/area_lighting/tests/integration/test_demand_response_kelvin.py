"""Demand response: the kelvin router never lights a shed route bulb."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles

_SWITCH = "switch.circadian_lighting_kitchen_kitchen_circadian"
_LOFT_SWITCH = "switch.circadian_lighting_loft_loft_circadian"


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _light_calls(service_calls, service: str) -> set[str]:
    return {
        c.data.get("entity_id")
        for c in service_calls
        if c.domain == "light" and c.service == service
    }


def _make_switch_off_update_state(hass: HomeAssistant) -> None:
    """Replace the mocked switch.turn_off with one that updates the switch
    entity's state, so the router's source listener fires exactly as it
    would in production when the controller disables circadian switches."""

    @callback
    def _handler(call: ServiceCall) -> None:
        hass.states.async_set(call.data["entity_id"], "off", {})

    hass.services.async_register("switch", "turn_off", _handler)


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


async def _setup(hass: HomeAssistant, colortemp: int, cfg: dict | None = None) -> None:
    for eid in (
        "light.kitchen_fluorescent",
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    ):
        hass.states.async_set(eid, "off", {})
    hass.states.async_set(_SWITCH, "on", {"brightness": 75.0, "colortemp": colortemp})
    assert await async_setup_component(hass, "area_lighting", cfg or _kitchen_routes_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_router_sheds_route_bulbs_on_live_flag_flip(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A live demand-response flip must shed route bulbs even when the route index is unchanged."""
    await _setup(hass, colortemp=3000)  # fallback route (all 3 strips) active
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
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, colortemp=3000)  # fallback route (all 3 strips) active
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
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A route change re-sizes the shed set over the NEW active route."""
    await _setup(hass, colortemp=3000)  # fallback route (all 3 strips) active
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
    hass.states.async_set(_SWITCH, "on", {"brightness": 75.0, "colortemp": 5000})
    await hass.async_block_till_done()

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
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """External scene.turn_on of circadian under DR refreshes the shed set."""
    await _setup(hass, colortemp=3000)  # fallback route (all 3 strips) active
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


async def _setup_loft(hass: HomeAssistant, colortemp: int) -> None:
    for eid in (
        "light.loft_track_1",
        "light.loft_track_2",
        "light.loft_accent",
        "light.loft_lamp_1",
        "light.loft_lamp_2",
    ):
        hass.states.async_set(eid, "off", {})
    hass.states.async_set(_LOFT_SWITCH, "on", {"brightness": 75.0, "colortemp": colortemp})
    assert await async_setup_component(hass, "area_lighting", _loft_mixed_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_mixed_area_unchanged_reconcile_is_noop(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A reconcile whose recomputed shed set equals the old one must issue NO
    service calls, and must NOT relight a kept non-route lamp that is off."""
    await _setup_loft(hass, colortemp=3000)  # fallback route (accent) active
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
    hass.states.async_set(_LOFT_SWITCH, "on", {"brightness": 74.0, "colortemp": 3000})
    await hass.async_block_till_done()

    assert ctrl.dr_shed_ids == frozenset({"light.loft_lamp_2"})
    assert [c for c in service_calls if c.domain == "light"] == []


@pytest.mark.integration
async def test_mixed_area_route_change_redrives_only_flipped_lamps(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A route change that grows the shed set re-drives ONLY the non-route
    lamp whose shed status flipped; the still-shed lamp is untouched."""
    await _setup_loft(hass, colortemp=3000)  # fallback route (accent) active
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
    hass.states.async_set(_LOFT_SWITCH, "on", {"brightness": 75.0, "colortemp": 5000})
    await hass.async_block_till_done()

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
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """Leaving circadian for a visual scene must deregister the router's
    source listener BEFORE the circadian switches are disabled, so the
    switch-off cannot enqueue a stale reconcile that re-drives route lights
    against the incoming scene."""
    await _setup(hass, colortemp=5000, cfg=_kitchen_visual_config())
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl._kelvin_router._unsub is not None

    # Physical result of the bring-up: banded route (fluorescent) on.
    hass.states.async_set("light.kitchen_fluorescent", "on", {})
    await hass.async_block_till_done()

    # From here on, disabling a circadian switch really updates its state
    # (production behavior), which is what fires the router's listener.
    _make_switch_off_update_state(hass)

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    assert ctrl._kelvin_router._unsub is None
    # No stale circadian reconcile fights the visual scene: the strips are
    # never lit and the scene's fluorescent is never turned off.
    on = _light_calls(service_calls, "turn_on")
    off = _light_calls(service_calls, "turn_off")
    assert on.isdisjoint(
        {"light.kitchen_strip_1", "light.kitchen_strip_2", "light.kitchen_strip_3"}
    )
    assert "light.kitchen_fluorescent" not in off


@pytest.mark.integration
async def test_external_off_shed_clear_sticks(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """External 'off' under DR clears dr_shed_ids, and no stale reconcile
    (from the circadian switch turning off) repopulates it afterwards."""
    await _setup(hass, colortemp=3000)  # fallback route (strips) active
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    _toggles(hass)._demand_response_active = True

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset({"light.kitchen_strip_3"})

    _make_switch_off_update_state(hass)

    service_calls.clear()
    await ctrl.handle_scene_activated("off")
    await hass.async_block_till_done()

    assert ctrl.dr_shed_ids == frozenset()
    assert ctrl._kelvin_router._unsub is None
    assert _light_calls(service_calls, "turn_on") == set()
