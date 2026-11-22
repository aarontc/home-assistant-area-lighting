"""Manual changes remain detectable during animation fades."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.color import color_hs_to_xy

from custom_components.area_lighting.animation import point_on_animation_segment
from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.controller import AreaLightingController

from .test_animation import START, _activate, _advance, _area, _config, _setup, _turn_ons


@pytest.fixture
def disable_post_settle_selfcheck():
    with patch.object(AreaLightingController, "_schedule_post_settle_selfcheck"):
        yield


async def _start_fade(hass, freezer, transition=20, count=1):
    freezer.move_to(START)
    cfg = _config(_area(count=count), scene_self_heal=True)
    cfg[DOMAIN]["animations"]["festive"]["transition"] = transition
    cfg[DOMAIN]["animations"]["festive"]["palette"][0]["brightness"] = 100
    cfg[DOMAIN]["animations"]["festive"]["palette"][1]["brightness"] = 200
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    await _advance(hass, freezer, 20)
    await _advance(hass, freezer, 30 if transition == 5 else 25)
    return ctrl


async def _tick_at(hass, freezer, ctrl, seconds):
    if ctrl._animation_unsub is not None:
        ctrl._animation_unsub()
        ctrl._animation_unsub = None
    await _advance(hass, freezer, seconds)
    await ctrl._animation_tick(ctrl._animation_generation)
    ctrl._start_animation()


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
@pytest.mark.parametrize(
    ("start", "end", "brightness", "actual_xy", "after_tick", "period", "transition", "manual"),
    [
        pytest.param(
            {"color_temp_kelvin": 5000},
            {"hs_color": [0, 100]},
            255,
            (0.3298, 0.2367),
            7,
            15,
            15,
            False,
            id="kelvin-to-red",
        ),
        pytest.param(
            {"xy_color": [0.371, 0.349]},
            {"hs_color": [0, 100]},
            200,
            (0.4366, 0.2861),
            7,
            15,
            15,
            False,
            id="xy-white-to-red",
        ),
        pytest.param(
            {"hs_color": [128, 100]},
            {"color_temp_kelvin": 5000},
            255,
            (0.2925, 0.376),
            11,
            15,
            15,
            False,
            id="green-to-kelvin",
        ),
        pytest.param(
            {"color_temp_kelvin": 5000},
            {"hs_color": [0, 100]},
            60,
            (0.3298, 0.2367),
            7,
            15,
            15,
            True,
            id="brightness-outside-range",
        ),
        pytest.param(
            {"color_temp_kelvin": 5000},
            {"hs_color": [0, 100]},
            255,
            (0.3298, 0.2367),
            26,
            30,
            20,
            True,
            id="after-white-fade",
        ),
        pytest.param(
            {"color_temp_kelvin": 5000},
            {"hs_color": [0, 100]},
            255,
            (0.3298, 0.2367),
            22,
            30,
            20,
            False,
            id="white-fade-grace-after-transition",
        ),
        pytest.param(
            {"hs_color": [0, 100]},
            {"hs_color": [128, 100]},
            255,
            (0.3298, 0.2367),
            7,
            15,
            15,
            True,
            id="saturated-fade",
        ),
        pytest.param(
            {"hs_color": [0, 0]},
            {"hs_color": [0, 100]},
            255,
            None,
            7,
            15,
            15,
            False,
            id="white-fade-without-xy",
        ),
        pytest.param(
            {"hs_color": [0, 0]},
            {"hs_color": [0, 100]},
            60,
            None,
            7,
            15,
            15,
            True,
            id="brightness-outside-range-without-xy",
        ),
        pytest.param(
            {"hs_color": [0, 0]},
            {"hs_color": [0, 100]},
            255,
            None,
            26,
            30,
            20,
            True,
            id="after-white-fade-without-xy",
        ),
    ],
)
async def test_desaturated_animation_report_manual_detection(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    freezer,
    start,
    end,
    brightness,
    actual_xy,
    after_tick,
    period,
    transition,
    manual,
):
    freezer.move_to(START)
    cfg = _config(_area(count=2), scene_self_heal=True)
    endpoint_brightness = 200 if brightness == 200 else 255
    entries = [
        dict(start, brightness=endpoint_brightness),
        dict(end, brightness=endpoint_brightness),
    ]
    initial = int(START.timestamp() // period) % len(entries)
    cfg[DOMAIN]["animations"]["festive"].update(
        period=period, transition=transition, palette=entries[-initial:] + entries[:-initial]
    )
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    first_tick = period - START.timestamp() % period
    await _tick_at(hass, freezer, ctrl, first_tick)
    ctrl._animation_unsub()
    ctrl._animation_unsub = None
    target = ctrl._active_scene_targets["light.den_0"]
    assert all(target["animation_from"][key] == value for key, value in start.items())
    assert all(target[key] == value for key, value in end.items())
    await _advance(hass, freezer, first_tick + after_tick)
    service_calls.clear()

    attributes = {"brightness": brightness}
    if actual_xy is not None:
        attributes.update(xy_color=actual_xy, color_mode="xy")
    else:
        attributes.update(hs_color=[240, 100], color_mode="hs")
    hass.states.async_set("light.den_0", "on", attributes, force_update=True)
    await hass.async_block_till_done()

    assert ctrl._state.is_manual is manual
    if not manual:
        assert ctrl.current_scene == "christmas"
        assert ctrl.diagnostic_snapshot()["animation"]["name"] == "festive"
    assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
@pytest.mark.parametrize("old_state", ["unavailable", "unknown"])
@pytest.mark.parametrize("scene_self_heal", [False, True])
async def test_recovery_during_white_fade_keeps_recovery_handling(
    hass: HomeAssistant, helper_entities, service_calls, freezer, old_state, scene_self_heal
):
    freezer.move_to(START)
    cfg = _config(_area(count=2), scene_self_heal=scene_self_heal)
    entries = [
        {"color_temp_kelvin": 5000, "brightness": 255},
        {"hs_color": [0, 100], "brightness": 255},
    ]
    initial = int(START.timestamp() // 15) % len(entries)
    cfg[DOMAIN]["animations"]["festive"].update(
        period=15, transition=15, palette=entries[-initial:] + entries[:-initial]
    )
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    first_tick = 15 - START.timestamp() % 15
    await _tick_at(hass, freezer, ctrl, first_tick)
    ctrl._animation_unsub()
    ctrl._animation_unsub = None
    assert ctrl._active_scene_targets["light.den_0"]["animation_from"]["color_temp_kelvin"] == 5000
    hass.states.async_set("light.den_0", old_state)
    await hass.async_block_till_done()
    await _advance(hass, freezer, first_tick + 7)
    service_calls.clear()

    hass.states.async_set(
        "light.den_0",
        "on",
        {"brightness": 255, "xy_color": (0.3298, 0.2367), "color_mode": "xy"},
    )
    await hass.async_block_till_done()

    assert ctrl.current_scene == "christmas"
    if scene_self_heal:
        assert _turn_ons(service_calls)["light.den_0"]["hs_color"] == [0, 100]
    else:
        assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
@pytest.mark.parametrize("after_tick", [3, 4, 5])
@pytest.mark.parametrize("report", ["xy", "hs", "off"])
async def test_previous_desaturated_segment_relaxes_color_only_during_grace(
    hass: HomeAssistant, helper_entities, service_calls, freezer, after_tick, report
):
    freezer.move_to(START)
    cfg = _config(_area(count=2), scene_self_heal=True)
    entries = [
        {"color_temp_kelvin": 5000, "brightness": 30},
        {"hs_color": [0, 100], "brightness": 120},
        {"hs_color": [128, 100], "brightness": 230},
    ]
    initial = int(START.timestamp() // 30) % len(entries)
    cfg[DOMAIN]["animations"]["festive"].update(
        transition=20, palette=entries[-initial:] + entries[:-initial]
    )
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    await _tick_at(hass, freezer, ctrl, 20)
    await _tick_at(hass, freezer, ctrl, 50)
    ctrl._animation_unsub()
    ctrl._animation_unsub = None
    await _advance(hass, freezer, 50 + after_tick)
    service_calls.clear()

    attributes = {"brightness": 30}
    if report == "xy":
        attributes.update(xy_color=(0.3298, 0.2367), color_mode="xy")
    else:
        attributes.update(hs_color=[240, 100], color_mode="hs")
    hass.states.async_set(
        "light.den_0", "off" if report == "off" else "on", attributes, force_update=True
    )
    await hass.async_block_till_done()

    assert ctrl._state.is_manual is (after_tick >= 4 or report == "off")
    assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
async def test_activation_grace_covers_staged_report_after_first_tick(
    hass: HomeAssistant, helper_entities, service_calls, freezer
):
    freezer.move_to(START + timedelta(seconds=19.7))
    ctrl = (await _setup(hass, _config(_area(count=1))))["den"]
    await _activate(hass, ctrl)
    await _tick_at(hass, freezer, ctrl, 20)
    assert "animation_from" in ctrl._active_scene_targets["light.den_0"]
    await _advance(hass, freezer, 20.1)
    service_calls.clear()

    hass.states.async_set("light.den_0", "on", {"brightness": 1, "hs_color": [240, 100]})
    await hass.async_block_till_done()

    assert ctrl.current_scene == "christmas"
    assert ctrl.diagnostic_snapshot()["animation"]["name"] == "festive"
    assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
@pytest.mark.parametrize("transition", [5, 20, 30])
async def test_persons_color_change_during_fade_latches_manual_with_self_heal(
    hass: HomeAssistant, helper_entities, service_calls, freezer, transition
):
    ctrl = await _start_fade(hass, freezer, transition)
    service_calls.clear()

    hass.states.async_set(
        "light.den_0",
        "on",
        {"brightness": 150, "xy_color": color_hs_to_xy(240, 100), "color_mode": "xy"},
    )
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert ctrl.diagnostic_snapshot()["animation"] is None
    assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
async def test_mid_fade_report_on_xy_segment_keeps_animation(
    hass: HomeAssistant, helper_entities, service_calls, freezer
):
    ctrl = await _start_fade(hass, freezer)
    red = color_hs_to_xy(0, 100)
    green = color_hs_to_xy(120, 100)
    midpoint = [(a + b) / 2 for a, b in zip(red, green, strict=True)]
    service_calls.clear()

    hass.states.async_set(
        "light.den_0", "on", {"brightness": 150, "xy_color": midpoint, "color_mode": "xy"}
    )
    await hass.async_block_till_done()

    assert ctrl.current_scene == "christmas"
    assert ctrl.diagnostic_snapshot()["animation"]["name"] == "festive"
    assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
async def test_brightness_outside_fade_range_latches_manual(
    hass: HomeAssistant, helper_entities, service_calls, freezer
):
    ctrl = await _start_fade(hass, freezer)
    service_calls.clear()

    hass.states.async_set(
        "light.den_0",
        "on",
        {"brightness": 230, "xy_color": color_hs_to_xy(120, 100), "color_mode": "xy"},
    )
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
@pytest.mark.parametrize("old_state", ["unavailable", "unknown"])
async def test_recovery_during_animation_fade_still_heals(
    hass: HomeAssistant, helper_entities, service_calls, freezer, old_state
):
    ctrl = await _start_fade(hass, freezer)
    hass.states.async_set("light.den_0", old_state)
    await hass.async_block_till_done()
    service_calls.clear()

    hass.states.async_set(
        "light.den_0",
        "on",
        {"brightness": 255, "xy_color": color_hs_to_xy(240, 100), "color_mode": "xy"},
    )
    await hass.async_block_till_done()

    assert ctrl.current_scene == "christmas"
    assert _turn_ons(service_calls)["light.den_0"]["hs_color"] == [120, 100]


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
async def test_animation_without_xy_report_accepts_previous_endpoint(
    hass: HomeAssistant, helper_entities, service_calls, freezer
):
    ctrl = await _start_fade(hass, freezer)
    service_calls.clear()

    hass.states.async_set(
        "light.den_0", "on", {"brightness": 100, "hs_color": [0, 100]}, force_update=True
    )
    await hass.async_block_till_done()

    assert ctrl.current_scene == "christmas"
    assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
@pytest.mark.parametrize("scene_self_heal", [False, True])
@pytest.mark.parametrize("report", ["color", "off"])
@pytest.mark.parametrize(("period", "transition", "first_tick"), [(2, 0.5, 2), (30, 5, 20)])
async def test_persons_change_within_command_grace_latches_manual(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    freezer,
    scene_self_heal,
    report,
    period,
    transition,
    first_tick,
):
    freezer.move_to(START - timedelta(seconds=10))
    cfg = _config(scene_self_heal=scene_self_heal)
    cfg[DOMAIN]["animations"]["festive"].update(period=period, transition=transition)
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    await _tick_at(hass, freezer, ctrl, first_tick)
    await _advance(hass, freezer, first_tick + 1)
    service_calls.clear()

    hass.states.async_set(
        "light.den_0",
        "off" if report == "off" else "on",
        {"brightness": 200, "hs_color": [240, 100]},
    )
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert ctrl.diagnostic_snapshot()["animation"] is None
    await _advance(hass, freezer, first_tick + period)
    assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
async def test_person_turning_off_one_animated_light_latches_manual(
    hass: HomeAssistant, helper_entities, service_calls, freezer
):
    ctrl = await _start_fade(hass, freezer, count=2)
    service_calls.clear()

    hass.states.async_set("light.den_0", "off")
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert ctrl.diagnostic_snapshot()["animation"] is None
    assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
@pytest.mark.parametrize(("period", "transition"), [(2, 0.5), (4, 1)])
@pytest.mark.parametrize("report", ["color", "off"])
async def test_fast_animation_detects_persons_change_between_ticks(
    hass: HomeAssistant, helper_entities, service_calls, freezer, period, transition, report
):
    freezer.move_to(START)
    cfg = _config(scene_self_heal=True)
    cfg[DOMAIN]["animations"]["festive"].update(period=period, transition=transition)
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    for elapsed in range(period, 40, period):
        await _advance(hass, freezer, elapsed)
    await _advance(hass, freezer, elapsed + period / 2)
    service_calls.clear()

    hass.states.async_set(
        "light.den_0",
        "off" if report == "off" else "on",
        {"brightness": 200, "xy_color": color_hs_to_xy(240, 100), "color_mode": "xy"},
    )
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert ctrl.diagnostic_snapshot()["animation"] is None
    await _advance(hass, freezer, elapsed + period)
    assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
@pytest.mark.parametrize("animated", [False, True])
@pytest.mark.parametrize("after_tick", [1, 10])
@pytest.mark.parametrize("sequential", [False, True])
async def test_room_turned_off_externally_becomes_off_immediately(
    hass: HomeAssistant, helper_entities, service_calls, freezer, animated, after_tick, sequential
):
    freezer.move_to(START)
    area = _area()
    if not animated:
        area["scenes"][0].pop("animations")
    cfg = _config(area, scene_self_heal=True)
    cfg[DOMAIN]["animations"]["festive"].update(period=60, transition=60)
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    await _advance(hass, freezer, 50)
    await _advance(hass, freezer, 50 + after_tick)
    service_calls.clear()

    hass.states.async_set("light.den_0", "off")
    if sequential:
        await hass.async_block_till_done()
    hass.states.async_set("light.den_1", "off")
    await hass.async_block_till_done()

    assert ctrl.current_scene == "off"
    assert ctrl._lights_off_recheck_handle is None
    assert ctrl.diagnostic_snapshot()["animation"] is None
    await _advance(hass, freezer, 110)
    assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
@pytest.mark.parametrize("old_state", ["unavailable", "unknown"])
@pytest.mark.parametrize("scene_self_heal", [False, True])
async def test_stale_recovery_after_missed_animation_steps_never_latches_manual(
    hass: HomeAssistant, helper_entities, service_calls, freezer, old_state, scene_self_heal
):
    freezer.move_to(START)
    cfg = _config(scene_self_heal=scene_self_heal)
    cfg[DOMAIN]["animations"]["festive"]["palette"] = [
        {"hs_color": [0, 100]},
        {"hs_color": [120, 100]},
        {"hs_color": [240, 100]},
        {"hs_color": [60, 100]},
    ]
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    stale = dict(hass.states.get("light.den_0").attributes)
    hass.states.async_set("light.den_0", old_state)
    await hass.async_block_till_done()
    for elapsed in (20, 50, 80, 90):
        await _advance(hass, freezer, elapsed)
    service_calls.clear()

    hass.states.async_set(
        "light.den_0",
        "on",
        {"brightness": 200, "xy_color": color_hs_to_xy(*stale["hs_color"]), "color_mode": "xy"},
    )
    await hass.async_block_till_done()

    assert ctrl.current_scene == "christmas"
    assert ctrl.diagnostic_snapshot()["animation"]["name"] == "festive"
    if scene_self_heal:
        assert (
            _turn_ons(service_calls)["light.den_0"]["hs_color"]
            == ctrl._active_scene_targets["light.den_0"]["hs_color"]
        )
    else:
        assert not service_calls
    service_calls.clear()
    await _advance(hass, freezer, 110)
    assert "light.den_0" in _turn_ons(service_calls)


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
@pytest.mark.parametrize("report", ["previous_segment", "current_segment", "previous_endpoint"])
async def test_late_animation_reports_accept_previous_segment_and_brightness(
    hass: HomeAssistant, helper_entities, service_calls, freezer, report
):
    freezer.move_to(START)
    cfg = _config(_area(count=1), scene_self_heal=True)
    entries = [
        {"hs_color": [0, 100], "brightness": 30},
        {"hs_color": [120, 100], "brightness": 120},
        {"hs_color": [240, 100], "brightness": 230},
    ]
    initial = int(START.timestamp() // 30) % len(entries)
    cfg[DOMAIN]["animations"]["festive"]["palette"] = entries[-initial:] + entries[:-initial]
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    assert ctrl._active_scene_targets["light.den_0"]["brightness"] == 30
    await _tick_at(hass, freezer, ctrl, 20)
    await _tick_at(hass, freezer, ctrl, 50)
    await _advance(hass, freezer, 53)
    service_calls.clear()

    if report == "previous_endpoint":
        attributes = {"brightness": 30, "hs_color": [0, 100]}
    else:
        hues = (0, 120) if report == "previous_segment" else (120, 240)
        start, end = (color_hs_to_xy(hue, 100) for hue in hues)
        attributes = {
            "brightness": 30,
            "xy_color": [(a + b) / 2 for a, b in zip(start, end, strict=True)],
            "color_mode": "xy",
        }
    hass.states.async_set("light.den_0", "on", attributes, force_update=True)
    await hass.async_block_till_done()

    assert ctrl.current_scene == "christmas"
    assert ctrl.diagnostic_snapshot()["animation"]["name"] == "festive"
    assert not service_calls


@pytest.mark.integration
@pytest.mark.usefixtures("disable_post_settle_selfcheck")
@pytest.mark.parametrize("after_tick", [4, 5])
@pytest.mark.parametrize("report", ["previous_segment", "previous_brightness", "previous_endpoint"])
async def test_previous_animation_step_stops_masking_manual_changes_after_grace(
    hass: HomeAssistant, helper_entities, service_calls, freezer, after_tick, report
):
    freezer.move_to(START)
    cfg = _config(_area(count=1), scene_self_heal=True)
    entries = [
        {"hs_color": [0, 100], "brightness": 30},
        {"hs_color": [120, 100], "brightness": 120},
        {"hs_color": [240, 100], "brightness": 230},
    ]
    initial = int(START.timestamp() // 30) % len(entries)
    cfg[DOMAIN]["animations"]["festive"]["palette"] = entries[-initial:] + entries[:-initial]
    ctrl = (await _setup(hass, cfg))["den"]
    await _activate(hass, ctrl)
    await _tick_at(hass, freezer, ctrl, 20)
    await _tick_at(hass, freezer, ctrl, 50)
    await _advance(hass, freezer, 50 + after_tick)
    target = ctrl._active_scene_targets["light.den_0"]
    assert target["animation_from_previous"]["brightness"] == 30
    assert target["animation_from"]["brightness"] == 120
    assert target["brightness"] == 230
    service_calls.clear()

    if report == "previous_endpoint":
        attributes = {"brightness": 30, "hs_color": [0, 100]}
    else:
        hues = (0, 120) if report == "previous_segment" else (120, 240)
        start, end = (color_hs_to_xy(hue, 100) for hue in hues)
        xy = [a + (b - a) / 4 for a, b in zip(start, end, strict=True)]
        if report == "previous_segment":
            assert point_on_animation_segment(xy, start, end)
            assert not point_on_animation_segment(
                xy, color_hs_to_xy(120, 100), color_hs_to_xy(240, 100)
            )
        attributes = {
            "brightness": 150 if report == "previous_segment" else 30,
            "xy_color": xy,
            "color_mode": "xy",
        }
    hass.states.async_set("light.den_0", "on", attributes, force_update=True)
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert ctrl.diagnostic_snapshot()["animation"] is None
    assert not service_calls
