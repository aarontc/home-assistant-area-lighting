"""A grouped light switched on while the adjuster runs gets the circadian values."""

from __future__ import annotations

import time

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.global_state import GlobalToggles

NETWORK_LIGHTS = ("light.network_room_overhead_1", "light.network_room_overhead_2")
PAYLOAD = {"brightness": 255, "color_temp_kelvin": 4000, "transition": 0.5}


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
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


async def _circadian(hass: HomeAssistant, area_id: str, *, forget_echo: bool = True):
    """Activate circadian, by default letting the activation's echo window lapse."""
    ctrl = hass.data[DOMAIN]["controllers"][area_id]
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    if forget_echo:
        ctrl._circadian_commanded_at.clear()
    return ctrl


async def _switch(hass: HomeAssistant, entity_id: str, state: str, **attrs) -> None:
    hass.states.async_set(entity_id, state, attrs)
    await hass.async_block_till_done()


def _area(area_id: str, lights: list[dict], **extra) -> dict:
    return {
        DOMAIN: {
            "areas": [
                {
                    "id": area_id,
                    "name": area_id.replace("_", " ").title(),
                    "event_handlers": True,
                    "circadian_groups": [
                        {"name": "Main", "max_brightness": 100, "min_brightness": 40},
                    ],
                    "lights": lights,
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                    ],
                    **extra,
                }
            ]
        }
    }


def _grouped(entity_id: str) -> dict:
    return {"id": entity_id, "circadian_group": "Main", "circadian_type": "ct"}


def _stub_off(hass: HomeAssistant, *entity_ids: str) -> None:
    for entity_id in entity_ids:
        hass.states.async_set(entity_id, "off", {})


@pytest.mark.integration
async def test_light_switched_on_externally_gets_circadian_values(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    await _circadian(hass, "network_room")
    service_calls.clear()

    await _switch(hass, "light.network_room_overhead_1", "on", brightness=20)

    assert _turn_ons(service_calls) == {"light.network_room_overhead_1": PAYLOAD}


@pytest.mark.integration
async def test_activation_echo_is_not_commanded_twice(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    await _circadian(hass, "network_room", forget_echo=False)
    service_calls.clear()

    for entity_id in NETWORK_LIGHTS:
        await _switch(hass, entity_id, "on", brightness=255)

    assert _turn_ons(service_calls) == {}


@pytest.mark.integration
async def test_shed_light_switched_on_is_left_to_manual_detection(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    lights = [f"light.study_{i}" for i in range(1, 7)]
    _stub_off(hass, *lights)
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, _area("study", [_grouped(light) for light in lights]))
    toggles: GlobalToggles = hass.data[DOMAIN]["global"]
    toggles._demand_response_active = True
    ctrl = await _circadian(hass, "study")
    assert "light.study_3" in ctrl.dr_shed_ids
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0
    service_calls.clear()

    await _switch(hass, "light.study_3", "on", brightness=150)

    assert "light.study_3" not in _turn_ons(service_calls)
    assert ctrl._state.is_manual


@pytest.mark.integration
async def test_unavailable_to_on_gets_circadian_values(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    await _circadian(hass, "network_room")
    await _switch(hass, "light.network_room_overhead_1", "unavailable")
    service_calls.clear()

    await _switch(hass, "light.network_room_overhead_1", "on", brightness=254)

    assert _turn_ons(service_calls) == {"light.network_room_overhead_1": PAYLOAD}


@pytest.mark.integration
async def test_attribute_change_while_on_is_ignored(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = await _circadian(hass, "network_room")
    await _switch(hass, "light.network_room_overhead_1", "on", brightness=200)
    ctrl._circadian_commanded_at.clear()
    service_calls.clear()

    await _switch(hass, "light.network_room_overhead_1", "on", brightness=100)

    assert _turn_ons(service_calls) == {}


@pytest.mark.integration
async def test_light_without_group_is_ignored(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    _stub_off(hass, "light.den_grouped", "light.den_plain")
    await pin_circadian(50, 4000, tick=False)
    cfg = _area("den", [_grouped("light.den_grouped"), {"id": "light.den_plain"}])
    await _setup(hass, cfg)
    await _circadian(hass, "den")
    service_calls.clear()

    await _switch(hass, "light.den_plain", "on", brightness=100)

    assert _turn_ons(service_calls) == {}


@pytest.mark.integration
async def test_events_after_adjuster_stops_are_ignored(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = await _circadian(hass, "network_room")
    ctrl.current_scene = "daylight"
    assert not ctrl.circadian_running
    service_calls.clear()

    await _switch(hass, "light.network_room_overhead_1", "on", brightness=100)

    assert _turn_ons(service_calls) == {}


@pytest.mark.integration
async def test_events_handled_when_event_handlers_disabled(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    network_room_config[DOMAIN]["areas"][0]["event_handlers"] = False
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    await _circadian(hass, "network_room")
    service_calls.clear()

    await _switch(hass, "light.network_room_overhead_2", "on", brightness=100)

    assert _turn_ons(service_calls) == {"light.network_room_overhead_2": PAYLOAD}


@pytest.mark.integration
async def test_cluster_switched_on_under_demand_response_is_ignored(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    members = [f"light.zone_{s}" for s in "abcd"]
    zone = "light.zone_all"
    _stub_off(hass, zone, *members)
    cfg = _area(
        "zone_room",
        [_grouped(member) for member in members],
        light_clusters=[{**_grouped(zone), "members": members}],
    )
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, cfg)
    toggles: GlobalToggles = hass.data[DOMAIN]["global"]
    toggles._demand_response_active = True
    await _circadian(hass, "zone_room")
    service_calls.clear()

    await _switch(hass, zone, "on", brightness=100)

    assert _turn_ons(service_calls) == {}
