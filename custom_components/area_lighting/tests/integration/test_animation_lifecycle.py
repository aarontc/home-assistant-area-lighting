"""Animation shutdown and failures during activation, reload and startup."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.controller import AreaLightingController

from .test_animation import START, _activate, _advance, _area, _config, _setup


@pytest.fixture(autouse=True)
def disable_post_settle_selfcheck():
    with patch.object(AreaLightingController, "_schedule_post_settle_selfcheck"):
        yield


def _register_lights(hass: HomeAssistant, failing: set[str], sent: list[str]) -> None:
    async def turn_on(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        sent.append(entity_id)
        if entity_id in failing:
            raise HomeAssistantError("Bridge busy")

    hass.services.async_register("light", "turn_on", turn_on)
    hass.services.async_register("light", "turn_off", lambda call: None)


@pytest.mark.integration
@pytest.mark.parametrize("path", ["ambient", "force"])
async def test_own_turn_off_never_latches_manual(
    hass: HomeAssistant, helper_entities, freezer, path
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config(_area(count=3), scene_self_heal=True)))["den"]

    async def turn_on(call: ServiceCall) -> None:
        attributes = {
            key: value for key, value in call.data.items() if key in {"brightness", "hs_color"}
        }
        hass.states.async_set(call.data["entity_id"], "on", attributes)

    async def turn_off(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        for _ in range(int(entity_id.rsplit("_", 1)[1]) * 5):
            await asyncio.sleep(0)
        hass.states.async_set(entity_id, "off")

    hass.services.async_register("light", "turn_on", turn_on)
    hass.services.async_register("light", "turn_off", turn_off)
    await ctrl._activate_scene("christmas", ActivationSource.AMBIENCE)
    await hass.async_block_till_done()
    await _advance(hass, freezer, 20)
    await _advance(hass, freezer, 30)
    assert all("animation_from" in target for target in ctrl._active_scene_targets.values())
    scenes = []
    ctrl.add_state_listener(lambda: scenes.append(ctrl.current_scene))

    if path == "ambient":
        await ctrl.handle_ambient_disabled()
    else:
        await ctrl.lighting_force_off()
    await hass.async_block_till_done()

    assert ctrl.current_scene == "off"
    assert "manual" not in scenes
    assert ctrl.diagnostic_snapshot()["animation"] is None


@pytest.mark.integration
async def test_stopping_animation_clears_both_fade_endpoints(
    hass: HomeAssistant, helper_entities, service_calls, freezer
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config()))["den"]
    await _activate(hass, ctrl)
    await _advance(hass, freezer, 20)
    first_from = ctrl._active_scene_targets["light.den_0"]["animation_from"]
    await _advance(hass, freezer, 50)
    assert ctrl._active_scene_targets["light.den_0"]["animation_from_previous"] == first_from
    stamps = {
        entity_id: (target["commanded_at"], target["transition"])
        for entity_id, target in ctrl._active_scene_targets.items()
    }

    ctrl._stop_animation()

    assert all(
        "animation_from" not in target and "animation_from_previous" not in target
        for target in ctrl._active_scene_targets.values()
    )
    for entity_id, target in ctrl._active_scene_targets.items():
        assert (target["commanded_at"], target["transition"]) == stamps[entity_id]
        assert target["animation_tick"] is True
    assert ctrl.lights_settling_until() is None


@pytest.mark.integration
@pytest.mark.parametrize("path", ["ambient", "force"])
@pytest.mark.parametrize("animated", [False, True])
@pytest.mark.parametrize("scene_self_heal", [False, True])
async def test_turn_off_ignores_last_tick_echo(
    hass: HomeAssistant, helper_entities, service_calls, freezer, path, animated, scene_self_heal
) -> None:
    freezer.move_to(START)
    area = _area()
    if not animated:
        area["scenes"][0].pop("animations")
    cfg = _config(area, scene_self_heal=scene_self_heal)
    cfg[DOMAIN]["animations"]["festive"].update(transition=20)
    ctrl = (await _setup(hass, cfg))["den"]
    await ctrl._activate_scene("christmas", ActivationSource.AMBIENCE)
    for entity_id, target in ctrl._active_scene_targets.items():
        hass.states.async_set(entity_id, "on", {"brightness": 200, "hs_color": target["hs_color"]})
    await hass.async_block_till_done()
    await _advance(hass, freezer, 20)
    await _advance(hass, freezer, 20.1)
    scenes = []
    ctrl.add_state_listener(lambda: scenes.append(ctrl.current_scene))

    async def echo_then_turn_off(transition):
        # The bridge reports its clamped tick color before applying the off.
        hass.states.async_set(
            "light.den_0",
            "on",
            {"brightness": 200, "xy_color": (0.17, 0.7), "color_mode": "xy"}
            if animated
            else {"brightness": 200, "hs_color": [240, 100]},
        )
        await hass.async_block_till_done()
        for entity_id in ctrl._active_scene_targets:
            hass.states.async_set(entity_id, "off")
        await hass.async_block_till_done()

    with patch.object(ctrl, "_turn_off_all_lights", side_effect=echo_then_turn_off):
        if path == "ambient":
            await ctrl.handle_ambient_disabled()
        else:
            await ctrl.lighting_force_off()
    await hass.async_block_till_done()

    assert ctrl.current_scene == "off"
    assert "manual" not in scenes
    assert ctrl._lights_off_recheck_handle is None
    assert ctrl.diagnostic_snapshot()["animation"] is None


@pytest.mark.integration
async def test_activation_logs_failed_lights_and_starts_ticks(
    hass: HomeAssistant, helper_entities, freezer, caplog
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config(_area(count=3))))["den"]
    failing = {"light.den_0", "light.den_1"}
    sent = []
    _register_lights(hass, failing, sent)

    await _activate(hass, ctrl)

    assert ctrl.current_scene == "christmas"
    assert ctrl.diagnostic_snapshot()["animation"]["name"] == "festive"
    assert sorted(sent) == ["light.den_0", "light.den_1", "light.den_2"]
    for entity_id in failing:
        assert any(
            entity_id in record.message and "Bridge busy" in record.message
            for record in caplog.records
        )
    failing.clear()
    sent.clear()
    await _advance(hass, freezer, 20)
    assert sorted(sent) == ["light.den_0", "light.den_1", "light.den_2"]


@pytest.mark.integration
async def test_select_reapply_with_failed_light_keeps_ticks(
    hass: HomeAssistant, helper_entities, freezer
) -> None:
    freezer.move_to(START)
    cfg = _config()
    cfg[DOMAIN]["animations"]["other"] = {"period": 30, "palette": [{"hs_color": [40, 100]}]}
    cfg[DOMAIN]["areas"][0]["scenes"][0]["animations"].append("other")
    ctrl = (await _setup(hass, cfg))["den"]
    failing: set[str] = set()
    sent = []
    _register_lights(hass, failing, sent)
    await _activate(hass, ctrl)
    failing.add("light.den_0")

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.area_lighting_christmas_animation", "option": "other"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert ctrl.diagnostic_snapshot()["animation"]["name"] == "other"
    failing.clear()
    sent.clear()
    await _advance(hass, freezer, 20)
    assert sorted(sent) == ["light.den_0", "light.den_1"]


@pytest.mark.integration
async def test_failed_reapply_restarts_ticks_in_finally(
    hass: HomeAssistant, helper_entities, freezer
) -> None:
    freezer.move_to(START)
    ctrl = (await _setup(hass, _config()))["den"]
    sent = []
    _register_lights(hass, set(), sent)
    await _activate(hass, ctrl)

    with (
        patch.object(
            ctrl, "_apply_scene_data", AsyncMock(side_effect=HomeAssistantError("Reapply failed"))
        ),
        pytest.raises(HomeAssistantError, match="Reapply failed"),
    ):
        await ctrl.async_scene_animation_changed("christmas")

    assert ctrl.diagnostic_snapshot()["animation"]["name"] == "festive"
    sent.clear()
    await _advance(hass, freezer, 20)
    assert sorted(sent) == ["light.den_0", "light.den_1"]


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["light", "controller"])
async def test_reload_failure_does_not_skip_later_areas(
    hass: HomeAssistant, helper_entities, network_room_config, pin_circadian, freezer, failure
) -> None:
    freezer.move_to(START)
    await pin_circadian(50, 3000, tick=False)
    cfg = _config(_area(), _area("hall"), network_room_config[DOMAIN]["areas"][0])
    controllers = await _setup(hass, cfg)
    den, hall, network = (controllers[key] for key in ("den", "hall", "network_room"))
    failing: set[str] = set()
    sent = []
    _register_lights(hass, failing, sent)
    await _activate(hass, den)
    await _activate(hass, hall)
    await network._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    failing.add("light.den_0")
    resume = den.async_scene_animation_changed
    if failure == "controller":
        resume = AsyncMock(side_effect=HomeAssistantError("Area reapply failed"))

    with (
        patch(
            "custom_components.area_lighting.async_hass_config_yaml", AsyncMock(return_value=cfg)
        ),
        patch.object(den, "async_scene_animation_changed", resume),
    ):
        await hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
    await hass.async_block_till_done()

    assert hall.diagnostic_snapshot()["animation"]["name"] == "festive"
    assert network.circadian_running
    if failure == "light":
        assert den.diagnostic_snapshot()["animation"]["name"] == "festive"
    failing.clear()
    sent.clear()
    await _advance(hass, freezer, 20)
    expected = {"light.hall_0", "light.hall_1"}
    if failure == "light":
        expected.update({"light.den_0", "light.den_1"})
    assert expected.issubset(sent)


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["light", "controller"])
async def test_startup_failure_does_not_skip_later_areas(
    hass: HomeAssistant, helper_entities, network_room_config, pin_circadian, freezer, failure
) -> None:
    freezer.move_to(START)
    await pin_circadian(50, 3000, tick=False)
    cfg = _config(_area(), _area("hall"), network_room_config[DOMAIN]["areas"][0])
    for area in cfg[DOMAIN]["areas"]:
        for light in area["lights"]:
            hass.states.async_set(light["id"], "on", {"brightness": 200, "hs_color": [0, 100]})
    assert await async_setup_component(hass, DOMAIN, cfg)
    await hass.async_block_till_done()
    den, hall, network = (
        hass.data[DOMAIN]["controllers"][key] for key in ("den", "hall", "network_room")
    )
    den._state.transition_to_scene("christmas", ActivationSource.USER)
    hall._state.transition_to_scene("christmas", ActivationSource.USER)
    network._state.transition_to_circadian(ActivationSource.USER)
    failing = {"light.den_0"}
    sent = []
    _register_lights(hass, failing, sent)
    resume = den.async_scene_animation_changed
    if failure == "controller":
        resume = AsyncMock(side_effect=HomeAssistantError("Area reapply failed"))

    with patch.object(den, "async_scene_animation_changed", resume):
        hass.bus.async_fire("homeassistant_started")
        await hass.async_block_till_done()

    assert hall.diagnostic_snapshot()["animation"]["name"] == "festive"
    assert network.circadian_running
    if failure == "light":
        assert den.diagnostic_snapshot()["animation"]["name"] == "festive"
