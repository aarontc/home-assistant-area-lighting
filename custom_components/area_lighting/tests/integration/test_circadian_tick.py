"""The controller drives circadian lights from the built-in clock."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util.color import color_temperature_to_rgb

from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.global_state import GlobalToggles

NETWORK_LIGHTS = ("light.network_room_overhead_1", "light.network_room_overhead_2")


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


def _set_on(hass: HomeAssistant, *entity_ids: str) -> None:
    for entity_id in entity_ids:
        hass.states.async_set(entity_id, "on", {"brightness": 100})


async def _echo_on(hass: HomeAssistant, ctrl, *entity_ids: str) -> None:
    """Report lights on as the echo of the activation, then let the echo
    window lapse so the next tick sends."""
    _set_on(hass, *entity_ids)
    await hass.async_block_till_done()
    ctrl._circadian_commanded_at.clear()


async def _circadian(hass: HomeAssistant, area_id: str):
    ctrl = hass.data[DOMAIN]["controllers"][area_id]
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    return ctrl


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
                        {"id": "off", "name": "Off"},
                    ],
                    **extra,
                }
            ]
        }
    }


def _grouped(entity_id: str, circadian_type: str = "ct") -> dict:
    return {"id": entity_id, "circadian_group": "Main", "circadian_type": circadian_type}


def _stub_off(hass: HomeAssistant, *entity_ids: str) -> None:
    for entity_id in entity_ids:
        hass.states.async_set(entity_id, "off", {})


@pytest.mark.integration
async def test_activation_sends_payload_with_half_second_transition(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    service_calls.clear()

    await _circadian(hass, "network_room")

    expected = {"brightness": 255, "color_temp_kelvin": 4000, "transition": 0.5}
    assert _turn_ons(service_calls) == dict.fromkeys(NETWORK_LIGHTS, expected)


@pytest.mark.integration
async def test_tick_updates_only_lights_that_are_on(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = await _circadian(hass, "network_room")
    await _echo_on(hass, ctrl, "light.network_room_overhead_1")
    _stub_off(hass, "light.network_room_overhead_2")
    service_calls.clear()

    await pin_circadian(60, 4500)

    assert _turn_ons(service_calls) == {
        "light.network_room_overhead_1": {
            "brightness": 255,
            "color_temp_kelvin": 4500,
            "transition": 60,
        }
    }


@pytest.mark.integration
async def test_tick_uses_configured_transition(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    network_room_config[DOMAIN]["circadian"] = {"transition": 7}
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = await _circadian(hass, "network_room")
    await _echo_on(hass, ctrl, *NETWORK_LIGHTS)
    service_calls.clear()

    await pin_circadian(50, 4000)

    assert {data["transition"] for data in _turn_ons(service_calls).values()} == {7}
    assert set(_turn_ons(service_calls)) == set(NETWORK_LIGHTS)


@pytest.mark.integration
async def test_tick_keeps_a_fractional_transition(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    network_room_config[DOMAIN]["circadian"] = {"transition": 2.5}
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = await _circadian(hass, "network_room")
    await _echo_on(hass, ctrl, *NETWORK_LIGHTS)
    service_calls.clear()

    await pin_circadian(50, 4000)

    assert {data["transition"] for data in _turn_ons(service_calls).values()} == {2.5}


@pytest.mark.integration
async def test_tick_skips_shed_lights(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    lights = [f"light.study_{i}" for i in range(1, 7)]
    _stub_off(hass, *lights)
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, _area("study", [_grouped(light) for light in lights]))
    toggles: GlobalToggles = hass.data[DOMAIN]["global"]
    toggles._demand_response_active = True
    ctrl = await _circadian(hass, "study")
    assert ctrl.dr_shed_ids == frozenset(lights[2:])
    await _echo_on(hass, ctrl, *lights)
    service_calls.clear()

    await pin_circadian(50, 4000)

    assert set(_turn_ons(service_calls)) == {"light.study_1", "light.study_2"}


@pytest.mark.integration
async def test_tick_skips_while_alert_active(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = await _circadian(hass, "network_room")
    await _echo_on(hass, ctrl, *NETWORK_LIGHTS)
    ctrl._alert_active = True
    service_calls.clear()

    await pin_circadian(50, 4000)

    assert _turn_ons(service_calls) == {}


@pytest.mark.integration
async def test_tick_skips_lights_commanded_within_echo_window(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = hass.data[DOMAIN]["controllers"]["network_room"]
    await ctrl.lighting_circadian()
    _set_on(hass, *NETWORK_LIGHTS)
    await hass.async_block_till_done()
    # The activation just commanded both lights; age one past the window.
    ctrl._circadian_commanded_at["light.network_room_overhead_2"] -= 10
    service_calls.clear()

    await pin_circadian(50, 4000)

    assert set(_turn_ons(service_calls)) == {"light.network_room_overhead_2"}


@pytest.mark.integration
async def test_tick_skips_route_lights_outside_the_active_route(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    strips = [f"light.kitchen_strip_{i}" for i in range(1, 4)]
    tube = "light.kitchen_fluorescent"
    _stub_off(hass, tube, *strips)
    routes = {
        "crossfade_seconds": 1.0,
        "routes": [
            {"kelvin_range": [4500, 5500], "lights": [tube]},
            {"lights": strips},
        ],
    }
    cfg = _area(
        "kitchen",
        [_grouped(light) for light in (tube, *strips)],
        circadian_kelvin_routes=routes,
    )
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, cfg)
    ctrl = await _circadian(hass, "kitchen")
    # The outgoing tube still reports on, as it would mid-crossfade.
    await _echo_on(hass, ctrl, tube, *strips)
    service_calls.clear()

    await pin_circadian(50, 4000)

    assert set(_turn_ons(service_calls)) == set(strips)


@pytest.mark.integration
async def test_tick_skips_clusters_under_demand_response(
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
    ctrl = await _circadian(hass, "zone_room")
    await _echo_on(hass, ctrl, zone, *members)
    service_calls.clear()

    await pin_circadian(50, 4000)

    assert set(_turn_ons(service_calls)) == {"light.zone_a", "light.zone_b"}


@pytest.mark.integration
async def test_rgb_light_gets_rgb_color(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    _stub_off(hass, "light.den_strip")
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, _area("den", [_grouped("light.den_strip", "rgb")]))
    service_calls.clear()

    await _circadian(hass, "den")

    rgb = tuple(round(c) for c in color_temperature_to_rgb(4000))
    assert _turn_ons(service_calls) == {
        "light.den_strip": {"brightness": 255, "rgb_color": rgb, "transition": 0.5}
    }


@pytest.mark.integration
async def test_brightness_light_gets_brightness_only(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    _stub_off(hass, "light.den_lamp")
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, _area("den", [_grouped("light.den_lamp", "brightness")]))
    service_calls.clear()

    await _circadian(hass, "den")

    assert _turn_ons(service_calls) == {"light.den_lamp": {"brightness": 255, "transition": 0.5}}


@pytest.mark.integration
async def test_group_bounds_shape_overnight_brightness(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    _stub_off(hass, "light.den_lamp")
    await pin_circadian(-50, 2500, tick=False)
    await _setup(hass, _area("den", [_grouped("light.den_lamp")]))
    service_calls.clear()

    await _circadian(hass, "den")

    # Bounds 40/100 at -50 %: 40 + 60 * 0.5 = 70 %.
    assert _turn_ons(service_calls)["light.den_lamp"]["brightness"] == round(70 * 2.55) == 178


@pytest.mark.integration
async def test_dimmed_circadian_area_is_not_adjusted(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = await _circadian(hass, "network_room")
    await _echo_on(hass, ctrl, *NETWORK_LIGHTS)
    await ctrl.lighting_lower()
    await hass.async_block_till_done()
    assert ctrl._state.is_circadian
    assert ctrl._state.dimmed
    service_calls.clear()

    await pin_circadian(50, 4000)

    assert _turn_ons(service_calls) == {}


@pytest.mark.integration
async def test_overlapping_ticks_do_not_run_concurrently(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = await _circadian(hass, "network_room")
    await _echo_on(hass, ctrl, *NETWORK_LIGHTS)
    service_calls.clear()

    release = asyncio.Event()
    syncs = 0

    async def slow_sync() -> None:
        nonlocal syncs
        syncs += 1
        await release.wait()

    ctrl._sync_kelvin_router = slow_sync
    clock = hass.data[DOMAIN]["circadian_clock"]
    await clock.async_tick()
    await asyncio.sleep(0)
    await clock.async_tick()
    await asyncio.sleep(0)
    release.set()
    await hass.async_block_till_done()

    assert syncs == 1
    turn_ons = [c for c in service_calls if c.domain == "light" and c.service == "turn_on"]
    assert sorted(c.data["entity_id"] for c in turn_ons) == sorted(NETWORK_LIGHTS)


@pytest.mark.integration
async def test_stop_before_dispatch_drops_the_tick_commands(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = await _circadian(hass, "network_room")
    await _echo_on(hass, ctrl, *NETWORK_LIGHTS)
    service_calls.clear()

    recently_commanded = ctrl._recently_commanded

    def recently_commanded_then_queue_stop(entity_id):
        # The stop is queued ahead of the send tasks, as a turn-off
        # handler already waiting on the loop would be.
        hass.loop.call_soon(ctrl._stop_circadian)
        return recently_commanded(entity_id)

    ctrl._recently_commanded = recently_commanded_then_queue_stop
    await pin_circadian(50, 4000)

    assert not ctrl.circadian_running
    assert _turn_ons(service_calls) == {}


@pytest.mark.integration
async def test_failed_command_holds_the_tick_until_its_siblings_finish(
    hass: HomeAssistant,
    helper_entities,
    network_room_config,
    pin_circadian,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, network_room_config)
    ctrl = await _circadian(hass, "network_room")
    await _echo_on(hass, ctrl, *NETWORK_LIGHTS)

    failing, blocked = NETWORK_LIGHTS
    sent: list[str] = []
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def call_service(service: str, **kwargs) -> None:
        sent.append(kwargs["entity_id"])
        if kwargs["entity_id"] == failing:
            raise HomeAssistantError("unreachable")
        waiting.set()
        await release.wait()

    ctrl._call_service = call_service
    clock = hass.data[DOMAIN]["circadian_clock"]
    await clock.async_tick()
    await waiting.wait()
    for _ in range(5):
        await asyncio.sleep(0)
    # A later tick, past the echo window, while the sibling still blocks.
    ctrl._circadian_commanded_at.clear()
    await clock.async_tick()
    for _ in range(5):
        await asyncio.sleep(0)
    release.set()
    await hass.async_block_till_done()

    assert sent.count(blocked) == 1
    assert f"circadian update of {failing} failed: unreachable" in caplog.text
