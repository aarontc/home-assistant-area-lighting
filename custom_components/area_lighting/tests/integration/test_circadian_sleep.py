"""Circadian groups follow their sleep entity."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting import alert
from custom_components.area_lighting.alert import execute_alert
from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.global_state import GlobalToggles
from custom_components.area_lighting.models import AlertPattern, AlertStep

SLEEP_ENTITY = "input_select.bedroom_mode"
MAIN = "light.bedroom_main"
OTHER = "light.bedroom_other"
AWAKE_PAYLOAD = {"brightness": 255, "color_temp_kelvin": 4000}
# sleep_brightness 5 % is round(5 * 2.55) = 13.
SLEEP_PAYLOAD = {"brightness": 13, "color_temp_kelvin": 2000}


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


def _config(lights: list[dict] | None = None, **extra) -> dict:
    return {
        DOMAIN: {
            "areas": [
                {
                    "id": "bedroom",
                    "name": "Bedroom",
                    "event_handlers": True,
                    "circadian_groups": [
                        {
                            "name": "Main",
                            "sleep_entity": SLEEP_ENTITY,
                            "sleep_state": ["sleep", "nap"],
                            "sleep_brightness": 5,
                            "sleep_colortemp": 2000,
                        },
                        {"name": "Other"},
                    ],
                    "lights": lights
                    or [
                        {"id": MAIN, "circadian_group": "Main", "circadian_type": "ct"},
                        {"id": OTHER, "circadian_group": "Other", "circadian_type": "ct"},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                    ],
                    **extra,
                }
            ]
        }
    }


async def _bedroom(hass: HomeAssistant, pin_circadian, cfg: dict | None = None):
    for entity_id in (MAIN, OTHER):
        hass.states.async_set(entity_id, "off", {})
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, cfg or _config())
    return hass.data[DOMAIN]["controllers"]["bedroom"]


async def _circadian_with_lights_on(hass: HomeAssistant, ctrl, *entity_ids: str) -> None:
    """Activate circadian, report the lights on, then let the echo window lapse."""
    await ctrl.lighting_circadian()
    for entity_id in entity_ids:
        hass.states.async_set(entity_id, "on", {"brightness": 255})
    await hass.async_block_till_done()
    ctrl._circadian_commanded_at.clear()


@pytest.mark.integration
async def test_sleeping_group_uses_sleep_brightness_and_colortemp(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set(SLEEP_ENTITY, "nap")
    ctrl = await _bedroom(hass, pin_circadian)
    service_calls.clear()

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    assert _turn_ons(service_calls)[MAIN] == {**SLEEP_PAYLOAD, "transition": 0.5}


@pytest.mark.integration
async def test_sleep_entity_change_updates_group_with_half_second_transition(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set(SLEEP_ENTITY, "awake")
    ctrl = await _bedroom(hass, pin_circadian)
    await _circadian_with_lights_on(hass, ctrl, MAIN, OTHER)
    service_calls.clear()

    hass.states.async_set(SLEEP_ENTITY, "sleep")
    await hass.async_block_till_done()

    assert _turn_ons(service_calls) == {MAIN: {**SLEEP_PAYLOAD, "transition": 0.5}}


@pytest.mark.integration
async def test_sleep_attribute_only_change_is_ignored(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set(SLEEP_ENTITY, "sleep")
    ctrl = await _bedroom(hass, pin_circadian)
    await _circadian_with_lights_on(hass, ctrl, MAIN, OTHER)
    service_calls.clear()

    hass.states.async_set(SLEEP_ENTITY, "sleep", {"friendly_name": "Bedroom mode"})
    await hass.async_block_till_done()

    assert _turn_ons(service_calls) == {}


@pytest.mark.integration
async def test_missing_sleep_entity_means_awake(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    ctrl = await _bedroom(hass, pin_circadian)
    assert hass.states.get(SLEEP_ENTITY) is None
    service_calls.clear()

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    assert _turn_ons(service_calls)[MAIN] == {**AWAKE_PAYLOAD, "transition": 0.5}


@pytest.mark.integration
async def test_sleep_applies_only_to_its_group(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set(SLEEP_ENTITY, "sleep")
    ctrl = await _bedroom(hass, pin_circadian)
    service_calls.clear()

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    assert _turn_ons(service_calls) == {
        MAIN: {**SLEEP_PAYLOAD, "transition": 0.5},
        OTHER: {**AWAKE_PAYLOAD, "transition": 0.5},
    }


@pytest.mark.integration
async def test_sleep_change_during_alert_is_applied_after_the_alert(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian, monkeypatch
) -> None:
    hass.states.async_set(SLEEP_ENTITY, "awake")
    ctrl = await _bedroom(hass, pin_circadian)
    await _circadian_with_lights_on(hass, ctrl, MAIN, OTHER)
    service_calls.clear()
    during_alert: dict[str, dict] = {}

    async def steps_with_sleep_change(*_args) -> None:
        hass.states.async_set(SLEEP_ENTITY, "sleep")
        for _ in range(5):
            await asyncio.sleep(0)
        during_alert.update(_turn_ons(service_calls))

    monkeypatch.setattr(alert, "_execute_steps", steps_with_sleep_change)
    pattern = AlertPattern(steps=[AlertStep(target="all", state="on")], restore=False)
    await execute_alert(hass, ctrl, pattern)
    await hass.async_block_till_done()

    assert during_alert == {}
    assert _turn_ons(service_calls)[MAIN] == {**SLEEP_PAYLOAD, "transition": 60}


@pytest.mark.integration
async def test_sleep_change_during_short_alert_is_applied_after_the_alert(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian, monkeypatch
) -> None:
    hass.states.async_set(SLEEP_ENTITY, "awake")
    ctrl = await _bedroom(hass, pin_circadian)
    await ctrl.lighting_circadian()
    for entity_id in (MAIN, OTHER):
        hass.states.async_set(entity_id, "on", {"brightness": 255})
    await hass.async_block_till_done()
    # The alert ends inside the echo window of the activation's commands.
    assert MAIN in ctrl._circadian_commanded_at
    service_calls.clear()

    async def steps_with_sleep_change(*_args) -> None:
        hass.states.async_set(SLEEP_ENTITY, "sleep")

    monkeypatch.setattr(alert, "_execute_steps", steps_with_sleep_change)
    pattern = AlertPattern(steps=[AlertStep(target="all", state="on")], restore=False)
    await execute_alert(hass, ctrl, pattern)
    await hass.async_block_till_done()

    assert _turn_ons(service_calls)[MAIN] == {**SLEEP_PAYLOAD, "transition": 60}


@pytest.mark.integration
async def test_sleep_change_does_not_relight_a_light_switched_off_before_the_send(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set(SLEEP_ENTITY, "awake")
    ctrl = await _bedroom(hass, pin_circadian)
    await _circadian_with_lights_on(hass, ctrl, MAIN, OTHER)
    service_calls.clear()

    hass.states.async_set(SLEEP_ENTITY, "sleep")
    hass.states.async_set(MAIN, "off", {})
    await hass.async_block_till_done()

    assert ctrl.current_scene == "circadian"
    assert _turn_ons(service_calls) == {}


@pytest.mark.integration
async def test_sleep_change_skips_clusters_under_demand_response(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    members = [f"light.bedroom_{s}" for s in "abcd"]
    zone = "light.bedroom_zone"
    for entity_id in (zone, *members):
        hass.states.async_set(entity_id, "off", {})
    grouped = {"circadian_group": "Main", "circadian_type": "ct"}
    cfg = _config(
        [{"id": member, **grouped} for member in members],
        light_clusters=[{"id": zone, **grouped, "members": members}],
    )
    hass.states.async_set(SLEEP_ENTITY, "awake")
    ctrl = await _bedroom(hass, pin_circadian, cfg)
    toggles: GlobalToggles = hass.data[DOMAIN]["global"]
    toggles._demand_response_active = True
    await _circadian_with_lights_on(hass, ctrl, zone, *members)
    service_calls.clear()

    hass.states.async_set(SLEEP_ENTITY, "sleep")
    await hass.async_block_till_done()

    assert set(_turn_ons(service_calls)) == {"light.bedroom_a", "light.bedroom_b"}
