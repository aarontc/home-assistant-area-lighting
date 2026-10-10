"""area_lighting.reload applies a new config to the running areas."""

from __future__ import annotations

import asyncio
import copy
import logging
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

import custom_components.area_lighting as area_lighting
from custom_components.area_lighting import alert
from custom_components.area_lighting.alert import execute_alert
from custom_components.area_lighting.area_state import ActivationSource, LeaderReason
from custom_components.area_lighting.circadian_clock import CircadianValues
from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.controller import AreaLightingController
from custom_components.area_lighting.diagnostics import AreaLightingDiagnosticSensor
from custom_components.area_lighting.models import AlertPattern, AlertStep, CircadianConfig

AREA = "den"
LIGHT_1 = "light.den_1"
LIGHT_2 = "light.den_2"
LIGHT_3 = "light.den_3"
MOTION = "binary_sensor.den_motion"
OCCUPANCY = "binary_sensor.den_occupancy"
SLEEP_ENTITY = "input_boolean.den_sleep"
REMOTE = "den_remote_device"


def _light(entity_id: str, group: str = "Main") -> dict:
    return {
        "id": entity_id,
        "circadian_group": group,
        "circadian_type": "ct",
        "roles": ["dimming", "white"],
    }


def _area(area_id: str = AREA, **extra) -> dict:
    return {
        "id": area_id,
        "name": area_id.title(),
        "event_handlers": True,
        "circadian_groups": [{"name": "Main"}],
        "lights": [_light(f"light.{area_id}_1"), _light(f"light.{area_id}_2")],
        "scenes": [
            {"id": "circadian", "name": "Circadian"},
            {"id": "daylight", "name": "Daylight"},
            {"id": "evening", "name": "Evening"},
            {"id": "off", "name": "Off"},
        ],
        **extra,
    }


def _config(*areas: dict, **extra) -> dict:
    return {DOMAIN: {"areas": list(areas) or [_area()], **extra}}


async def _setup(hass: HomeAssistant, cfg: dict) -> dict[str, AreaLightingController]:
    for area in cfg[DOMAIN]["areas"]:
        for light in area["lights"]:
            if hass.states.get(light["id"]) is None:
                hass.states.async_set(light["id"], "off", {})
    assert await async_setup_component(hass, DOMAIN, copy.deepcopy(cfg))
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    return hass.data[DOMAIN]["controllers"]


async def _reload(hass: HomeAssistant, cfg: dict) -> None:
    with patch(
        "custom_components.area_lighting.async_hass_config_yaml",
        AsyncMock(return_value=copy.deepcopy(cfg)),
    ):
        await hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
    await hass.async_block_till_done()


def _turn_ons(service_calls) -> dict[str, dict]:
    """entity_id -> data of the light.turn_on calls, without entity_id."""
    return {
        c.data["entity_id"]: {k: v for k, v in c.data.items() if k != "entity_id"}
        for c in service_calls
        if c.domain == "light" and c.service == "turn_on"
    }


def _both(payload: dict) -> dict[str, dict]:
    return {LIGHT_1: payload, LIGHT_2: payload}


def _turn_offs(service_calls) -> set[str]:
    return {c.data["entity_id"] for c in service_calls if c.service == "turn_off"}


def _set_on(hass: HomeAssistant, *entity_ids: str) -> None:
    for entity_id in entity_ids:
        hass.states.async_set(entity_id, "on", {"brightness": 100})


async def _circadian_with_lights_on(hass: HomeAssistant, ctrl, *entity_ids: str) -> None:
    await ctrl.lighting_circadian()
    _set_on(hass, *entity_ids)
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_reload_applies_a_new_light_list(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = (await _setup(hass, _config()))[AREA]
    await _circadian_with_lights_on(hass, ctrl, LIGHT_1, LIGHT_2)
    area = _area()
    area["lights"].append(_light(LIGHT_3))
    hass.states.async_set(LIGHT_3, "off", {})

    await _reload(hass, _config(area))
    service_calls.clear()
    _set_on(hass, LIGHT_3)
    await hass.async_block_till_done()

    assert [light.id for light in ctrl.area.lights] == [LIGHT_1, LIGHT_2, LIGHT_3]
    assert _turn_ons(service_calls) == {
        LIGHT_3: {"brightness": 255, "color_temp_kelvin": 4000, "transition": 1}
    }


@pytest.mark.integration
async def test_reload_applies_new_motion_sensors(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set(MOTION, "off")
    hass.states.async_set("binary_sensor.den_motion_2", "off")
    ctrl = (await _setup(hass, _config(_area(motion_light_motion_sensor_ids=[MOTION]))))[AREA]
    ctrl.motion_light_enabled = True

    await _reload(
        hass, _config(_area(motion_light_motion_sensor_ids=[MOTION, "binary_sensor.den_motion_2"]))
    )
    hass.states.async_set("binary_sensor.den_motion_2", "on")
    await hass.async_block_till_done()

    assert ctrl._state.source == ActivationSource.MOTION
    assert not ctrl._state.is_off


@pytest.mark.integration
async def test_reload_applies_new_remote_favorite_cycle(
    hass: HomeAssistant, helper_entities
) -> None:
    def remote(cycle: list[str]) -> dict:
        return {"id": REMOTE, "name": "Den Remote", "buttons": {"favorite": cycle}}

    ctrl = (await _setup(hass, _config(_area(lutron_remotes=[remote(["evening"])]))))[AREA]

    await _reload(hass, _config(_area(lutron_remotes=[remote(["daylight"])])))
    hass.bus.async_fire(
        "lutron_caseta_button_event",
        {"device_id": REMOTE, "button_type": "stop", "action": "press"},
    )
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "daylight"


@pytest.mark.integration
async def test_reload_applies_group_bounds_and_sleep_options(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    hass.states.async_set(SLEEP_ENTITY, "on")
    cfg = _config(
        _area(
            circadian_groups=[{"name": "Main"}, {"name": "Bed"}],
            lights=[_light(LIGHT_1), _light(LIGHT_2, "Bed")],
        )
    )
    ctrl = (await _setup(hass, cfg))[AREA]
    await _circadian_with_lights_on(hass, ctrl, LIGHT_1, LIGHT_2)
    groups = [
        {"name": "Main", "max_brightness": 50},
        {
            "name": "Bed",
            "sleep_entity": SLEEP_ENTITY,
            "sleep_state": "on",
            "sleep_brightness": 10,
            "sleep_colortemp": 2000,
        },
    ]
    cfg[DOMAIN]["areas"][0]["circadian_groups"] = groups
    service_calls.clear()

    await _reload(hass, cfg)

    assert _turn_ons(service_calls) == {
        LIGHT_1: {"brightness": 127, "color_temp_kelvin": 4000, "transition": 60},
        LIGHT_2: {"brightness": 26, "color_temp_kelvin": 2000, "transition": 60},
    }
    service_calls.clear()
    hass.states.async_set(SLEEP_ENTITY, "off")
    await hass.async_block_till_done()
    assert _turn_ons(service_calls) == {
        LIGHT_2: {"brightness": 255, "color_temp_kelvin": 4000, "transition": 1}
    }


@pytest.mark.integration
async def test_reload_applies_the_global_circadian_block(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = (await _setup(hass, _config()))[AREA]
    await _circadian_with_lights_on(hass, ctrl, LIGHT_1, LIGHT_2)
    old_clock = hass.data[DOMAIN]["circadian_clock"]
    block = {"min_colortemp": 2000, "max_colortemp": 6000, "interval": 120, "transition": 10}

    await _reload(hass, _config(_area(), circadian=block))
    await pin_circadian(60, 4500, tick=False)
    ctrl._circadian_commanded_at.clear()
    service_calls.clear()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=121))
    await hass.async_block_till_done()

    clock = hass.data[DOMAIN]["circadian_clock"]
    assert clock is not old_clock
    assert old_clock._unsub_interval is None
    assert clock.config == CircadianConfig(2000, 6000, 120, 10)
    assert _turn_ons(service_calls) == _both(
        {"brightness": 255, "color_temp_kelvin": 4500, "transition": 10}
    )
    assert hass.states.get("sensor.area_lighting_circadian").attributes["colortemp"] == 4500


@pytest.mark.integration
async def test_reload_applies_scene_self_heal(hass: HomeAssistant, helper_entities) -> None:
    ctrl = (await _setup(hass, _config()))[AREA]
    assert ctrl.scene_self_heal_enabled

    await _reload(hass, _config(_area(), scene_self_heal=False))

    assert not ctrl.scene_self_heal_enabled


@pytest.mark.integration
async def test_reload_disabling_scene_self_heal_stops_pending_heals(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    scenes = [
        {"id": "circadian", "name": "Circadian"},
        {"id": "daylight", "name": "Daylight", "entities": {LIGHT_1: {"state": "on"}}},
    ]
    ctrl = (await _setup(hass, _config(_area(scenes=scenes))))[AREA]
    await ctrl._activate_scene("daylight", ActivationSource.USER, transition=5)
    await hass.async_block_till_done()
    assert ctrl._heal_selfcheck_handle is not None

    await _reload(hass, _config(_area(scenes=scenes), scene_self_heal=False))
    hass.states.async_set(LIGHT_1, "off", {})
    hass.states.async_set(LIGHT_1, "on", {"brightness": 30})
    await hass.async_block_till_done()
    service_calls.clear()
    ctrl._run_post_settle_selfcheck()
    await ctrl.handle_scene_drift_reassert(LIGHT_1, "glitch_window")
    await hass.async_block_till_done()

    assert not service_calls


def _sensor_area(**extra) -> dict:
    return _area(
        motion_light_motion_sensor_ids=[MOTION],
        occupancy_light_sensor_ids=[OCCUPANCY],
        **extra,
    )


async def _evening_with_timers(hass: HomeAssistant) -> AreaLightingController:
    hass.states.async_set(MOTION, "off")
    hass.states.async_set(OCCUPANCY, "off")
    ctrl = (await _setup(hass, _config(_sensor_area())))[AREA]
    await ctrl._activate_scene("evening", ActivationSource.MOTION)
    ctrl._motion_timer.start()
    await hass.async_block_till_done()
    assert ctrl._motion_timer.is_active
    assert ctrl._occupancy_timer.is_active
    return ctrl


@pytest.mark.integration
async def test_reload_keeps_state_dimmed_flag_and_timer_deadlines(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    ctrl = await _evening_with_timers(hass)
    ctrl._state.mark_dimmed()
    deadlines = (ctrl._motion_timer.deadline_utc, ctrl._occupancy_timer.deadline_utc)
    service_calls.clear()

    await _reload(hass, _config(_sensor_area()))

    assert ctrl.current_scene == "evening"
    assert ctrl.dimmed
    assert (ctrl._motion_timer.deadline_utc, ctrl._occupancy_timer.deadline_utc) == deadlines
    assert not service_calls


@pytest.mark.integration
@pytest.mark.parametrize(
    "sensors",
    [{}, {"motion_light_motion_sensor_ids": [], "occupancy_light_sensor_ids": []}],
    ids=["omitted", "empty"],
)
async def test_reload_cancels_timers_for_removed_sensors(
    hass: HomeAssistant, helper_entities, sensors: dict
) -> None:
    ctrl = await _evening_with_timers(hass)

    await _reload(hass, _config(_area(**sensors)))

    assert not ctrl._motion_timer.is_active
    assert not ctrl._occupancy_timer.is_active
    assert ctrl.current_scene == "evening"


@pytest.mark.integration
async def test_reload_cancels_occupancy_countdown_when_a_new_sensor_is_on(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    ctrl = await _evening_with_timers(hass)
    hass.states.async_set("binary_sensor.den_occupancy_2", "on")
    service_calls.clear()

    await _reload(
        hass,
        _config(
            _area(
                motion_light_motion_sensor_ids=[MOTION],
                occupancy_light_sensor_ids=[OCCUPANCY, "binary_sensor.den_occupancy_2"],
            )
        ),
    )

    assert not ctrl._occupancy_timer.is_active
    assert ctrl._motion_timer.is_active
    assert not service_calls


@pytest.mark.integration
async def test_reload_cancels_motion_countdown_when_a_new_sensor_is_on(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    ctrl = await _evening_with_timers(hass)
    hass.states.async_set("binary_sensor.den_motion_2", "on")
    service_calls.clear()

    await _reload(
        hass,
        _config(
            _area(
                motion_light_motion_sensor_ids=[MOTION, "binary_sensor.den_motion_2"],
                occupancy_light_sensor_ids=[OCCUPANCY],
            )
        ),
    )

    assert not ctrl._motion_timer.is_active
    assert ctrl._occupancy_timer.is_active
    assert not service_calls


@pytest.mark.integration
async def test_reload_hears_a_sensor_that_turns_on_while_lights_are_commanded(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    hass.states.async_set(OCCUPANCY, "off")
    cfg = _config(_area(occupancy_light_sensor_ids=[OCCUPANCY]))
    ctrl = (await _setup(hass, cfg))[AREA]
    await _circadian_with_lights_on(hass, ctrl, LIGHT_1, LIGHT_2)
    assert ctrl._occupancy_timer.is_active
    commanding, release = asyncio.Event(), asyncio.Event()

    async def paused_turn_on(_call) -> None:
        commanding.set()
        await release.wait()

    hass.services.async_register("light", "turn_on", paused_turn_on)
    with patch(
        "custom_components.area_lighting.async_hass_config_yaml",
        AsyncMock(return_value=copy.deepcopy(cfg)),
    ):
        reload = asyncio.ensure_future(
            hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
        )
        await asyncio.wait_for(commanding.wait(), 5)
        hass.states.async_set(OCCUPANCY, "on")
        await hass.async_block_till_done()
        release.set()
        await reload
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_reload_recomputes_circadian_shed_set_without_routes(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ids = [f"light.den_{i}" for i in range(1, 6)]
    ctrl = (await _setup(hass, _config(_area(lights=[_light(i) for i in ids[:4]]))))[AREA]
    hass.data[DOMAIN]["global"]._demand_response_active = True
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset(ids[2:4])
    _set_on(hass, ids[0], ids[1], ids[4])
    hass.states.async_set(ids[2], "off", {})
    hass.states.async_set(ids[3], "off", {})
    await hass.async_block_till_done()
    service_calls.clear()

    await _reload(hass, _config(_area(lights=[_light(i) for i in ids])))

    # Five lights keep three: den_3 comes back, den_5 is shed.
    assert ctrl.dr_shed_ids == frozenset(ids[3:])
    assert ids[2] in _turn_ons(service_calls)
    assert _turn_offs(service_calls) == {ids[4]}


@pytest.mark.integration
async def test_reload_during_alert_resumes_circadian_after_the_alert(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian, monkeypatch
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = (await _setup(hass, _config()))[AREA]
    await _circadian_with_lights_on(hass, ctrl, LIGHT_1, LIGHT_2)
    new_cfg = _config(_area(circadian_groups=[{"name": "Main", "max_brightness": 50}]))
    running_during_alert = []

    async def steps(*_args) -> None:
        await _reload(hass, new_cfg)
        running_during_alert.append(ctrl.circadian_running)

    monkeypatch.setattr(alert, "_execute_steps", steps)
    service_calls.clear()
    await execute_alert(
        hass, ctrl, AlertPattern(steps=[AlertStep(target="all", state="on")], restore=False)
    )
    await hass.async_block_till_done()

    assert running_during_alert == [False]
    assert ctrl.circadian_running
    assert _turn_ons(service_calls) == _both(
        {"brightness": 127, "color_temp_kelvin": 4000, "transition": 60}
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("new_area", "motion_active", "occupancy_active"),
    [
        (_area(), False, False),
        (
            _area(
                motion_light_motion_sensor_ids=[MOTION],
                occupancy_light_sensor_ids=["binary_sensor.den_occupancy_2"],
            ),
            True,
            False,
        ),
    ],
    ids=["sensors_removed", "occupancy_replaced_by_one_that_is_on"],
)
async def test_alert_does_not_restore_timers_a_reload_made_stale(
    hass: HomeAssistant,
    helper_entities,
    monkeypatch,
    new_area: dict,
    motion_active: bool,
    occupancy_active: bool,
) -> None:
    ctrl = await _evening_with_timers(hass)
    hass.states.async_set("binary_sensor.den_occupancy_2", "on")

    async def steps(*_args) -> None:
        await _reload(hass, _config(new_area))

    monkeypatch.setattr(alert, "_execute_steps", steps)
    await execute_alert(
        hass, ctrl, AlertPattern(steps=[AlertStep(target="all", state="on")], restore=False)
    )

    assert ctrl._motion_timer.is_active is motion_active
    assert ctrl._occupancy_timer.is_active is occupancy_active


@pytest.mark.integration
async def test_alert_keeps_a_countdown_started_during_it(
    hass: HomeAssistant, helper_entities, monkeypatch
) -> None:
    ctrl = await _evening_with_timers(hass)
    saved = ctrl._occupancy_timer.deadline_utc
    hass.states.async_set("binary_sensor.den_occupancy_2", "on")
    fresh = []

    async def steps(*_args) -> None:
        await _reload(
            hass,
            _config(
                _area(
                    motion_light_motion_sensor_ids=[MOTION],
                    occupancy_light_sensor_ids=["binary_sensor.den_occupancy_2"],
                )
            ),
        )
        assert not ctrl._occupancy_timer.is_active
        hass.states.async_set("binary_sensor.den_occupancy_2", "off")
        await hass.async_block_till_done()
        fresh.append(ctrl._occupancy_timer.deadline_utc)

    monkeypatch.setattr(alert, "_execute_steps", steps)
    await execute_alert(
        hass, ctrl, AlertPattern(steps=[AlertStep(target="all", state="on")], restore=False)
    )

    assert fresh[0] is not None
    assert fresh[0] != saved
    assert ctrl._occupancy_timer.deadline_utc == fresh[0]


@pytest.mark.integration
async def test_reload_during_a_tick_applies_the_new_clock_values(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = (await _setup(hass, _config()))[AREA]
    await _circadian_with_lights_on(hass, ctrl, LIGHT_1, LIGHT_2)
    ctrl._circadian_commanded_at.clear()
    syncing, release = asyncio.Event(), asyncio.Event()
    sync = ctrl._sync_kelvin_router

    async def paused_sync() -> None:
        ctrl._sync_kelvin_router = sync
        syncing.set()
        await release.wait()

    ctrl._sync_kelvin_router = paused_sync
    old_tick = asyncio.ensure_future(ctrl._circadian_tick(CircadianValues(50, 4000)))
    await asyncio.wait_for(syncing.wait(), 5)
    await pin_circadian(60, 4500, tick=False)
    service_calls.clear()

    await _reload(hass, _config(_area(), circadian={"min_colortemp": 2000}))
    release.set()
    await old_tick
    await hass.async_block_till_done()

    turn_ons = [c.data for c in service_calls if c.service == "turn_on"]
    assert {data["entity_id"] for data in turn_ons} == {LIGHT_1, LIGHT_2}
    assert {data["color_temp_kelvin"] for data in turn_ons} == {4500}


def _leader_config(follower_leader: str | None) -> dict:
    follower = _area("closet")
    if follower_leader is not None:
        follower["leader_area_id"] = follower_leader
    return _config(_area("bath"), _area("hall"), follower)


@pytest.mark.integration
async def test_leader_notification_queued_before_rewiring_is_ignored(
    hass: HomeAssistant, helper_entities
) -> None:
    ctrls = await _setup(hass, _leader_config("bath"))
    closet = ctrls["closet"]
    await closet.lighting_circadian()
    queued = closet.handle_leader_change(ctrls["bath"], "evening", LeaderReason.SCENE_ACTIVATED)

    await _reload(hass, _leader_config("hall"))
    await queued

    assert closet.leader is ctrls["hall"]
    assert closet.current_scene == "circadian"


@pytest.mark.integration
async def test_leader_notification_queued_before_removal_is_ignored(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    ctrls = await _setup(hass, _leader_config("bath"))
    bath, closet = ctrls["bath"], ctrls["closet"]
    await closet.lighting_circadian()
    queued = closet.handle_leader_change(bath, "evening", LeaderReason.SCENE_ACTIVATED)

    await _reload(hass, _config(_area("bath"), _area("hall")))
    service_calls.clear()
    await queued

    assert closet.leader is None
    assert closet.followers == []
    assert closet.current_scene == "circadian"
    assert not service_calls


@pytest.mark.integration
@pytest.mark.parametrize(
    ("slug", "reason"),
    [("evening", LeaderReason.SCENE_ACTIVATED), (None, LeaderReason.OFF)],
    ids=["scene", "off"],
)
async def test_follower_closed_while_its_scene_commands_are_queued_sends_none(
    hass: HomeAssistant, helper_entities, service_calls, slug: str | None, reason: LeaderReason
) -> None:
    cfg = _leader_config("bath")
    cfg[DOMAIN]["areas"][2]["follow_leader_deactivation"] = True
    ctrls = await _setup(hass, cfg)
    bath, closet = ctrls["bath"], ctrls["closet"]
    await closet.lighting_circadian()
    await hass.async_block_till_done()
    service_calls.clear()
    listener_calls = []
    closet.add_state_listener(lambda: listener_calls.append(closet.current_scene))
    following = asyncio.ensure_future(closet.handle_leader_change(bath, slug, reason))
    await asyncio.sleep(0)

    closet.shutdown()
    await following

    assert closet.current_scene == "circadian"
    assert not service_calls
    assert listener_calls == []


@pytest.mark.integration
async def test_tick_sends_queued_before_an_adjuster_restart_are_dropped(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = (await _setup(hass, _config()))[AREA]
    await _circadian_with_lights_on(hass, ctrl, LIGHT_1, LIGHT_2)
    ctrl._circadian_commanded_at.clear()
    service_calls.clear()
    tick = asyncio.ensure_future(ctrl._circadian_tick(CircadianValues(50, 4000)))
    await asyncio.sleep(0)

    ctrl._stop_circadian()
    ctrl._start_circadian()
    await tick

    assert not service_calls


@pytest.mark.integration
async def test_reload_resumes_circadian_with_new_groups(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = (await _setup(hass, _config()))[AREA]
    await _circadian_with_lights_on(hass, ctrl, LIGHT_1, LIGHT_2)
    area = _area(
        circadian_groups=[{"name": "Accent", "max_brightness": 20}],
        lights=[_light(LIGHT_1, "Accent"), _light(LIGHT_2, "Accent")],
    )
    service_calls.clear()

    await _reload(hass, _config(area))
    assert ctrl.circadian_running
    assert _turn_ons(service_calls) == _both(
        {"brightness": 51, "color_temp_kelvin": 4000, "transition": 60}
    )

    service_calls.clear()
    ctrl._circadian_commanded_at.clear()
    await pin_circadian(60, 4500)
    assert _turn_ons(service_calls) == _both(
        {"brightness": 51, "color_temp_kelvin": 4500, "transition": 60}
    )


@pytest.mark.integration
async def test_reload_rewires_leader_and_followers(hass: HomeAssistant, helper_entities) -> None:
    ctrls = await _setup(hass, _leader_config("bath"))
    bath, hall, closet = ctrls["bath"], ctrls["hall"], ctrls["closet"]

    await _reload(hass, _leader_config("bath"))
    assert closet.leader is bath
    assert bath.followers == [closet]

    await _reload(hass, _leader_config("hall"))
    assert closet.leader is hall
    assert bath.followers == []
    assert hall.followers == [closet]

    await _reload(hass, _leader_config(None))
    assert closet.leader is None
    assert hall.followers == []


@pytest.mark.integration
async def test_reload_rebuilds_scene_targets_for_the_active_scene(
    hass: HomeAssistant, helper_entities
) -> None:
    def daylight(brightness: int) -> dict:
        return {
            "id": "daylight",
            "name": "Daylight",
            "entities": {
                LIGHT_1: {"state": "on", "brightness": brightness},
                LIGHT_2: {"state": "on", "brightness": brightness},
            },
        }

    def cfg(brightness: int) -> dict:
        return _config(
            _area(scenes=[{"id": "circadian", "name": "Circadian"}, daylight(brightness)])
        )

    ctrl = (await _setup(hass, cfg(100)))[AREA]
    await ctrl._activate_scene("daylight", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._active_scene_targets[LIGHT_1]["brightness"] == 100

    await _reload(hass, cfg(200))

    assert {eid: t["brightness"] for eid, t in ctrl._active_scene_targets.items()} == {
        LIGHT_1: 200,
        LIGHT_2: 200,
    }
    assert "commanded_at" in ctrl._active_scene_targets[LIGHT_1]


@pytest.mark.integration
async def test_invalid_reload_raises_and_keeps_the_running_config(
    hass: HomeAssistant, helper_entities
) -> None:
    ctrl = (await _setup(hass, _config()))[AREA]
    data = hass.data[DOMAIN]
    before = (data["config"], data["circadian_clock"], list(data["unsubs"]), ctrl.area)
    unknown_key = _config(_area(), not_a_key=True)
    unknown_group = _config(_area(lights=[_light(LIGHT_1, "Missing")]))

    for cfg in (unknown_key, unknown_group, {}):
        with pytest.raises(HomeAssistantError):
            await _reload(hass, cfg)

    assert (data["config"], data["circadian_clock"], data["unsubs"], ctrl.area) == before


@pytest.mark.integration
async def test_reload_emits_no_select_state_change(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    changes = []
    hass.bus.async_listen(EVENT_STATE_CHANGED, changes.append)

    await _reload(hass, network_room_config)

    assert [e for e in changes if e.data["entity_id"].startswith("select.")] == []


@pytest.mark.integration
async def test_reload_reruns_external_entity_validation(
    hass: HomeAssistant, helper_entities
) -> None:
    await _setup(hass, _config(_area(ambient_lighting_zone="upstairs")))
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, "missing_external_entities") is None

    await _reload(hass, _config(_area(ambient_lighting_zone="attic")))
    assert issues.async_get_issue(DOMAIN, "missing_external_entities") is not None

    await _reload(hass, _config(_area(ambient_lighting_zone="upstairs")))
    assert issues.async_get_issue(DOMAIN, "missing_external_entities") is None


def _lights_off(hass: HomeAssistant, area_id: str) -> None:
    for i in (1, 2):
        hass.states.async_set(f"light.{area_id}_{i}", "off", {})


def _removed(hass: HomeAssistant, entity_id: str) -> bool:
    """Home Assistant restores a removed entity that keeps its registry entry
    as unavailable."""
    state = hass.states.get(entity_id)
    return state is not None and state.state == "unavailable" and state.attributes["restored"]


def _diagnostics(hass: HomeAssistant) -> AreaLightingDiagnosticSensor:
    return next(
        e
        for e in hass.data[DOMAIN]["entities"][area_lighting.GLOBAL_ENTITIES]
        if isinstance(e, AreaLightingDiagnosticSensor)
    )


def _ha_area_name(hass: HomeAssistant, entity_id: str) -> str | None:
    entry = er.async_get(hass).async_get(entity_id)
    area = ar.async_get(hass).async_get_area(entry.area_id) if entry and entry.area_id else None
    return area.name if area else None


@pytest.mark.integration
async def test_reload_adds_an_area_with_its_entities(hass: HomeAssistant, helper_entities) -> None:
    await _setup(hass, _config())
    _lights_off(hass, "hall")

    await _reload(hass, _config(_area(), _area("hall")))

    hall = hass.data[DOMAIN]["controllers"]["hall"]
    for entity_id in (
        "scene.hall_daylight",
        "scene.hall_circadian",
        "select.hall_last_scene",
        "switch.hall_night_mode",
        "number.hall_motion_timeout_minutes",
        "binary_sensor.hall_occupied",
    ):
        assert hass.states.get(entity_id) is not None, entity_id
        assert _ha_area_name(hass, entity_id) == "Hall", entity_id
    assert hass.data[DOMAIN]["entities"]["hall"]
    await hall.lighting_circadian()
    await hass.async_block_till_done()
    assert hass.states.get("select.hall_last_scene").state == "circadian"
    assert hall in _diagnostics(hass)._controllers


@pytest.mark.integration
async def test_reload_removes_an_area_and_keeps_registry_entries(
    hass: HomeAssistant, helper_entities
) -> None:
    _lights_off(hass, "hall")
    ctrls = await _setup(hass, _config(_area(), _area("hall")))
    hall = ctrls["hall"]

    await _reload(hass, _config())

    assert "hall" not in hass.data[DOMAIN]["controllers"]
    assert "hall" not in hass.data[DOMAIN]["entities"]
    assert hall.closed
    assert hall._state_listeners == []
    assert hall not in _diagnostics(hass)._controllers
    registry = er.async_get(hass)
    for entity_id in ("scene.hall_daylight", "select.hall_last_scene", "switch.hall_night_mode"):
        assert _removed(hass, entity_id), entity_id
        assert registry.async_get(entity_id) is not None, entity_id


@pytest.mark.integration
async def test_reload_treats_a_changed_area_id_as_remove_and_add(
    hass: HomeAssistant, helper_entities
) -> None:
    den = (await _setup(hass, _config()))[AREA]
    _lights_off(hass, "study")

    await _reload(hass, _config(_area("study")))

    assert set(hass.data[DOMAIN]["controllers"]) == {"study"}
    assert den.closed
    assert _removed(hass, "select.den_last_scene")
    assert hass.states.get("select.study_last_scene").state == "off"
    assert hass.states.get("scene.study_daylight") is not None


@pytest.mark.integration
async def test_reload_adds_and_removes_scene_entities(hass: HomeAssistant, helper_entities) -> None:
    await _setup(hass, _config())
    entities = hass.data[DOMAIN]["entities"][AREA]
    daylight = next(e for e in entities if e.entity_id == "scene.den_daylight")
    scenes = [
        {"id": "circadian", "name": "Circadian"},
        {"id": "daylight", "name": "Daylight"},
        {"id": "movie", "name": "Movie"},
        {"id": "off", "name": "Off"},
    ]

    await _reload(hass, _config(_area(scenes=scenes)))

    entities = hass.data[DOMAIN]["entities"][AREA]
    assert _removed(hass, "scene.den_evening")
    assert er.async_get(hass).async_get("scene.den_evening") is not None
    assert hass.states.get("scene.den_movie") is not None
    assert daylight in entities
    assert [e.entity_id for e in entities].count("scene.den_movie") == 1
    assert "scene.den_evening" not in [e.entity_id for e in entities]
    options = hass.states.get("select.den_last_scene").attributes["options"]
    assert "movie" in options
    assert "evening" not in options


async def _evening_with_timers_beside_hall(hass: HomeAssistant) -> AreaLightingController:
    hass.states.async_set(MOTION, "off")
    hass.states.async_set(OCCUPANCY, "off")
    _lights_off(hass, "hall")
    ctrl = (await _setup(hass, _config(_sensor_area(), _area("hall"))))[AREA]
    await ctrl._activate_scene("evening", ActivationSource.MOTION)
    ctrl._motion_timer.start()
    await hass.async_block_till_done()
    assert ctrl._motion_timer.is_active
    assert ctrl._occupancy_timer.is_active
    return ctrl


@pytest.mark.integration
async def test_removed_area_controller_is_closed(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    ctrl = await _evening_with_timers_beside_hall(hass)

    await _reload(hass, _config(_area("hall")))
    storage = hass.data[DOMAIN]["state_storage"]
    service_calls.clear()
    with patch.object(storage, "async_save_area_state", AsyncMock()) as save:
        ctrl._notify_state_change()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=2))
        await hass.async_block_till_done()

    assert ctrl.closed
    assert not ctrl._motion_timer.is_active
    assert not ctrl._occupancy_timer.is_active
    assert not service_calls
    save.assert_not_called()


@pytest.mark.integration
async def test_removed_area_state_is_saved_without_timer_deadlines(
    hass: HomeAssistant, helper_entities
) -> None:
    ctrl = await _evening_with_timers_beside_hall(hass)
    ctrl.night_mode = True
    await hass.async_block_till_done()

    await _reload(hass, _config(_area("hall")))

    saved = hass.data[DOMAIN]["state_storage"].get_area_state(AREA)
    assert saved["timer_deadlines"] == {}
    assert saved["area_state"]["scene_slug"] == "evening"
    assert saved["night_mode"] is True


@pytest.mark.integration
async def test_readded_area_restores_persisted_state(hass: HomeAssistant, helper_entities) -> None:
    old = await _evening_with_timers_beside_hall(hass)
    old.night_mode = True
    await hass.async_block_till_done()

    await _reload(hass, _config(_area("hall")))
    await _reload(hass, _config(_sensor_area(), _area("hall")))

    ctrl = hass.data[DOMAIN]["controllers"][AREA]
    assert ctrl is not old
    assert ctrl.current_scene == "evening"
    assert ctrl.night_mode
    assert not ctrl._motion_timer.is_active
    assert ctrl._active_scene_targets.keys() == {LIGHT_1, LIGHT_2}
    assert hass.states.get("select.den_last_scene").state == "evening"
    assert hass.states.get("switch.den_night_mode").state == "on"


@pytest.mark.integration
async def test_reload_disabling_an_area_removes_it(hass: HomeAssistant, helper_entities) -> None:
    _lights_off(hass, "hall")
    hall = (await _setup(hass, _config(_area(), _area("hall"))))["hall"]

    await _reload(hass, _config(_area(), _area("hall", enabled=False)))

    assert set(hass.data[DOMAIN]["controllers"]) == {AREA}
    assert hall.closed
    assert _removed(hass, "select.hall_last_scene")


@pytest.mark.integration
async def test_reload_before_start_adds_area_entities_once(
    hass: HomeAssistant, helper_entities, caplog: pytest.LogCaptureFixture
) -> None:
    hass.states.async_set(LIGHT_1, "off", {})
    hass.states.async_set(LIGHT_2, "off", {})
    _lights_off(hass, "hall")
    assert await async_setup_component(hass, DOMAIN, _config())
    await hass.async_block_till_done()

    await _reload(hass, _config(_area(), _area("hall")))
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    entity_ids = [e.entity_id for e in hass.data[DOMAIN]["entities"]["hall"]]
    assert "select.hall_last_scene" in entity_ids
    assert len(entity_ids) == len(set(entity_ids))
    assert [r.message for r in caplog.records if r.levelno >= logging.ERROR] == []


@pytest.mark.integration
async def test_reload_during_startup_adds_area_entities_once(
    hass: HomeAssistant, helper_entities, caplog: pytest.LogCaptureFixture
) -> None:
    hass.states.async_set(LIGHT_1, "off", {})
    hass.states.async_set(LIGHT_2, "off", {})
    _lights_off(hass, "hall")
    assert await async_setup_component(hass, DOMAIN, _config())
    await hass.async_block_till_done()
    entered, release = asyncio.Event(), asyncio.Event()
    register_sensors = area_lighting._register_sensors

    async def stalled_register_sensors(hass: HomeAssistant) -> None:
        entered.set()
        await release.wait()
        await register_sensors(hass)

    with patch.object(area_lighting, "_register_sensors", stalled_register_sensors):
        hass.bus.async_fire("homeassistant_started")
        await entered.wait()
        reload = asyncio.ensure_future(_reload(hass, _config(_area(), _area("hall"))))
        await asyncio.sleep(0)
        release.set()
        await reload

    assert "select.hall_last_scene" in [e.entity_id for e in hass.data[DOMAIN]["entities"]["hall"]]
    assert [r.message for r in caplog.records if r.levelno >= logging.ERROR] == []


@pytest.mark.integration
async def test_reload_adds_a_scene_sharing_a_helper_unique_id(
    hass: HomeAssistant, helper_entities
) -> None:
    await _setup(hass, _config())
    scenes = [*_area()["scenes"], {"id": "night_mode", "name": "Night Mode"}]

    await _reload(hass, _config(_area(scenes=scenes)))

    assert hass.states.get("scene.den_night_mode") is not None
    assert hass.states.get("switch.den_night_mode") is not None


@pytest.mark.integration
async def test_reload_enabling_the_first_area_keeps_global_switches(
    hass: HomeAssistant, helper_entities, caplog: pytest.LogCaptureFixture
) -> None:
    await _setup(hass, _config(_area(enabled=False)))
    assert hass.states.get("switch.area_lighting_motion_lights_enabled") is not None
    assert "not loaded" not in caplog.text

    await _reload(hass, _config())

    assert hass.states.get("select.den_last_scene") is not None
    assert hass.states.get("switch.area_lighting_motion_lights_enabled") is not None
