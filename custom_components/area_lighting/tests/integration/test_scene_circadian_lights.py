"""Lights marked circadian: true follow the curve inside a visual scene.

The kitchen's christmas scene holds both overheads on the curve and the
accent and strip at static colours. The accent has a circadian group, so it
shows that the adjuster leaves a grouped light alone when the scene sets it.
The scene lists the second overhead first, so diagnostics must sort.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.color import color_temperature_to_rgb

from custom_components.area_lighting import alert
from custom_components.area_lighting.alert import execute_alert
from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.models import AlertPattern, AlertStep
from custom_components.area_lighting.state_storage import STORAGE_KEY

OVERHEAD_1 = "light.kitchen_overhead_1"
ACCENT = "light.kitchen_accent"
STRIP = "light.kitchen_strip"
OVERHEAD_2 = "light.kitchen_overhead_2"
LIGHTS = (OVERHEAD_1, ACCENT, STRIP, OVERHEAD_2)
CIRCADIAN_LIGHTS = (OVERHEAD_1, OVERHEAD_2)
SLEEP = "input_boolean.kitchen_sleep"
ACCENT_TARGET = {"brightness": 255, "hs_color": [0, 100]}
STRIP_TARGET = {"brightness": 200, "hs_color": [128, 100]}


def _rgb(kelvin: float) -> list[int]:
    return [round(c) for c in color_temperature_to_rgb(kelvin)]


def _payloads(kelvin: float, transition: float) -> dict[str, dict]:
    """What both overheads get at percent 50: full brightness and the kelvin."""
    return {
        OVERHEAD_1: {"brightness": 255, "color_temp_kelvin": kelvin, "transition": transition},
        OVERHEAD_2: {"brightness": 255, "rgb_color": _rgb(kelvin), "transition": transition},
    }


def _config() -> dict:
    return {
        DOMAIN: {
            "areas": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "event_handlers": True,
                    "circadian_groups": [
                        {
                            "name": "Main",
                            "max_brightness": 100,
                            "min_brightness": 40,
                            "sleep_entity": SLEEP,
                            "sleep_state": ["on"],
                            "sleep_brightness": 5,
                            "sleep_colortemp": 2000,
                        },
                    ],
                    "lights": [
                        {"id": OVERHEAD_1, "circadian_group": "Main", "circadian_type": "ct"},
                        {"id": ACCENT, "circadian_group": "Main", "circadian_type": "ct"},
                        {"id": STRIP},
                        {"id": OVERHEAD_2, "circadian_group": "Main", "circadian_type": "rgb"},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                        {"id": "evening", "name": "Evening"},
                        {
                            "id": "christmas",
                            "name": "Christmas",
                            "entities": {
                                OVERHEAD_2: {"circadian": True},
                                ACCENT: {"state": "on", **ACCENT_TARGET},
                                STRIP: {"state": "on", **STRIP_TARGET},
                                OVERHEAD_1: {"circadian": True},
                            },
                        },
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict | None = None):
    for entity_id in LIGHTS:
        hass.states.async_set(entity_id, "off", {})
    hass.states.async_set(SLEEP, "off")
    assert await async_setup_component(hass, DOMAIN, cfg or _config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    return hass.data[DOMAIN]["controllers"]["kitchen"]


async def _christmas(hass: HomeAssistant, ctrl) -> None:
    await ctrl._activate_scene("christmas", ActivationSource.USER)
    await hass.async_block_till_done()


def _calls(service_calls, service: str = "turn_on") -> dict[str, dict]:
    """entity_id -> data of the light calls, without entity_id. Colours read
    as lists: a scene sends lists and the adjuster tuples."""
    return {
        c.data["entity_id"]: {
            k: list(v) if isinstance(v, tuple) else v for k, v in c.data.items() if k != "entity_id"
        }
        for c in service_calls
        if c.domain == "light" and c.service == service
    }


def _report(hass: HomeAssistant, entity_id: str, **attributes: Any) -> None:
    hass.states.async_set(entity_id, "on", attributes)


async def _all_on(hass: HomeAssistant, ctrl) -> None:
    """Report every light on as the activation left it, then let the echo
    window lapse so the adjuster sends again."""
    _report(hass, OVERHEAD_1, brightness=255, color_temp_kelvin=4000)
    _report(hass, ACCENT, **ACCENT_TARGET)
    _report(hass, STRIP, **STRIP_TARGET)
    _report(hass, OVERHEAD_2, brightness=255, rgb_color=_rgb(4000))
    await hass.async_block_till_done()
    ctrl._circadian_commanded_at.clear()


def _age(ctrl, seconds: float = 300.0) -> None:
    """Move the activation into the past, beyond its grace, fade and heal windows."""
    ctrl._state.last_scene_change_monotonic = time.monotonic() - seconds
    for target in ctrl._active_scene_targets.values():
        if "commanded_at" in target:
            target["commanded_at"] -= seconds


async def _reload(hass: HomeAssistant, cfg: dict) -> None:
    with patch(
        "custom_components.area_lighting.async_hass_config_yaml", AsyncMock(return_value=cfg)
    ):
        await hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
    await hass.async_block_till_done()


def _demand_response(hass: HomeAssistant) -> None:
    hass.data[DOMAIN]["global"]._demand_response_active = True


# ── Activation and ticks ───────────────────────────────────────────────


@pytest.mark.integration
async def test_activation_sends_circadian_payloads_to_the_subset_and_static_targets_to_the_rest(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    service_calls.clear()

    await _christmas(hass, ctrl)

    assert _calls(service_calls) == {
        **_payloads(4000, 0.5),
        ACCENT: {**ACCENT_TARGET, "transition": 0.5},
        STRIP: {**STRIP_TARGET, "transition": 0.5},
    }
    assert _calls(service_calls, "turn_off") == {}
    assert ctrl.circadian_running
    assert ctrl.diagnostic_snapshot()["circadian_lights"] == [OVERHEAD_1, OVERHEAD_2]


@pytest.mark.integration
async def test_a_tick_moves_only_the_subset(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)
    await _all_on(hass, ctrl)
    service_calls.clear()

    await pin_circadian(60, 4500)

    assert _calls(service_calls) == _payloads(4500, 60)


@pytest.mark.integration
async def test_the_activation_on_echo_gets_no_second_command(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)
    assert ctrl.circadian_running
    service_calls.clear()

    _report(hass, OVERHEAD_1, brightness=255, color_temp_kelvin=4000)
    _report(hass, OVERHEAD_2, brightness=255, rgb_color=_rgb(4000))
    await hass.async_block_till_done()

    assert _calls(service_calls) == {}


@pytest.mark.integration
async def test_the_diagnostics_sensor_lists_the_circadian_lights(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)

    await _christmas(hass, ctrl)

    per_area = hass.states.get("sensor.area_lighting_diagnostics").attributes["per_area"]
    assert per_area["kitchen"]["circadian_lights"] == sorted(CIRCADIAN_LIGHTS)

    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl.diagnostic_snapshot()["circadian_lights"] == []


# ── Manual detection and self-heal ─────────────────────────────────────


@pytest.mark.integration
async def test_a_tick_echo_does_not_latch_manual_but_a_change_to_a_static_light_does(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)
    await _all_on(hass, ctrl)
    _age(ctrl)

    _report(hass, OVERHEAD_1, brightness=120, color_temp_kelvin=2700)
    await hass.async_block_till_done()
    assert ctrl.current_scene == "christmas"

    _report(hass, ACCENT, brightness=255, hs_color=[240, 100])
    await hass.async_block_till_done()
    assert ctrl._state.is_manual


@pytest.mark.integration
async def test_self_heal_does_not_reassert_a_circadian_target(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian, monkeypatch
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)
    ctrl._heal_selfcheck_handle.cancel()
    await _all_on(hass, ctrl)
    # The curve has moved the overhead since the activation, and a glitch
    # has turned the accent blue.
    _report(hass, OVERHEAD_1, brightness=120, color_temp_kelvin=2700)
    _report(hass, ACCENT, brightness=255, hs_color=[240, 100])
    await hass.async_block_till_done()
    service_calls.clear()

    await ctrl.handle_scene_drift_reassert(OVERHEAD_1, "glitch_window")
    assert _calls(service_calls) == {}

    healed: list[str] = []

    async def record(entity_id: str, _reason: str) -> None:
        healed.append(entity_id)

    monkeypatch.setattr(ctrl, "handle_scene_drift_reassert", record)
    ctrl._run_post_settle_selfcheck()
    await hass.async_block_till_done()
    assert healed == [ACCENT]


# ── Listeners and the minimum brightness reduction ─────────────────────


@pytest.mark.integration
async def test_the_light_on_listener_touches_only_the_subset(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)
    _report(hass, STRIP, **STRIP_TARGET)
    await hass.async_block_till_done()
    ctrl._circadian_commanded_at.clear()
    service_calls.clear()

    _report(hass, ACCENT, **ACCENT_TARGET)
    _report(hass, OVERHEAD_1, brightness=100)
    await hass.async_block_till_done()

    assert _calls(service_calls) == {OVERHEAD_1: _payloads(4000, 0.5)[OVERHEAD_1]}


@pytest.mark.integration
async def test_the_sleep_listener_touches_only_the_subset(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)
    await _all_on(hass, ctrl)
    service_calls.clear()

    hass.states.async_set(SLEEP, "on")
    await hass.async_block_till_done()

    assert _calls(service_calls) == {
        OVERHEAD_1: {"brightness": 13, "color_temp_kelvin": 2000, "transition": 0.5},
        OVERHEAD_2: {"brightness": 13, "rgb_color": _rgb(2000), "transition": 0.5},
    }


@pytest.mark.integration
async def test_the_min_brightness_reduction_touches_only_the_subset(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)
    await _all_on(hass, ctrl)
    service_calls.clear()

    await hass.data[DOMAIN]["global"].async_set_circadian_min_brightness_reduction(50)
    await hass.async_block_till_done()

    assert set(_calls(service_calls)) == set(CIRCADIAN_LIGHTS)


# ── Leaving the scene ──────────────────────────────────────────────────


async def _assert_tick_sends_nothing(hass: HomeAssistant, ctrl, service_calls, pin_circadian):
    assert not ctrl.circadian_running
    await _all_on(hass, ctrl)
    service_calls.clear()
    await pin_circadian(60, 4500)
    assert _calls(service_calls) == {}


@pytest.mark.integration
@pytest.mark.parametrize("press", ["lighting_raise", "lighting_lower"])
async def test_raise_and_lower_stop_the_adjuster(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian, press: str
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)
    await _all_on(hass, ctrl)

    await getattr(ctrl, press)()
    await hass.async_block_till_done()

    assert ctrl.dimmed
    assert ctrl.diagnostic_snapshot()["circadian_lights"] == []
    await _assert_tick_sends_nothing(hass, ctrl, service_calls, pin_circadian)


@pytest.mark.integration
async def test_dim_from_dark_stops_the_adjuster(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian, monkeypatch
) -> None:
    """The restore starts the adjuster, and it must be stopped before the dim
    commands go out, or a tick in between pushes the scene's values back."""
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)
    running_at_dim: list[bool] = []
    set_lights_to_pct = ctrl._set_lights_to_pct

    async def record(pct: int, entity_ids: list[str]) -> None:
        running_at_dim.append(ctrl.circadian_running)
        await set_lights_to_pct(pct, entity_ids)

    monkeypatch.setattr(ctrl, "_set_lights_to_pct", record)

    await ctrl.lighting_lower()
    await hass.async_block_till_done()

    assert running_at_dim == [False]
    assert ctrl.current_scene == "christmas"
    assert ctrl.dimmed
    await _assert_tick_sends_nothing(hass, ctrl, service_calls, pin_circadian)


@pytest.mark.integration
async def test_last_scene_select_into_a_mixed_scene_starts_the_adjuster_and_leaving_stops_it(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)

    async def select(option: str) -> None:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.kitchen_last_scene", "option": option},
            blocking=True,
        )
        await hass.async_block_till_done()

    await select("christmas")
    assert ctrl.circadian_running
    await _all_on(hass, ctrl)
    service_calls.clear()
    await pin_circadian(60, 4500)
    assert _calls(service_calls) == _payloads(4500, 60)

    await select("evening")
    await _assert_tick_sends_nothing(hass, ctrl, service_calls, pin_circadian)


@pytest.mark.integration
async def test_activating_a_scene_without_circadian_lights_stops_the_adjuster(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)

    await ctrl._activate_scene("evening", ActivationSource.USER)
    await hass.async_block_till_done()

    await _assert_tick_sends_nothing(hass, ctrl, service_calls, pin_circadian)


@pytest.mark.integration
async def test_a_state_change_out_of_the_mixed_scene_stops_the_adjuster(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)

    ctrl._state.transition_to_manual()
    ctrl._notify_state_change()

    assert not ctrl.circadian_running


@pytest.mark.integration
async def test_leaving_a_removed_scene_stops_the_adjuster(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)
    ctrl.area.scenes = [scene for scene in ctrl.area.scenes if scene.slug != "christmas"]

    assert ctrl._leave_removed_scene()

    assert not ctrl.circadian_running


# ── Demand response ────────────────────────────────────────────────────


@pytest.mark.integration
async def test_a_shed_circadian_light_stays_off(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """Four lights on sheds half: the strip and the second overhead."""
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    _demand_response(hass)
    service_calls.clear()

    await _christmas(hass, ctrl)

    assert ctrl.dr_shed_ids == {STRIP, OVERHEAD_2}
    assert set(_calls(service_calls, "turn_off")) == {STRIP, OVERHEAD_2}
    assert ctrl.diagnostic_snapshot()["circadian_lights"] == [OVERHEAD_1]

    await _all_on(hass, ctrl)
    service_calls.clear()
    await pin_circadian(60, 4500)
    assert _calls(service_calls) == {OVERHEAD_1: _payloads(4500, 60)[OVERHEAD_1]}


@pytest.mark.integration
async def test_the_shed_set_survives_resume_and_reload(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    cfg = _config()
    ctrl = await _setup(hass, cfg)
    _demand_response(hass)
    await _christmas(hass, ctrl)
    shed = ctrl.dr_shed_ids
    assert shed == {STRIP, OVERHEAD_2}

    await ctrl.recompute_and_apply_circadian_dr()
    assert ctrl.dr_shed_ids == shed

    await ctrl.async_resume_circadian()
    assert ctrl.dr_shed_ids == shed

    await _reload(hass, cfg)
    assert ctrl.dr_shed_ids == shed
    assert ctrl.circadian_running


@pytest.mark.integration
async def test_a_manual_relight_of_a_shed_circadian_light_latches_manual(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    _demand_response(hass)
    await _christmas(hass, ctrl)
    _report(hass, OVERHEAD_1, brightness=255, color_temp_kelvin=4000)
    _report(hass, ACCENT, **ACCENT_TARGET)
    await hass.async_block_till_done()
    _age(ctrl)

    _report(hass, OVERHEAD_2, brightness=150)
    await hass.async_block_till_done()

    assert ctrl._state.is_manual


# ── Resume ─────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_startup_resumes_a_mixed_scene(
    hass: HomeAssistant, hass_storage, helper_entities, service_calls, pin_circadian
) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "kitchen": {
                "area_state": {
                    "state": "scene",
                    "scene_slug": "christmas",
                    "source": "user",
                    "dimmed": False,
                    "previous_scene": None,
                }
            }
        },
    }
    await pin_circadian(50, 4000, tick=False)
    for entity_id in LIGHTS:
        _report(hass, entity_id, brightness=100)
    hass.states.async_set(SLEEP, "off")

    assert await async_setup_component(hass, DOMAIN, _config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    ctrl = hass.data[DOMAIN]["controllers"]["kitchen"]
    assert ctrl.current_scene == "christmas"
    assert ctrl.circadian_running
    assert _calls(service_calls) == _payloads(4000, 60)


@pytest.mark.integration
async def test_reload_resumes_a_mixed_scene(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    cfg = _config()
    ctrl = await _setup(hass, cfg)
    await _christmas(hass, ctrl)
    await _all_on(hass, ctrl)
    service_calls.clear()

    await _reload(hass, cfg)

    assert ctrl.circadian_running
    assert _calls(service_calls) == _payloads(4000, 60)


@pytest.mark.integration
async def test_alert_end_resumes_a_mixed_scene(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian, monkeypatch
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await _christmas(hass, ctrl)
    await _all_on(hass, ctrl)

    async def steps(*_args) -> None:
        # A reload stops the adjuster while the alert owns the area.
        ctrl._stop_circadian()

    monkeypatch.setattr(alert, "_execute_steps", steps)
    pattern = AlertPattern(steps=[AlertStep(target="all", state="on")], restore=False)
    await execute_alert(hass, ctrl, pattern)
    await hass.async_block_till_done()

    assert ctrl.circadian_running
    ctrl._circadian_commanded_at.clear()
    service_calls.clear()
    await pin_circadian(60, 4500)
    assert _calls(service_calls) == _payloads(4500, 60)


# ── The scene entity and snapshots ─────────────────────────────────────


@pytest.mark.integration
async def test_scene_turn_on_never_turns_a_circadian_light_off(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    service_calls.clear()

    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.kitchen_christmas"}, blocking=True
    )
    await hass.async_block_till_done()

    assert _calls(service_calls, "turn_off") == {}
    assert _calls(service_calls) == {
        **_payloads(4000, 0.5),
        ACCENT: {**ACCENT_TARGET, "transition": 0.5},
        STRIP: {**STRIP_TARGET, "transition": 0.5},
    }
    assert ctrl.current_scene == "christmas"
    assert ctrl.circadian_running

    # The commands' own echo gets no second command.
    service_calls.clear()
    _report(hass, OVERHEAD_1, brightness=255, color_temp_kelvin=4000)
    await hass.async_block_till_done()
    assert _calls(service_calls) == {}


@pytest.mark.integration
async def test_scene_turn_on_without_a_controller_skips_circadian_lights(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    await _setup(hass)
    controllers = hass.data[DOMAIN]["controllers"]
    ctrl = controllers.pop("kitchen")
    service_calls.clear()
    try:
        await hass.services.async_call(
            "scene", "turn_on", {"entity_id": "scene.kitchen_christmas"}, blocking=True
        )
        await hass.async_block_till_done()
    finally:
        controllers["kitchen"] = ctrl

    assert _calls(service_calls, "turn_off") == {}
    assert set(_calls(service_calls)) == {ACCENT, STRIP}


@pytest.mark.integration
async def test_snapshot_scene_refuses_a_scene_with_circadian_lights(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass)

    await hass.services.async_call(
        DOMAIN, "snapshot_scene", {"area_id": "kitchen", "scene": "christmas"}, blocking=True
    )

    assert hass.data[DOMAIN]["scene_storage"].get_scene_data("kitchen", "christmas") is None


@pytest.mark.integration
async def test_configured_circadian_lights_override_a_stored_snapshot(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """A snapshot taken before the scene gained circadian lights."""
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    hass.data[DOMAIN]["scene_storage"]._data["kitchen"] = {
        "christmas": {
            OVERHEAD_1: {"state": "on", "brightness": 80, "color_temp_kelvin": 2700},
            ACCENT: {"state": "on", "brightness": 90, "hs_color": [0, 100]},
            STRIP: {"state": "off"},
        }
    }
    service_calls.clear()

    await _christmas(hass, ctrl)

    assert _calls(service_calls) == {
        **_payloads(4000, 0.5),
        ACCENT: {"brightness": 90, "hs_color": [0, 100], "transition": 0.5},
    }
    assert set(_calls(service_calls, "turn_off")) == {STRIP}


@pytest.mark.integration
async def test_a_stored_cluster_target_does_not_override_a_circadian_member(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """A snapshot from before the change holds the cluster off. The cluster's
    other member still gets that target, and the circadian member is not
    turned off behind the adjuster's back."""
    await pin_circadian(50, 4000, tick=False)
    zone = "light.kitchen_zone"
    cfg = _config()
    cfg[DOMAIN]["areas"][0]["light_clusters"] = [{"id": zone, "members": [OVERHEAD_1, STRIP]}]
    hass.states.async_set(zone, "off", {})
    ctrl = await _setup(hass, cfg)
    hass.data[DOMAIN]["scene_storage"]._data["kitchen"] = {
        "christmas": {
            zone: {"state": "off"},
            ACCENT: {"state": "on", "brightness": 90, "hs_color": [0, 100]},
        }
    }
    service_calls.clear()

    await _christmas(hass, ctrl)

    assert set(_calls(service_calls, "turn_off")) == {STRIP}
    assert _calls(service_calls) == {
        **_payloads(4000, 0.5),
        ACCENT: {"brightness": 90, "hs_color": [0, 100], "transition": 0.5},
    }


@pytest.mark.integration
async def test_last_scene_select_replaces_the_previous_scene_tracking(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """A static scene's targets and pending self-check must not outlive a
    select into a mixed scene, or self-heal would reassert them over the curve."""
    await pin_circadian(50, 4000, tick=False)
    cfg = _config()
    cfg[DOMAIN]["areas"][0]["scenes"].append(
        {
            "id": "reading",
            "name": "Reading",
            "entities": {OVERHEAD_1: {"state": "on", "brightness": 100, "color_temp_kelvin": 2700}},
        }
    )
    ctrl = await _setup(hass, cfg)
    await ctrl._activate_scene("reading", ActivationSource.USER, transition=30)
    await hass.async_block_till_done()
    assert ctrl._heal_selfcheck_handle is not None

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.kitchen_last_scene", "option": "christmas"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert ctrl._heal_selfcheck_handle is None
    assert ctrl._active_scene_targets[OVERHEAD_1].get("circadian")
    service_calls.clear()
    await ctrl.handle_scene_drift_reassert(OVERHEAD_1, "test")
    assert _calls(service_calls) == {}


@pytest.mark.integration
async def test_a_change_after_a_last_scene_select_latches_manual(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """The select sends no commands, so a light changing afterwards is a
    person, not a fade settling or a glitch for self-heal to undo."""
    await pin_circadian(50, 4000, tick=False)
    ctrl = await _setup(hass)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.kitchen_last_scene", "option": "christmas"},
        blocking=True,
    )
    await hass.async_block_till_done()
    await _all_on(hass, ctrl)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 300
    service_calls.clear()

    _report(hass, ACCENT, brightness=255, hs_color=[240, 100])
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert ACCENT not in _calls(service_calls)


@pytest.mark.integration
async def test_a_stored_cluster_target_keeps_its_members_animation_exclusion(
    hass: HomeAssistant, helper_entities, pin_circadian
) -> None:
    await pin_circadian(50, 4000, tick=False)
    zone = "light.kitchen_zone"
    cfg = _config()
    cfg[DOMAIN]["animations"] = {
        "festive": {"period": 30, "transition": 5, "palette": [{"hs_color": [0, 100]}]}
    }
    area = cfg[DOMAIN]["areas"][0]
    area["light_clusters"] = [{"id": zone, "members": [OVERHEAD_1, STRIP]}]
    scene = area["scenes"][-1]
    scene["animations"] = ["festive"]
    scene["entities"][STRIP] = {"state": "on", **STRIP_TARGET, "animate": False}
    hass.states.async_set(zone, "off", {})
    ctrl = await _setup(hass, cfg)
    hass.data[DOMAIN]["scene_storage"]._data["kitchen"] = {
        "christmas": {zone: {"state": "on", "brightness": 200, "hs_color": [0, 100]}}
    }

    entities = ctrl._scene_entities("christmas")

    assert zone not in entities
    assert entities[STRIP] == {
        "state": "on",
        "brightness": 200,
        "hs_color": [0, 100],
        "animate": False,
    }
    assert entities[OVERHEAD_1].get("circadian")
