"""Animation activation transitions, scheduling and target publication."""

import copy
import time
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.const import DOMAIN, MANUAL_DETECTION_GRACE_SECONDS
from custom_components.area_lighting.controller import AreaLightingController

from .test_animation import START, _activate, _advance, _area, _config, _setup, _turn_ons


@pytest.mark.integration
@pytest.mark.parametrize("transition", [None, 1.25])
@pytest.mark.parametrize("path", ["controller", "scene", "apply"])
async def test_activation_keeps_its_transition_for_all_lights(
    hass: HomeAssistant, helper_entities, service_calls, freezer, transition, path
) -> None:
    freezer.move_to(START)
    area = _area(count=3)
    entities = area["scenes"][0]["entities"]
    entities["light.den_1"]["animate"] = False
    entities["light.den_2"] = {"state": "on", "color_temp_kelvin": 2700}
    ctrl = (await _setup(hass, _config(area)))["den"]
    service_calls.clear()

    if path == "controller":
        await ctrl._activate_scene("christmas", ActivationSource.MOTION, transition)
    elif path == "scene":
        data = {"entity_id": "scene.den_christmas"}
        if transition is not None:
            data["transition"] = transition
        await hass.services.async_call("scene", "turn_on", data, blocking=True)
    else:
        await ctrl._apply_scene_data("christmas", transition)
    await hass.async_block_till_done()

    expected = 0.5 if transition is None else transition
    assert set(_turn_ons(service_calls)) == set(entities)
    assert {data["transition"] for data in _turn_ons(service_calls).values()} == {expected}
    if path != "apply":
        assert {target["transition"] for target in ctrl._active_scene_targets.values()} == {
            expected
        }


@pytest.mark.integration
@pytest.mark.parametrize("snapshot", [False, True])
@pytest.mark.parametrize("path", ["controller", "scene"])
async def test_activation_never_commands_cluster_with_selected_animation(
    hass: HomeAssistant, helper_entities, service_calls, freezer, snapshot, path
) -> None:
    freezer.move_to(START)
    area = _area()
    area["light_clusters"] = [{"id": "light.den_zone", "members": ["light.den_0", "light.den_1"]}]
    area["scenes"][0]["entities"]["light.den_zone"] = {
        "state": "on",
        "hs_color": [240, 100],
    }
    ctrl = (await _setup(hass, _config(area)))["den"]
    if snapshot:
        storage = hass.data[DOMAIN]["scene_storage"]
        storage._data = {"den": {"christmas": copy.deepcopy(area["scenes"][0]["entities"])}}
    service_calls.clear()

    if path == "controller":
        await ctrl._activate_scene("christmas", ActivationSource.USER)
    else:
        await hass.services.async_call(
            "scene", "turn_on", {"entity_id": "scene.den_christmas"}, blocking=True
        )
    await hass.async_block_till_done()

    assert set(_turn_ons(service_calls)) == {"light.den_0", "light.den_1"}
    assert all(call.data["entity_id"] != "light.den_zone" for call in service_calls)


@pytest.mark.integration
async def test_tick_publishes_stamped_targets_before_senders_start(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config()))["den"]
    await _activate(hass, ctrl)
    previous = copy.deepcopy(ctrl._active_scene_targets)
    freezer.move_to(START + timedelta(seconds=20))
    observed = []

    async def inspect_targets(entity_id, transition, generation):
        observed.append(copy.deepcopy(ctrl._active_scene_targets))

    with patch.object(ctrl, "_send_animation_light", side_effect=inspect_targets) as send:
        await ctrl._animation_tick(ctrl._animation_generation)

    assert send.await_count == 2
    for targets in observed:
        for eid, target in targets.items():
            assert target["commanded_at"] == time.monotonic()
            assert target["transition"] == 5
            assert target["animation_tick"] is True
            assert target["animation_from"] == {
                key: previous[eid][key] for key in ("state", "brightness", "hs_color")
            }


@pytest.mark.integration
async def test_slow_activation_runs_missed_boundary_immediately(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config()))["den"]
    apply = ctrl._apply_scene_data

    async def slow_apply(*args):
        await apply(*args)
        freezer.move_to(START + timedelta(seconds=25))

    with patch.object(ctrl, "_apply_scene_data", side_effect=slow_apply):
        await ctrl._activate_scene("christmas", ActivationSource.USER)
    service_calls.clear()
    await hass.async_block_till_done()

    assert _turn_ons(service_calls)["light.den_0"]["hs_color"] == [120, 100]
    assert {data["transition"] for data in _turn_ons(service_calls).values()} == {5}


@pytest.mark.integration
async def test_last_scene_select_starts_animation_ticks(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config()))["den"]
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.den_last_scene", "option": "christmas"},
        blocking=True,
    )
    service_calls.clear()

    await _advance(hass, freezer, 20)

    assert ctrl.diagnostic_snapshot()["animation"]["name"] == "festive"
    assert _turn_ons(service_calls)["light.den_0"]["hs_color"] == [120, 100]


@pytest.mark.integration
async def test_activation_selfcheck_skips_tick_targets_until_they_settle(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config(scene_self_heal=True)))["den"]
    with patch.object(ctrl, "_schedule_post_settle_selfcheck"):
        await _activate(hass, ctrl)
    await _advance(hass, freezer, 20)
    with patch.object(ctrl, "handle_scene_drift_reassert", AsyncMock()) as heal:
        ctrl._run_post_settle_selfcheck()
        await hass.async_block_till_done()
        heal.assert_not_awaited()

        for target in ctrl._active_scene_targets.values():
            target["commanded_at"] -= target["transition"] + MANUAL_DETECTION_GRACE_SECONDS + 1
        ctrl._run_post_settle_selfcheck()
        await hass.async_block_till_done()
        assert heal.await_count == 2


@pytest.mark.integration
async def test_static_selfcheck_rechecks_a_recently_healed_bulb(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    area = _area(count=1)
    area["scenes"][0].pop("animations")
    with patch.object(AreaLightingController, "_schedule_post_settle_selfcheck"):
        ctrl = (await _setup(hass, _config(area, scene_self_heal=True)))["den"]
        await _activate(hass, ctrl)
        await _advance(hass, freezer, 5)
        service_calls.clear()

        hass.states.async_set("light.den_0", "on", {"brightness": 40, "hs_color": [240, 100]})
        await hass.async_block_till_done()

        assert _turn_ons(service_calls)["light.den_0"]["brightness"] == 200
        assert ctrl._active_scene_targets["light.den_0"]["commanded_at"] == time.monotonic()
        service_calls.clear()
        await _advance(hass, freezer, 6)
        ctrl._run_post_settle_selfcheck()
        await hass.async_block_till_done()

    assert _turn_ons(service_calls)["light.den_0"]["brightness"] == 200
    assert ctrl.current_scene == "christmas"


@pytest.mark.integration
async def test_heal_stamp_replaces_tick_selfcheck_exemption(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    with patch.object(AreaLightingController, "_schedule_post_settle_selfcheck"):
        ctrl = (await _setup(hass, _config(_area(count=1), scene_self_heal=True)))["den"]
        await _activate(hass, ctrl)
        await _advance(hass, freezer, 20)
        assert ctrl._active_scene_targets["light.den_0"]["animation_tick"] is True

        await ctrl.handle_scene_drift_reassert("light.den_0", "recovery")
        assert "animation_tick" not in ctrl._active_scene_targets["light.den_0"]
        service_calls.clear()
        await _advance(hass, freezer, 21)
        ctrl._run_post_settle_selfcheck()
        await hass.async_block_till_done()

    assert _turn_ons(service_calls)["light.den_0"]["hs_color"] == [120, 100]
