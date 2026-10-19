"""A manual action takes an area over from motion timing and alerts."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.alert import execute_alert
from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.models import AlertPattern, AlertStep

MOTION_SENSOR = "binary_sensor.network_room_motion_sensor_motion"
NETWORK_LIGHTS = ("light.network_room_overhead_1", "light.network_room_overhead_2")


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


async def _network_room_with_motion_timer(hass: HomeAssistant, cfg: dict):
    hass.states.async_set(MOTION_SENSOR, "off")
    await _setup(hass, cfg)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl.handle_motion_on()
    await ctrl.handle_motion_off()
    await hass.async_block_till_done()
    assert ctrl._state.source == ActivationSource.MOTION
    assert ctrl._motion_timer.is_active
    return ctrl


# ── Motion timing ──────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.parametrize(
    "action",
    ["on", "favorite", "raise", "circadian_service", "select"],
)
async def test_manual_action_cancels_motion_timer(
    hass: HomeAssistant, helper_entities, network_room_config, action
) -> None:
    ctrl = await _network_room_with_motion_timer(hass, network_room_config)

    if action == "on":
        await ctrl.lighting_on()
    elif action == "favorite":
        await ctrl.lighting_favorite()
    elif action == "raise":
        await ctrl.lighting_raise()
    elif action == "circadian_service":
        await ctrl.lighting_circadian()
    else:
        ctrl.current_scene = "evening"
    await hass.async_block_till_done()

    assert not ctrl._motion_timer.is_active
    assert not ctrl._motion_night_timer.is_active


@pytest.mark.integration
async def test_scene_from_the_app_cancels_motion_timer(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _network_room_with_motion_timer(hass, network_room_config)

    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.network_room_evening"}, blocking=True
    )
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "evening"
    assert not ctrl._motion_timer.is_active


@pytest.mark.integration
async def test_manual_light_change_cancels_motion_timer(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _network_room_with_motion_timer(hass, network_room_config)

    await ctrl.handle_manual_light_change()

    assert ctrl._state.is_manual
    assert not ctrl._motion_timer.is_active


@pytest.mark.integration
async def test_motion_actions_keep_the_motion_timer(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Only a person's action cancels it, not motion or ambience."""
    ctrl = await _network_room_with_motion_timer(hass, network_room_config)

    await ctrl.lighting_on(source=ActivationSource.MOTION)

    assert ctrl._motion_timer.is_active


@pytest.mark.integration
async def test_manual_action_keeps_the_occupancy_timer(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The occupancy timer is the escape hatch that still empties the room."""
    network_room_config["area_lighting"]["areas"][0]["occupancy_light_sensor_ids"] = [
        "binary_sensor.network_room_occupancy"
    ]
    hass.states.async_set("binary_sensor.network_room_occupancy", "off")
    ctrl = await _network_room_with_motion_timer(hass, network_room_config)
    assert ctrl._occupancy_timer.is_active

    await ctrl.lighting_favorite()
    await hass.async_block_till_done()

    assert not ctrl._motion_timer.is_active
    assert ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_manual_action_during_linked_cleanup_stops_the_fade(
    hass: HomeAssistant, helper_entities
) -> None:
    """A press while the motion timer is cleaning up keeps the press."""
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "theater",
                    "name": "Theater",
                    "event_handlers": True,
                    "lights": [{"id": "light.theater_overhead", "roles": ["dimming"]}],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "stairs", "name": "Stairs"},
                    ],
                },
                {
                    "id": "stairs",
                    "name": "Stairs",
                    "event_handlers": True,
                    "lights": [{"id": "light.stairs_upper", "roles": ["dimming"]}],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "evening", "name": "Evening"},
                    ],
                    "motion_light_motion_sensor_ids": ["binary_sensor.stairs_motion"],
                    "linked_motion": [
                        {
                            "remote_area": "theater",
                            "default": {"local_scene": "circadian", "remote_scene": None},
                            "when_remote_scene": {
                                "off": {"local_scene": "circadian", "remote_scene": "stairs"},
                            },
                        }
                    ],
                },
            ]
        }
    }
    for eid in ("light.theater_overhead", "light.stairs_upper", "binary_sensor.stairs_motion"):
        hass.states.async_set(eid, "off")
    await _setup(hass, cfg)
    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]
    await stairs_ctrl.handle_motion_on()
    await stairs_ctrl.handle_motion_off()
    await hass.async_block_till_done()

    original_activate = theater_ctrl._activate_scene

    async def activate_with_stairs_press(*args, **kwargs):
        await original_activate(*args, **kwargs)
        await hass.services.async_call(
            "scene", "turn_on", {"entity_id": "scene.stairs_evening"}, blocking=True
        )

    theater_ctrl._activate_scene = activate_with_stairs_press

    await stairs_ctrl._on_motion_timer()
    await hass.async_block_till_done()

    assert theater_ctrl._state.is_off
    assert stairs_ctrl._state.scene_slug == "evening"


# ── Alerts ─────────────────────────────────────────────────────────────


def _long_alert() -> AlertPattern:
    return AlertPattern(
        steps=[
            AlertStep(target="all", state="on", brightness=255, delay=30),
            AlertStep(target="all", state="off", delay=30),
        ],
        repeat=3,
        restore=True,
    )


@pytest.mark.integration
async def test_manual_action_stops_an_alert_without_restoring(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    for eid in NETWORK_LIGHTS:
        hass.states.async_set(eid, "on", {"brightness": 100})
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("evening", ActivationSource.USER)

    alert_task = hass.async_create_task(execute_alert(hass, ctrl, _long_alert()))
    await asyncio.sleep(0.05)
    assert ctrl._alert_active

    service_calls.clear()
    await ctrl.lighting_force_off()
    await asyncio.wait_for(alert_task, 2)
    await hass.async_block_till_done()

    assert ctrl._state.is_off
    assert not ctrl._alert_active
    assert not ctrl._alert_cancels
    # Nothing turned a light back on: no further flash and no restore.
    assert not [c for c in service_calls if c.service == "turn_on"]


@pytest.mark.integration
async def test_alert_service_returns_normally_when_a_manual_action_stops_it(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.data["area_lighting"]["config"].alert_patterns["long"] = _long_alert()

    call = hass.async_create_task(
        hass.services.async_call(
            "area_lighting",
            "alert",
            {"area_id": "network_room", "pattern": "long"},
            blocking=True,
        )
    )
    await asyncio.sleep(0.05)
    assert ctrl._alert_active

    await ctrl.lighting_raise()
    await asyncio.wait_for(call, 2)

    assert not ctrl._alert_active


@pytest.mark.integration
async def test_alert_without_manual_action_still_restores(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("evening", ActivationSource.USER)

    await execute_alert(
        hass, ctrl, AlertPattern(steps=[AlertStep(target="all", state="off")], restore=True)
    )

    assert ctrl._state.scene_slug == "evening"
    assert not ctrl._alert_active
    assert not ctrl._alert_cancels


# ── Lights-off detection during a scene swap ───────────────────────────


def _settle(ctrl) -> None:
    """Move the active scene's commands far enough into the past to settle."""
    for target in ctrl._active_scene_targets.values():
        target["commanded_at"] -= 60


@pytest.mark.integration
async def test_all_off_while_scene_lights_settle_waits_for_a_recheck(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Off reports that beat the on reports of a swap do not end the scene."""
    for eid in NETWORK_LIGHTS:
        hass.states.async_set(eid, "off")
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("evening", ActivationSource.USER)
    for eid in NETWORK_LIGHTS:
        hass.states.async_set(eid, "on")
    await hass.async_block_till_done()

    hass.states.async_set(NETWORK_LIGHTS[0], "off")
    hass.states.async_set(NETWORK_LIGHTS[1], "off")
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "evening"
    assert ctrl._lights_off_recheck_handle is not None

    # The lights really are off at the settle point.
    _settle(ctrl)
    ctrl._lights_off_recheck_handle.cancel()
    ctrl._run_lights_off_recheck()
    await hass.async_block_till_done()

    assert ctrl._state.is_off


@pytest.mark.integration
async def test_all_off_recheck_leaves_a_scene_whose_lights_came_on(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    for eid in NETWORK_LIGHTS:
        hass.states.async_set(eid, "off")
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("evening", ActivationSource.USER)
    hass.states.async_set(NETWORK_LIGHTS[0], "on")
    hass.states.async_set(NETWORK_LIGHTS[0], "off")
    await hass.async_block_till_done()
    assert ctrl._lights_off_recheck_handle is not None

    hass.states.async_set(NETWORK_LIGHTS[1], "on")
    _settle(ctrl)
    ctrl._lights_off_recheck_handle.cancel()
    ctrl._run_lights_off_recheck()
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "evening"


@pytest.mark.integration
async def test_all_off_after_lights_settled_ends_the_scene_at_once(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    for eid in NETWORK_LIGHTS:
        hass.states.async_set(eid, "on")
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("evening", ActivationSource.USER)
    _settle(ctrl)

    hass.states.async_set(NETWORK_LIGHTS[0], "off")
    hass.states.async_set(NETWORK_LIGHTS[1], "off")
    await hass.async_block_till_done()

    assert ctrl._state.is_off


@pytest.mark.integration
async def test_app_scene_swap_gets_the_settling_grace(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """A scene changed through scene.turn_on is protected like any other."""
    for eid in NETWORK_LIGHTS:
        hass.states.async_set(eid, "on")
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.network_room_evening"}, blocking=True
    )
    hass.states.async_set(NETWORK_LIGHTS[0], "off")
    hass.states.async_set(NETWORK_LIGHTS[1], "off")
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "evening"
    assert ctrl._lights_off_recheck_handle is not None


@pytest.mark.integration
@pytest.mark.parametrize("newer", ["daylight", "circadian"])
async def test_stale_recheck_leaves_a_newer_activation_alone(
    hass: HomeAssistant, helper_entities, network_room_config, newer
) -> None:
    """Circadian keeps no targets, so only the claim shows it is newer."""
    for eid in NETWORK_LIGHTS:
        hass.states.async_set(eid, "off")
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("evening", ActivationSource.USER)
    hass.states.async_set(NETWORK_LIGHTS[0], "on")
    hass.states.async_set(NETWORK_LIGHTS[0], "off")
    await hass.async_block_till_done()
    stale = ctrl._lights_off_recheck_handle
    assert stale is not None

    async def activate_newer():
        if newer == "circadian":
            await ctrl.lighting_circadian()
        else:
            await ctrl._activate_scene(newer, ActivationSource.USER)

    await activate_newer()
    _settle(ctrl)
    stale.cancel()
    ctrl._run_lights_off_recheck()
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == newer


@pytest.mark.integration
async def test_manual_action_while_an_alert_hands_back_takes_over_at_once(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("evening", ActivationSource.USER)

    handing_back = asyncio.Event()
    release = asyncio.Event()
    original_end = ctrl.async_end_alert

    async def slow_end(cancel=None):
        handing_back.set()
        await release.wait()
        await original_end(cancel)

    ctrl.async_end_alert = slow_end
    alert_task = hass.async_create_task(
        execute_alert(
            hass, ctrl, AlertPattern(steps=[AlertStep(target="all", state="off")], restore=True)
        )
    )
    await asyncio.wait_for(handing_back.wait(), 2)
    assert ctrl._alert_active

    await ctrl.lighting_circadian()
    assert not ctrl._alert_active

    release.set()
    await asyncio.wait_for(alert_task, 2)
    assert ctrl._state.is_circadian
    assert not ctrl._alert_cancels


@pytest.mark.integration
async def test_alert_hand_back_does_not_replay_over_a_manual_action(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The demand-response re-drive at the end of an alert stands down."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("evening", ActivationSource.USER)
    hass.data["area_lighting"]["global"]._demand_response_active = True

    redrives = 0

    async def count_redrive():
        nonlocal redrives
        redrives += 1

    ctrl.reactivate_for_demand_response = count_redrive
    # A circadian tick still in flight makes async_end_alert wait.
    tick = hass.loop.create_future()
    ctrl._circadian_tick_done = tick
    handing_back = asyncio.Event()
    original_end = ctrl.async_end_alert

    async def end_alert(cancel=None):
        handing_back.set()
        await original_end(cancel)

    ctrl.async_end_alert = end_alert

    alert_task = hass.async_create_task(
        execute_alert(
            hass, ctrl, AlertPattern(steps=[AlertStep(target="all", state="off")], restore=True)
        )
    )
    await asyncio.wait_for(handing_back.wait(), 2)
    await ctrl.lighting_force_off()
    tick.set_result(None)
    await asyncio.wait_for(alert_task, 2)

    assert redrives == 0
    assert ctrl._state.is_off
