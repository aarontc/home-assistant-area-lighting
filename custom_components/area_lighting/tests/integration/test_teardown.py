"""async_teardown and closed controllers leave nothing running."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting import async_teardown

MOTION_SENSOR = "binary_sensor.network_room_motion_sensor_motion"


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.fixture
def routed_kitchen_config() -> dict:
    """A kitchen whose fluorescent is routed in [4500, 5500] K, strip as fallback."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "event_handlers": True,
                    "circadian_groups": [
                        {"name": "Kitchen", "max_brightness": 100, "min_brightness": 20},
                    ],
                    "lights": [
                        {
                            "id": "light.kitchen_fluorescent",
                            "circadian_group": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip",
                            "circadian_group": "Kitchen",
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
                                "lights": ["light.kitchen_fluorescent"],
                            },
                            {"lights": ["light.kitchen_strip"]},
                        ],
                    },
                }
            ]
        }
    }


async def _setup_routed_kitchen(hass: HomeAssistant, cfg: dict):
    hass.states.async_set("light.kitchen_fluorescent", "off", {})
    hass.states.async_set("light.kitchen_strip", "off", {})
    await _setup(hass, cfg)
    return hass.data["area_lighting"]["controllers"]["kitchen"]


@pytest.mark.integration
async def test_teardown_unsubscribes_event_handlers(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    hass.states.async_set(MOTION_SENSOR, "off")
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.motion_light_enabled = True

    with patch.object(ctrl, "handle_motion_on", AsyncMock()) as spy:
        hass.states.async_set(MOTION_SENSOR, "on")
        await hass.async_block_till_done()
        assert spy.await_count == 1

        hass.states.async_set(MOTION_SENSOR, "off")
        await hass.async_block_till_done()
        await async_teardown(hass)
        hass.states.async_set(MOTION_SENSOR, "on")
        await hass.async_block_till_done()
        assert spy.await_count == 1


@pytest.mark.integration
async def test_closed_controller_does_not_save_state(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    storage = hass.data["area_lighting"]["state_storage"]
    listener = MagicMock()
    ctrl.add_state_listener(listener)

    ctrl.shutdown()
    assert ctrl.closed
    with patch.object(storage, "async_save_area_state", AsyncMock()) as save:
        ctrl._notify_state_change()
        await hass.async_block_till_done()

    listener.assert_called_once()
    save.assert_not_called()


@pytest.mark.integration
async def test_closed_controller_does_not_sync_kelvin_router(
    hass: HomeAssistant, helper_entities, service_calls, routed_kitchen_config
) -> None:
    ctrl = await _setup_routed_kitchen(hass, routed_kitchen_config)
    ctrl.shutdown()

    with patch.object(ctrl._kelvin_router, "sync_to_state", AsyncMock()) as sync:
        await ctrl._sync_kelvin_router()

    sync.assert_not_called()


@pytest.mark.integration
async def test_teardown_deactivates_kelvin_router(
    hass: HomeAssistant, helper_entities, service_calls, routed_kitchen_config
) -> None:
    ctrl = await _setup_routed_kitchen(hass, routed_kitchen_config)
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl._kelvin_router.current_index is not None

    await async_teardown(hass)

    assert ctrl._kelvin_router.current_index is None


@pytest.mark.integration
async def test_teardown_is_idempotent(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)

    await async_teardown(hass)
    await async_teardown(hass)

    assert hass.data["area_lighting"]["unsubs"] == []
    assert all(ctrl.closed for ctrl in hass.data["area_lighting"]["controllers"].values())


@pytest.mark.integration
async def test_teardown_without_setup_does_nothing(hass: HomeAssistant) -> None:
    await async_teardown(hass)

    assert "area_lighting" not in hass.data


@pytest.mark.integration
async def test_teardown_before_started_registers_no_entities(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    assert await async_setup_component(hass, "area_lighting", network_room_config)
    await hass.async_block_till_done()
    before = set(hass.states.async_entity_ids())

    await async_teardown(hass)
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    assert set(hass.states.async_entity_ids()) == before


@pytest.mark.integration
async def test_teardown_during_startup_registers_no_more_entities(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    assert await async_setup_component(hass, "area_lighting", network_room_config)
    await hass.async_block_till_done()
    before = set(hass.states.async_entity_ids())
    entered = asyncio.Event()
    release = asyncio.Event()

    async def stalled_scene_registration(*_args) -> None:
        entered.set()
        await release.wait()

    with patch(
        "custom_components.area_lighting._register_scene_entities",
        stalled_scene_registration,
    ):
        hass.bus.async_fire("homeassistant_started")
        await entered.wait()
        await async_teardown(hass)
        release.set()
        await hass.async_block_till_done()

    assert set(hass.states.async_entity_ids()) == before


@pytest.mark.integration
async def test_closed_controller_schedules_no_self_heal_check(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._active_scene_targets = {
        "light.network_room_overhead_1": {
            "state": "on",
            "brightness": 10,
            "commanded_at": time.monotonic(),
            "transition": 0.0,
        }
    }

    ctrl.shutdown()
    ctrl._schedule_post_settle_selfcheck(0.0)

    assert ctrl._heal_selfcheck_handle is None
