"""Animated scenes follow shared clock boundaries and area ownership."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.const import DOMAIN

START = datetime(2026, 11, 20, 8, 0, 10, tzinfo=UTC)
RED = [0, 100]
GREEN = [120, 100]


def _area(area_id: str = "den", count: int = 2, **extra) -> dict:
    lights = [f"light.{area_id}_{i}" for i in range(count)]
    return {
        "id": area_id,
        "name": area_id.title(),
        "event_handlers": True,
        "lights": [{"id": light, "roles": ["color", "dimming"]} for light in lights],
        "scenes": [
            {
                "id": "christmas",
                "name": "Christmas",
                "animations": ["festive"],
                "entities": {
                    light: {"state": "on", "brightness": 200, "hs_color": [240, 100]}
                    for light in lights
                },
            },
            {"id": "off", "name": "Off"},
        ],
        **extra,
    }


def _config(*areas: dict, **extra) -> dict:
    return {
        DOMAIN: {
            "scene_self_heal": False,
            "animations": {
                "festive": {
                    "period": 30,
                    "transition": 5,
                    "palette": [{"hs_color": RED}, {"hs_color": GREEN}],
                }
            },
            "areas": list(areas) or [_area()],
            **extra,
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> dict:
    for area in cfg[DOMAIN]["areas"]:
        for light in area["lights"]:
            hass.states.async_set(light["id"], "off", {})
    assert await async_setup_component(hass, DOMAIN, copy.deepcopy(cfg))
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    return hass.data[DOMAIN]["controllers"]


async def _activate(hass: HomeAssistant, ctrl, scene_slug: str = "christmas") -> None:
    await ctrl._activate_scene(scene_slug, ActivationSource.USER)
    await _echo_targets(hass, ctrl)


async def _echo_targets(hass: HomeAssistant, ctrl) -> None:
    for entity_id, target in ctrl._active_scene_targets.items():
        hass.states.async_set(
            entity_id,
            target["state"],
            {key: value for key, value in target.items() if key in {"brightness", "hs_color"}},
        )
    await hass.async_block_till_done()


def _turn_ons(service_calls) -> dict[str, dict]:
    return {
        call.data["entity_id"]: dict(call.data)
        for call in service_calls
        if call.domain == "light" and call.service == "turn_on"
    }


async def _advance(hass: HomeAssistant, freezer, seconds: float) -> None:
    now = START + timedelta(seconds=seconds)
    freezer.move_to(now)
    async_fire_time_changed(hass, now)
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_activation_and_boundaries_send_current_step(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config()))["den"]
    service_calls.clear()

    await _activate(hass, ctrl)

    initial = _turn_ons(service_calls)
    assert initial["light.den_0"]["hs_color"] == RED
    assert initial["light.den_1"]["hs_color"] == GREEN
    assert {data["transition"] for data in initial.values()} == {0.5}
    service_calls.clear()
    await _advance(hass, freezer, 19)
    assert not service_calls

    await _advance(hass, freezer, 20)

    next_step = _turn_ons(service_calls)
    assert next_step["light.den_0"]["hs_color"] == GREEN
    assert next_step["light.den_1"]["hs_color"] == RED
    assert {data["transition"] for data in next_step.values()} == {5}
    assert ctrl.diagnostic_snapshot()["animation"] == {
        "name": "festive",
        "step": int((START.timestamp() + 20) // 30),
    }


@pytest.mark.integration
async def test_areas_starting_at_different_times_share_steps(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    controllers = await _setup(hass, _config(_area(), _area("hall")))
    await _activate(hass, controllers["den"])
    await _advance(hass, freezer, 10)
    await _activate(hass, controllers["hall"])
    service_calls.clear()

    await _advance(hass, freezer, 20)

    calls = _turn_ons(service_calls)
    assert set(calls) == {"light.den_0", "light.den_1", "light.hall_0", "light.hall_1"}
    for index in range(2):
        assert calls[f"light.den_{index}"]["hs_color"] == calls[f"light.hall_{index}"]["hs_color"]


@pytest.mark.integration
async def test_leader_drives_follower_with_current_animation_step(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    den = _area()
    hall = _area("hall", leader_area_id="den")
    for area in (den, hall):
        area["scenes"][0]["id"] = "party"
    hall["scenes"].append({"id": "reading", "name": "Reading"})
    controllers = await _setup(hass, _config(den, hall))
    await controllers["hall"]._activate_scene("reading", ActivationSource.USER)
    for index in range(2):
        hass.states.async_set(f"light.hall_{index}", "on", {"brightness": 200})
    await hass.async_block_till_done()
    service_calls.clear()

    await _activate(hass, controllers["den"], "party")

    calls = _turn_ons(service_calls)
    assert controllers["hall"].current_scene == "party"
    assert calls["light.hall_0"]["hs_color"] == RED
    assert calls["light.hall_1"]["hs_color"] == GREEN
    assert all(call.data.get("hs_color") != [240, 100] for call in service_calls)
    for index, color in enumerate((RED, GREEN)):
        hass.states.async_set(f"light.hall_{index}", "on", {"brightness": 200, "hs_color": color})
    await hass.async_block_till_done()
    service_calls.clear()
    await _advance(hass, freezer, 20)
    assert _turn_ons(service_calls)["light.hall_0"]["hs_color"] == GREEN


@pytest.mark.integration
async def test_ticks_only_command_animated_lights_and_stamp_their_targets(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    area = _area(count=5)
    entities = area["scenes"][0]["entities"]
    entities["light.den_2"] = {"state": "on", "brightness": 150, "color_temp_kelvin": 2700}
    entities["light.den_3"]["animate"] = False
    entities["light.den_4"]["state"] = "off"
    entities["light.den_zone"] = {"state": "on", "hs_color": RED}
    area["light_clusters"] = [{"id": "light.den_zone", "members": ["light.den_0", "light.den_1"]}]
    hass.states.async_set("light.den_zone", "off", {})
    ctrl = (await _setup(hass, _config(area)))["den"]
    await _activate(hass, ctrl)
    old_targets = copy.deepcopy(ctrl._active_scene_targets)
    service_calls.clear()
    with patch.object(ctrl, "_schedule_post_settle_selfcheck") as selfcheck:
        await _advance(hass, freezer, 20)

    assert set(_turn_ons(service_calls)) == {"light.den_0", "light.den_1"}
    for entity_id in ("light.den_0", "light.den_1"):
        assert ctrl._active_scene_targets[entity_id]["transition"] == 5
        assert (
            ctrl._active_scene_targets[entity_id]["commanded_at"]
            >= old_targets[entity_id]["commanded_at"]
        )
    for entity_id in ("light.den_2", "light.den_3", "light.den_4"):
        assert ctrl._active_scene_targets[entity_id].get("commanded_at") == old_targets[
            entity_id
        ].get("commanded_at")
    selfcheck.assert_not_called()


@pytest.mark.integration
async def test_persons_color_change_latches_manual_and_stops_ticks(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config(scene_self_heal=False)))["den"]
    await _activate(hass, ctrl)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 60
    for target in ctrl._active_scene_targets.values():
        target["commanded_at"] = time.monotonic() - 60

    hass.states.async_set("light.den_0", "on", {"brightness": 200, "hs_color": [240, 100]})
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert ctrl.diagnostic_snapshot()["animation"] is None
    service_calls.clear()
    await _advance(hass, freezer, 20)
    assert not service_calls


@pytest.mark.integration
async def test_demand_response_keeps_shed_bulbs_off_on_every_tick(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config(_area(count=6))))["den"]
    await _activate(hass, ctrl)
    await hass.data[DOMAIN]["global"].async_set_demand_response_active(True)
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset(f"light.den_{i}" for i in range(2, 6))
    for entity_id in ctrl.dr_shed_ids:
        hass.states.async_set(entity_id, "off", {})
    await hass.async_block_till_done()
    for seconds in (20, 50):
        service_calls.clear()
        await _advance(hass, freezer, seconds)
        assert set(_turn_ons(service_calls)) == {"light.den_0", "light.den_1"}
        assert all(hass.states.get(entity_id).state == "off" for entity_id in ctrl.dr_shed_ids)
        assert all(
            ctrl._active_scene_targets[entity_id]["state"] == "off"
            for entity_id in ctrl.dr_shed_ids
        )


@pytest.mark.integration
async def test_reload_restarts_running_animation_with_new_palette(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    cfg = _config()
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    cfg[DOMAIN]["animations"]["festive"]["palette"] = [{"hs_color": [40, 100]}]
    service_calls.clear()
    with patch(
        "custom_components.area_lighting.async_hass_config_yaml", AsyncMock(return_value=cfg)
    ):
        await hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
    await hass.async_block_till_done()

    assert ctrl.current_scene == "christmas"
    assert {tuple(data["hs_color"]) for data in _turn_ons(service_calls).values()} == {(40, 100)}
    service_calls.clear()
    await _advance(hass, freezer, 20)
    assert {tuple(data["hs_color"]) for data in _turn_ons(service_calls).values()} == {(40, 100)}


@pytest.mark.integration
@pytest.mark.parametrize("remove_animation", [False, True])
async def test_alert_pauses_animation_and_resumes_at_current_step(
    hass: HomeAssistant, helper_entities, service_calls, freezer, remove_animation: bool
) -> None:
    freezer.move_to(START)
    # The post-settle self-check runs on the loop's real clock, which the
    # freezer does not move, so a slow run could heal inside the counted window.
    cfg = _config(
        alert_patterns={"flash": {"steps": [{"target": "all", "state": "on", "brightness": 255}]}},
        scene_self_heal=False,
    )
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    entered, release = asyncio.Event(), asyncio.Event()
    recorded = []

    async def hold_flash(call) -> None:
        recorded.append(call)
        if call.data.get("brightness") == 255:
            entered.set()
            await release.wait()

    hass.services.async_register("light", "turn_on", hold_flash)
    alert_task = asyncio.create_task(
        hass.services.async_call(
            DOMAIN, "alert", {"area_id": "den", "pattern": "flash"}, blocking=True
        )
    )
    await entered.wait()
    try:
        assert ctrl.diagnostic_snapshot()["animation"] is None
        if remove_animation:
            cfg[DOMAIN]["areas"][0]["scenes"][0].pop("animations")
            with patch(
                "custom_components.area_lighting.async_hass_config_yaml",
                AsyncMock(return_value=cfg),
            ):
                await hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
            await hass.async_block_till_done()
        await _advance(hass, freezer, 20)
        assert ctrl._animation_unsub is None
    finally:
        release.set()
        await alert_task
    await hass.async_block_till_done()

    assert ctrl.current_scene == "christmas"
    if remove_animation:
        assert ctrl.diagnostic_snapshot()["animation"] is None
        restored = _turn_ons(recorded)
        assert set(restored) == {"light.den_0", "light.den_1"}
        assert all(data["hs_color"] == [240, 100] for data in restored.values())
        assert all(data["transition"] == 0.5 for data in restored.values())
    else:
        assert ctrl.diagnostic_snapshot()["animation"]["name"] == "festive"
    await _echo_targets(hass, ctrl)
    service_calls.clear()
    hass.services.async_register("light", "turn_on", lambda call: None)
    with patch.object(ctrl, "_call_service", AsyncMock()) as send:
        await _advance(hass, freezer, 50)
    assert send.await_count == (0 if remove_animation else 2)


@pytest.mark.integration
async def test_dimming_pauses_animation_and_lighting_on_resumes(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config()))["den"]
    await _activate(hass, ctrl)
    await hass.services.async_call(DOMAIN, "lighting_lower", {"area_id": "den"}, blocking=True)
    assert ctrl._state.dimmed
    assert ctrl.diagnostic_snapshot()["animation"] is None
    service_calls.clear()
    await _advance(hass, freezer, 20)
    assert not service_calls

    await hass.services.async_call(DOMAIN, "lighting_on", {"area_id": "den"}, blocking=True)
    await hass.async_block_till_done()

    assert not ctrl._state.dimmed
    assert _turn_ons(service_calls)["light.den_0"]["hs_color"] == GREEN
    service_calls.clear()
    await _advance(hass, freezer, 50)
    assert _turn_ons(service_calls)["light.den_0"]["hs_color"] == RED


@pytest.mark.integration
@pytest.mark.parametrize("demand_response", [False, True])
async def test_scene_entity_activation_sends_step_colors_without_static_flash(
    hass: HomeAssistant, helper_entities, service_calls, freezer, demand_response: bool
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config(_area(count=6 if demand_response else 2))))["den"]
    if demand_response:
        await hass.data[DOMAIN]["global"].async_set_demand_response_active(True)
    service_calls.clear()

    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.den_christmas"}, blocking=True
    )
    await hass.async_block_till_done()

    calls = _turn_ons(service_calls)
    assert set(calls) == {"light.den_0", "light.den_1"}
    assert calls["light.den_0"]["hs_color"] == RED
    assert calls["light.den_1"]["hs_color"] == GREEN
    assert all(call.data.get("hs_color") != [240, 100] for call in service_calls)
    assert ctrl.diagnostic_snapshot()["animation"]["name"] == "festive"
    if demand_response:
        shed = {f"light.den_{index}" for index in range(2, 6)}
        assert {
            call.data["entity_id"] for call in service_calls if call.service == "turn_off"
        } == shed
        assert ctrl.dr_shed_ids == frozenset(shed)


@pytest.mark.integration
async def test_snapshot_refuses_animated_scene_even_when_selection_is_off(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.area_lighting_christmas_animation", "option": "off"},
        blocking=True,
    )

    await hass.services.async_call(
        DOMAIN, "snapshot_scene", {"area_id": "den", "scene": "christmas"}, blocking=True
    )

    assert hass.data[DOMAIN]["scene_storage"].get_scene_data("den", "christmas") is None


@pytest.mark.integration
async def test_scene_that_gains_animation_overlays_its_existing_snapshot(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    cfg = _config()
    cfg[DOMAIN]["areas"][0]["scenes"][0].pop("animations")
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    hass.states.async_set("light.den_0", "on", {"brightness": 80, "hs_color": [240, 100]})
    await hass.services.async_call(
        DOMAIN, "snapshot_scene", {"area_id": "den", "scene": "christmas"}, blocking=True
    )
    stored = hass.data[DOMAIN]["scene_storage"].get_scene_data("den", "christmas")
    assert stored["light.den_0"]["brightness"] == 80
    cfg[DOMAIN]["areas"][0]["scenes"][0]["animations"] = ["festive"]
    service_calls.clear()

    with patch(
        "custom_components.area_lighting.async_hass_config_yaml", AsyncMock(return_value=cfg)
    ):
        await hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
    await hass.async_block_till_done()

    calls = _turn_ons(service_calls)
    assert calls["light.den_0"]["hs_color"] == RED
    assert calls["light.den_0"]["brightness"] == 80
    assert calls["light.den_1"]["hs_color"] == GREEN
    assert stored["light.den_0"]["hs_color"] == [240, 100]
    service_calls.clear()
    await _advance(hass, freezer, 20)
    assert _turn_ons(service_calls)["light.den_0"]["hs_color"] == GREEN


@pytest.mark.integration
async def test_tick_during_slow_send_is_skipped(
    hass: HomeAssistant, helper_entities, service_calls, freezer, caplog
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config()))["den"]
    await _activate(hass, ctrl)
    await _advance(hass, freezer, 19)
    entered, release, skipped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_tick = ctrl._animation_tick

    async def record_tick(generation: int) -> None:
        await original_tick(generation)
        if not release.is_set():
            skipped.set()

    async def slow_send(*args, **kwargs) -> None:
        entered.set()
        await release.wait()

    caplog.set_level(logging.DEBUG, logger="custom_components.area_lighting.controller")
    with (
        patch.object(ctrl, "_call_service", AsyncMock(side_effect=slow_send)) as send,
        patch.object(ctrl, "_animation_tick", AsyncMock(side_effect=record_tick)),
    ):
        boundary = START + timedelta(seconds=20)
        freezer.move_to(boundary)
        async_fire_time_changed(hass, boundary)
        await entered.wait()
        try:
            boundary += timedelta(seconds=30)
            freezer.move_to(boundary)
            async_fire_time_changed(hass, boundary)
            await skipped.wait()
            assert send.await_count == 2
            assert "skip" in caplog.text.lower()
        finally:
            release.set()
            await hass.async_block_till_done()
        assert send.await_count == 2


@pytest.mark.integration
async def test_stale_tick_after_off_does_nothing(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config()))["den"]
    await _activate(hass, ctrl)
    generation = ctrl._animation_generation
    await ctrl.lighting_force_off()
    service_calls.clear()

    await ctrl._animation_tick(generation)
    await _advance(hass, freezer, 20)

    assert not service_calls
    assert ctrl.diagnostic_snapshot()["animation"] is None
