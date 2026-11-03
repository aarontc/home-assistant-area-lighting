"""The global number that lowers every circadian group's minimum brightness."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.const import DOMAIN

ENTITY_ID = "number.area_lighting_circadian_min_brightness_reduction"


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, DOMAIN, cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _config() -> dict:
    return {
        DOMAIN: {
            "areas": [
                {
                    "id": "den",
                    "name": "Den",
                    "event_handlers": True,
                    "circadian_groups": [
                        {"name": "Main", "max_brightness": 100, "min_brightness": 40},
                    ],
                    "lights": [
                        {"id": "light.den_lamp", "circadian_group": "Main", "circadian_type": "ct"}
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


def _lamp_brightness(service_calls) -> list[int]:
    return [
        c.data["brightness"]
        for c in service_calls
        if c.domain == "light"
        and c.service == "turn_on"
        and c.data["entity_id"] == "light.den_lamp"
    ]


async def _set_reduction(hass: HomeAssistant, value: float) -> None:
    await hass.services.async_call(
        "number", "set_value", {"entity_id": ENTITY_ID, "value": value}, blocking=True
    )
    await hass.async_block_till_done()


async def _den_in_circadian(hass: HomeAssistant):
    ctrl = hass.data[DOMAIN]["controllers"]["den"]
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    hass.states.async_set("light.den_lamp", "on", {"brightness": 100})
    await hass.async_block_till_done()
    ctrl._circadian_commanded_at.clear()
    return ctrl


@pytest.mark.integration
async def test_number_is_visible_and_defaults_to_zero(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.den_lamp", "off")
    await _setup(hass, _config())

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert float(state.state) == 0.0
    assert state.attributes["unit_of_measurement"] == "%"
    assert er.async_get(hass).async_get(ENTITY_ID).hidden_by is None


@pytest.mark.integration
async def test_setting_the_number_lowers_overnight_brightness_at_once(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set("light.den_lamp", "off")
    await pin_circadian(-50, 2700, tick=False)
    await _setup(hass, _config())
    await _den_in_circadian(hass)
    service_calls.clear()

    await _set_reduction(hass, 50)

    # Minimum 40 lowered by 50 % is 20; at -50 % the curve is 20 + 80 * 0.5 = 60 %.
    assert _lamp_brightness(service_calls) == [round(60 * 2.55)]
    assert float(hass.states.get(ENTITY_ID).state) == 50.0


@pytest.mark.integration
async def test_a_second_change_within_the_echo_window_still_reaches_the_lights(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set("light.den_lamp", "off")
    await pin_circadian(-100, 2700, tick=False)
    await _setup(hass, _config())
    await _den_in_circadian(hass)
    service_calls.clear()

    await _set_reduction(hass, 50)
    await _set_reduction(hass, 80)

    # Minimum 40 lowered by 50 % is 20 %, then by 80 % is 8 %.
    assert _lamp_brightness(service_calls) == [round(20 * 2.55), round(8 * 2.55)]


@pytest.mark.integration
async def test_a_change_during_a_tick_in_flight_still_reaches_the_lights(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set("light.den_lamp", "off")
    await pin_circadian(-100, 2700, tick=False)
    await _setup(hass, _config())
    ctrl = await _den_in_circadian(hass)
    service_calls.clear()

    release = asyncio.Event()

    async def slow_sync() -> None:
        await release.wait()

    ctrl._sync_kelvin_router = slow_sync
    await hass.data[DOMAIN]["circadian_clock"].async_tick()
    await asyncio.sleep(0)
    # Not _set_reduction: waiting for every task would wait on the held tick.
    await hass.services.async_call(
        "number", "set_value", {"entity_id": ENTITY_ID, "value": 50}, blocking=True
    )
    await asyncio.sleep(0)
    sent_before_release = list(_lamp_brightness(service_calls))
    release.set()
    await hass.async_block_till_done()

    # The change goes out while the tick is still held, and the tick, which
    # computes its payload after the change, sends the same 20 %.
    assert sent_before_release == [round(20 * 2.55)]
    assert set(_lamp_brightness(service_calls)) == {round(20 * 2.55)}


@pytest.mark.integration
async def test_reduction_reaches_the_lowered_minimum_at_solar_midnight(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set("light.den_lamp", "off")
    await pin_circadian(-100, 2700, tick=False)
    await _setup(hass, _config())
    await _set_reduction(hass, 80)
    service_calls.clear()

    await _den_in_circadian(hass)

    # Minimum 40 lowered by 80 % is 8 %.
    assert _lamp_brightness(service_calls) == [round(8 * 2.55)]


@pytest.mark.integration
async def test_reduction_leaves_daytime_brightness_alone(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    hass.states.async_set("light.den_lamp", "off")
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass, _config())
    await _den_in_circadian(hass)
    service_calls.clear()

    await _set_reduction(hass, 80)

    assert _lamp_brightness(service_calls) == [255]


@pytest.mark.integration
async def test_reduction_is_saved_with_the_global_state(
    hass: HomeAssistant, helper_entities
) -> None:
    hass.states.async_set("light.den_lamp", "off")
    await _setup(hass, _config())
    await _set_reduction(hass, 30)
    await hass.async_block_till_done()

    saved = hass.data[DOMAIN]["state_storage"].get_global_state()
    assert saved["circadian_min_brightness_reduction"] == 30.0
