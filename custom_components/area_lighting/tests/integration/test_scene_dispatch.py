"""Scene tracker must dispatch to every targeted area, not just the first.

Regression for the bug where a script calling
`scene.turn_on target.entity_id: [scene.a_daylight, scene.b_daylight, ...]`
left every area after the first stuck in `state=off`: the scene entities'
`async_activate` still ran (lights physically turned on), but the
controllers' state machines never transitioned, so the occupancy timer
was never armed and the lights stayed on indefinitely.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.fixture
def two_area_config() -> dict:
    """Two minimal areas with overlapping scene slugs."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "network_room",
                    "name": "Network Room",
                    "event_handlers": True,
                    "lights": [
                        {
                            "id": "light.network_room_overhead_1",
                            "roles": ["dimming"],
                        },
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                    ],
                },
                {
                    "id": "craft_room",
                    "name": "Craft Room",
                    "event_handlers": True,
                    "lights": [
                        {
                            "id": "light.craft_room_overhead_1",
                            "roles": ["dimming"],
                        },
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                    ],
                },
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    # Stub the craft_room light alongside the network_room lights that
    # helper_entities already creates, so the validator's light-existence
    # check is satisfied for both areas.
    hass.states.async_set("light.craft_room_overhead_1", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_scene_turn_on_with_entity_list_dispatches_to_every_area(
    hass: HomeAssistant, helper_entities, two_area_config
) -> None:
    """A single scene.turn_on with a list target must fire
    handle_scene_activated on every targeted area's controller."""
    await _setup(hass, two_area_config)

    network_ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    craft_ctrl = hass.data["area_lighting"]["controllers"]["craft_room"]

    network_spy = AsyncMock(wraps=network_ctrl.handle_scene_activated)
    craft_spy = AsyncMock(wraps=craft_ctrl.handle_scene_activated)
    network_ctrl.handle_scene_activated = network_spy
    craft_ctrl.handle_scene_activated = craft_spy

    # Mirror the call_service payload HA produces when a script uses
    # `service: scene.turn_on` with `target.entity_id: [list]` — the
    # registry normalizes target into service_data.entity_id.
    hass.bus.async_fire(
        "call_service",
        {
            "domain": "scene",
            "service": "turn_on",
            "service_data": {
                "entity_id": [
                    "scene.craft_room_daylight",
                    "scene.network_room_daylight",
                ],
            },
        },
    )
    await hass.async_block_till_done()

    network_spy.assert_awaited_once_with("daylight")
    craft_spy.assert_awaited_once_with("daylight")


@pytest.mark.integration
async def test_scene_turn_on_with_single_entity_string_still_dispatches(
    hass: HomeAssistant, helper_entities, two_area_config
) -> None:
    """Single-entity scene.turn_on (the original supported shape) keeps working."""
    await _setup(hass, two_area_config)

    network_ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    network_spy = AsyncMock(wraps=network_ctrl.handle_scene_activated)
    network_ctrl.handle_scene_activated = network_spy

    hass.bus.async_fire(
        "call_service",
        {
            "domain": "scene",
            "service": "turn_on",
            "service_data": {"entity_id": "scene.network_room_daylight"},
        },
    )
    await hass.async_block_till_done()

    network_spy.assert_awaited_once_with("daylight")


@pytest.mark.integration
async def test_scene_turn_on_with_empty_entity_list_is_noop(
    hass: HomeAssistant, helper_entities, two_area_config
) -> None:
    """A degenerate empty entity_id list must not raise and must not
    dispatch to any controller."""
    await _setup(hass, two_area_config)

    network_ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    craft_ctrl = hass.data["area_lighting"]["controllers"]["craft_room"]
    network_spy = AsyncMock(wraps=network_ctrl.handle_scene_activated)
    craft_spy = AsyncMock(wraps=craft_ctrl.handle_scene_activated)
    network_ctrl.handle_scene_activated = network_spy
    craft_ctrl.handle_scene_activated = craft_spy

    hass.bus.async_fire(
        "call_service",
        {
            "domain": "scene",
            "service": "turn_on",
            "service_data": {"entity_id": []},
        },
    )
    await hass.async_block_till_done()

    network_spy.assert_not_awaited()
    craft_spy.assert_not_awaited()
