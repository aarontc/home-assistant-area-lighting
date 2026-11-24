"""Light sync: while a light_sync entity is on, something else drives the
area's lights (a Hue Sync Box streaming to an entertainment area). Manual
detection and self-heal stand down, and the scene is re-applied once the
sync ends."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource

SYNC = "binary_sensor.network_room_tv_entertainment_configuration"
LIGHT = "light.network_room_overhead_1"


async def _setup(hass: HomeAssistant, cfg: dict, sync: str = "off"):
    cfg["area_lighting"]["areas"][0]["light_sync_entity_ids"] = [SYNC]
    hass.states.async_set(SYNC, sync)
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set(LIGHT, "on", {"brightness": 200, "color_temp_kelvin": 2700})
    await hass.async_block_till_done()
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0
    ctrl._active_scene_targets = {
        LIGHT: {
            "state": "on",
            "brightness": 200,
            "color_temp_kelvin": 2700,
            "commanded_at": time.monotonic() - 600.0,
            "transition": 0.0,
        }
    }
    return ctrl


async def _stream_color(hass: HomeAssistant) -> None:
    hass.states.async_set(LIGHT, "on", {"brightness": 90, "xy_color": [0.2, 0.6]})
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_stream_does_not_latch_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    assert ctrl.light_sync_active
    await _stream_color(hass)
    assert ctrl.current_scene == "evening"


@pytest.mark.integration
async def test_change_without_sync_still_latches_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config)
    assert not ctrl.light_sync_active
    await _stream_color(hass)
    assert ctrl.current_scene == "manual"


@pytest.mark.integration
async def test_heal_stands_down_during_sync(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    assert ctrl._heal_suppressed() == "light sync active"
    service_calls.clear()
    await ctrl.handle_scene_drift_reassert(LIGHT, "glitch_window")
    await hass.async_block_till_done()
    assert not service_calls


@pytest.mark.integration
async def test_sync_end_holds_then_redrives(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    ctrl._redrive = AsyncMock()

    hass.states.async_set(SYNC, "off")
    await hass.async_block_till_done()
    handle = ctrl._light_sync_end_handle
    assert handle is not None
    assert ctrl.light_sync_active, "the bridge's restore lands during the hold"
    await _stream_color(hass)
    assert ctrl.current_scene == "evening"

    handle.cancel()
    ctrl._end_light_sync()
    await hass.async_block_till_done()
    ctrl._redrive.assert_awaited_once()
    assert not ctrl.light_sync_active


@pytest.mark.integration
async def test_sync_restarted_during_hold_skips_redrive(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    ctrl._redrive = AsyncMock()
    hass.states.async_set(SYNC, "off")
    await hass.async_block_till_done()
    handle = ctrl._light_sync_end_handle
    hass.states.async_set(SYNC, "on")
    await hass.async_block_till_done()

    handle.cancel()
    ctrl._end_light_sync()
    await hass.async_block_till_done()
    ctrl._redrive.assert_not_awaited()
    assert ctrl.light_sync_active


@pytest.mark.integration
async def test_redrive_reapplies_scene(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    hass.states.async_set(SYNC, "off")
    await hass.async_block_till_done()
    ctrl._light_sync_end_handle.cancel()
    service_calls.clear()
    ctrl._end_light_sync()
    await hass.async_block_till_done()
    assert any(c.service == "turn_on" for c in service_calls)
    assert ctrl.current_scene == "evening"


@pytest.mark.integration
async def test_manual_area_is_not_redriven(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    ctrl._state.transition_to_manual()
    hass.states.async_set(SYNC, "off")
    await hass.async_block_till_done()
    ctrl._light_sync_end_handle.cancel()
    service_calls.clear()
    ctrl._end_light_sync()
    await hass.async_block_till_done()
    assert not service_calls
    assert ctrl.current_scene == "manual"


@pytest.mark.integration
async def test_unloading_cancels_pending_redrive(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    hass.states.async_set(SYNC, "off")
    await hass.async_block_till_done()
    handle = ctrl._light_sync_end_handle
    ctrl.shutdown()
    assert handle.cancelled()
    assert ctrl._light_sync_end_handle is None


@pytest.mark.integration
async def test_light_sync_is_optional(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    assert await async_setup_component(hass, "area_lighting", network_room_config)
    await hass.async_block_till_done()
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl.area.light_sync_entity_ids == []
    assert not ctrl.light_sync_active


@pytest.mark.integration
async def test_streaming_bulb_skipped_before_sensor_turns_on(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config)
    ctrl._hue_resource = lambda entity_id: SimpleNamespace(entertainment_active=True)
    assert not ctrl.light_sync_active
    await _stream_color(hass)
    assert ctrl.current_scene == "evening"


@pytest.mark.integration
async def test_streaming_bulb_is_not_healed(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    ctrl = await _setup(hass, network_room_config)
    ctrl._hue_resource = lambda entity_id: SimpleNamespace(entertainment_active=True)
    service_calls.clear()
    await ctrl.handle_scene_drift_reassert(LIGHT, "glitch_window")
    await hass.async_block_till_done()
    assert not service_calls


@pytest.mark.integration
async def test_restore_to_all_off_during_hold_keeps_scene(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    hass.states.async_set(SYNC, "off")
    await hass.async_block_till_done()
    hass.states.async_set(LIGHT, "off")
    hass.states.async_set("light.network_room_overhead_2", "off")
    await hass.async_block_till_done()
    assert ctrl.current_scene == "evening"
    ctrl._run_lights_off_recheck()
    await hass.async_block_till_done()
    assert ctrl.current_scene == "evening"


@pytest.mark.integration
async def test_sync_end_resets_kelvin_route(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    ctrl._redrive = AsyncMock()
    ctrl._kelvin_router = MagicMock()
    hass.states.async_set(SYNC, "off")
    await hass.async_block_till_done()
    ctrl._light_sync_end_handle.cancel()
    ctrl._end_light_sync()
    await hass.async_block_till_done()
    ctrl._kelvin_router.deactivate.assert_called_once()
    ctrl._redrive.assert_awaited_once()


async def _expire_hold(hass: HomeAssistant, ctrl) -> None:
    ctrl._light_sync_end_handle.cancel()
    ctrl._end_light_sync()
    await hass.async_block_till_done()


@pytest.mark.parametrize("state", ["manual", "dimmed"])
@pytest.mark.integration
async def test_skipped_area_left_dark_turns_off(
    hass: HomeAssistant, helper_entities, network_room_config, state: str
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    if state == "manual":
        ctrl._state.transition_to_manual()
    else:
        ctrl._state.dimmed = True
    hass.states.async_set(SYNC, "off")
    hass.states.async_set(LIGHT, "off")
    await hass.async_block_till_done()
    await _expire_hold(hass, ctrl)
    assert ctrl._state.is_off


@pytest.mark.integration
async def test_hold_waits_for_bulbs_to_stop_streaming(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    ctrl._redrive = AsyncMock()
    streaming = SimpleNamespace(entertainment_active=True)
    ctrl._hue_resource = lambda entity_id: streaming
    hass.states.async_set(SYNC, "unavailable")
    await hass.async_block_till_done()

    await _expire_hold(hass, ctrl)
    ctrl._redrive.assert_not_awaited()
    assert ctrl._light_sync_end_handle is not None, "the hold renews while bulbs stream"
    await _stream_color(hass)
    assert ctrl.current_scene == "evening"

    streaming.entertainment_active = False
    await _expire_hold(hass, ctrl)
    ctrl._redrive.assert_awaited_once()
    assert not ctrl.light_sync_active


@pytest.mark.integration
async def test_stale_streaming_flag_yields_to_sensor_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    ctrl._redrive = AsyncMock()
    ctrl._hue_resource = lambda entity_id: SimpleNamespace(entertainment_active=True)
    hass.states.async_set(SYNC, "off")
    await hass.async_block_till_done()
    await _expire_hold(hass, ctrl)
    ctrl._redrive.assert_awaited_once()


@pytest.mark.integration
async def test_hold_waits_for_an_alert(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    ctrl._redrive = AsyncMock()
    ctrl._alert_active = True
    hass.states.async_set(SYNC, "off")
    await hass.async_block_till_done()
    await _expire_hold(hass, ctrl)
    ctrl._redrive.assert_not_awaited()
    assert ctrl._light_sync_end_handle is not None

    ctrl._alert_active = False
    await _expire_hold(hass, ctrl)
    ctrl._redrive.assert_awaited_once()


@pytest.mark.integration
async def test_dark_area_still_settling_is_rechecked(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config, sync="on")
    ctrl._state.dimmed = True
    hass.states.async_set(SYNC, "off")
    hass.states.async_set(LIGHT, "off")
    await hass.async_block_till_done()
    ctrl.lights_settling_until = lambda: time.monotonic() + 5.0
    await _expire_hold(hass, ctrl)
    assert not ctrl._state.is_off
    assert ctrl._light_sync_end_handle is not None

    ctrl._alert_active = True
    ctrl.lights_settling_until = lambda: None
    await _expire_hold(hass, ctrl)
    assert not ctrl._state.is_off, "an alert keeps the hold"

    ctrl._alert_active = False
    await _expire_hold(hass, ctrl)
    assert ctrl._state.is_off
