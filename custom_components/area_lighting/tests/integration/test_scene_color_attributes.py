"""Scene color attributes must pass Home Assistant's light.turn_on schema.

The shared `service_calls` fixture mocks light.turn_on without a schema,
so it accepts arguments the real service rejects. These tests validate
against the real LIGHT_TURN_ON_SCHEMA:

- Home Assistant 2026.3 removed the mired `color_temp` argument, which
  scene config and snapshots from older releases still carry.
- A light reports every color representation at once (a light in
  color_temp mode also reports hs, rgb and xy), but light.turn_on takes
  exactly one, so a snapshot must replay only the one its color_mode names.
"""

from __future__ import annotations

import pytest
from homeassistant.components.light import LIGHT_TURN_ON_SCHEMA
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.const import DOMAIN

COLOR_KEYS = {
    "color_temp",
    "color_temp_kelvin",
    "hs_color",
    "rgb_color",
    "rgbw_color",
    "rgbww_color",
    "white",
    "xy_color",
}


def _den_config(daylight_entities: dict | None = None) -> dict:
    daylight: dict = {"id": "daylight", "name": "Daylight"}
    if daylight_entities is not None:
        daylight["entities"] = daylight_entities
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "den",
                    "name": "Den",
                    "event_handlers": True,
                    "lights": [{"id": "light.den_a", "roles": ["dimming"]}],
                    "scenes": [{"id": "circadian", "name": "Circadian"}, daylight],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> list:
    hass.states.async_set("light.den_a", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    async_mock_service(hass, "light", "turn_off")
    return async_mock_service(
        hass,
        "light",
        "turn_on",
        schema=cv.make_entity_service_schema(LIGHT_TURN_ON_SCHEMA),
    )


# What a bulb reports in each mode: the native color plus derived ones.
COLOR_TEMP_LIGHT = {
    "brightness": 200,
    "color_mode": "color_temp",
    "color_temp_kelvin": 3000,
    "hs_color": (27.0, 44.0),
    "rgb_color": (255, 184, 143),
    "xy_color": (0.46, 0.38),
}
RGBW_LIGHT = {
    "brightness": 160,
    "color_mode": "rgbw",
    "rgbw_color": (255, 0, 0, 64),
    "hs_color": (0.0, 75.0),
    "rgb_color": (255, 64, 64),
    "xy_color": (0.64, 0.32),
}
RGBWW_LIGHT = {
    "brightness": 140,
    "color_mode": "rgbww",
    "rgbww_color": (0, 128, 255, 20, 90),
    "hs_color": (215.0, 80.0),
    "rgb_color": (51, 150, 255),
    "xy_color": (0.17, 0.2),
}


async def _snapshot(hass: HomeAssistant, attributes: dict) -> None:
    hass.states.async_set("light.den_a", "on", attributes)
    storage = hass.data[DOMAIN]["scene_storage"]
    await storage.async_snapshot_scene("den", "daylight", ["light.den_a"])
    hass.states.async_set("light.den_a", "off", {})
    await hass.async_block_till_done()


def _color_args(calls: list) -> list[dict]:
    return [{k: v for k, v in c.data.items() if k in COLOR_KEYS} for c in calls]


@pytest.mark.integration
async def test_config_color_temp_is_sent_as_kelvin(hass: HomeAssistant, helper_entities) -> None:
    turn_on = await _setup(hass, _den_config({"light.den_a": {"state": "on", "color_temp": 333}}))
    ctrl = hass.data[DOMAIN]["controllers"]["den"]

    await ctrl._activate_scene("daylight", ActivationSource.USER)
    await hass.async_block_till_done()

    assert _color_args(turn_on) == [{"color_temp_kelvin": 3003}]


@pytest.mark.integration
async def test_snapshot_replays_only_its_color_mode(hass: HomeAssistant, helper_entities) -> None:
    turn_on = await _setup(hass, _den_config())
    await _snapshot(hass, COLOR_TEMP_LIGHT)
    ctrl = hass.data[DOMAIN]["controllers"]["den"]

    await ctrl._activate_scene("daylight", ActivationSource.USER)
    await hass.async_block_till_done()

    assert _color_args(turn_on) == [{"color_temp_kelvin": 3000}]
    assert turn_on[0].data["brightness"] == 200


@pytest.mark.integration
async def test_scene_entity_replays_only_its_color_mode(
    hass: HomeAssistant, helper_entities
) -> None:
    turn_on = await _setup(hass, _den_config())
    await _snapshot(hass, COLOR_TEMP_LIGHT)

    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.den_daylight"}, blocking=True
    )
    await hass.async_block_till_done()

    assert _color_args(turn_on) == [{"color_temp_kelvin": 3000}]


@pytest.mark.integration
async def test_stored_mired_snapshot_is_sent_as_kelvin(
    hass: HomeAssistant, helper_entities
) -> None:
    """A snapshot taken before 2026.3 holds `color_temp` in mireds."""
    turn_on = await _setup(hass, _den_config())
    storage = hass.data[DOMAIN]["scene_storage"]
    storage._data.setdefault("den", {})["daylight"] = {
        "light.den_a": {"state": "on", "brightness": 180, "color_temp": 250}
    }
    ctrl = hass.data[DOMAIN]["controllers"]["den"]

    await ctrl._activate_scene("daylight", ActivationSource.USER)
    await hass.async_block_till_done()

    assert _color_args(turn_on) == [{"color_temp_kelvin": 4000}]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("light", "native"),
    [(RGBW_LIGHT, "rgbw_color"), (RGBWW_LIGHT, "rgbww_color")],
)
async def test_white_channel_snapshot_replays_its_native_color(
    hass: HomeAssistant, helper_entities, light: dict, native: str
) -> None:
    turn_on = await _setup(hass, _den_config())
    await _snapshot(hass, light)
    ctrl = hass.data[DOMAIN]["controllers"]["den"]

    await ctrl._activate_scene("daylight", ActivationSource.USER)
    await hass.async_block_till_done()

    # The schema hands the service a tuple.
    assert _color_args(turn_on) == [{native: light[native]}]
