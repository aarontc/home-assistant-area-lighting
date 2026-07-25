"""Demand-response shedding: scene activations."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _bright_entities(n: int) -> dict:
    return {f"light.bright_room_{i}": {"state": "on", "brightness": 200} for i in range(1, n + 1)}


def _config(n_lights: int, scene_on: int) -> dict:
    """Area with `n_lights` bulbs and a 'bright' scene that turns `scene_on` on."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "bright_room",
                    "name": "Bright Room",
                    "event_handlers": True,
                    "lights": [
                        {"id": f"light.bright_room_{i}", "roles": ["dimming"]}
                        for i in range(1, n_lights + 1)
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "bright", "name": "Bright", "entities": _bright_entities(scene_on)},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict, n_lights: int) -> None:
    for i in range(1, n_lights + 1):
        hass.states.async_set(f"light.bright_room_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _on_off(service_calls):
    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    off = {
        c.data["entity_id"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    return on, off


@pytest.mark.integration
async def test_dr_sheds_six_light_scene_to_keep_two(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert on == {"light.bright_room_1", "light.bright_room_2"}
    assert {f"light.bright_room_{i}" for i in (3, 4, 5, 6)} <= off
    # Tracking marks shed bulbs off so self-heal / manual-detection leave them.
    assert ctrl._active_scene_targets["light.bright_room_4"]["state"] == "off"
    assert ctrl.dr_shed_ids == frozenset({f"light.bright_room_{i}" for i in (3, 4, 5, 6)})


@pytest.mark.integration
async def test_dr_sheds_two_light_scene_to_keep_one(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    # 25-bulb area, scene only turns on 2 -> n=2 -> keep 1.
    await _setup(hass, _config(25, 2), 25)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert on == {"light.bright_room_1"}
    assert "light.bright_room_2" in off


@pytest.mark.integration
async def test_no_shed_when_dr_inactive(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, _ = _on_off(service_calls)
    assert on == {f"light.bright_room_{i}" for i in range(1, 7)}
    assert ctrl.dr_shed_ids == frozenset()


@pytest.mark.integration
async def test_external_scene_tracking_is_filtered(hass: HomeAssistant, helper_entities) -> None:
    # handle_scene_activated tracks external scene.turn_on; under DR the
    # tracked targets must mark shed bulbs off (Task 8 filters the apply).
    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True

    await ctrl.handle_scene_activated("bright")
    await hass.async_block_till_done()

    assert ctrl._active_scene_targets["light.bright_room_1"]["state"] == "on"
    assert ctrl._active_scene_targets["light.bright_room_6"]["state"] == "off"


@pytest.mark.integration
async def test_diagnostics_expose_demand_response(hass: HomeAssistant, helper_entities) -> None:
    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    snap = ctrl.diagnostic_snapshot()
    assert snap["demand_response_active"] is True
    assert set(snap["demand_response_shed"]) == {f"light.bright_room_{i}" for i in (3, 4, 5, 6)}


def _skeleton_exclude_config() -> dict:
    """6-light area with a skeleton 'all' scene excluding light.g_room_2."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "g_room",
                    "name": "G Room",
                    "event_handlers": True,
                    "lights": [
                        {"id": f"light.g_room_{i}", "roles": ["dimming"]} for i in range(1, 7)
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "all", "name": "All", "group_exclude": ["light.g_room_2"]},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


@pytest.mark.integration
async def test_skeleton_group_exclude_light_left_untracked_and_untouched(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """group_exclude means LEFT UNTOUCHED: a physically-on excluded light
    gets no command from either apply path, has no entry in the tracked
    targets (an off-target there would let self-heal turn it off), and
    survives a demand-response re-activation unchanged. The shed is still
    sized over the exclude-filtered on-set, matching scene.py."""
    for i in range(1, 7):
        hass.states.async_set(f"light.g_room_{i}", "off", {})
    # The excluded light is physically ON before the scene lands.
    hass.states.async_set("light.g_room_2", "on", {"brightness": 120})
    assert await async_setup_component(hass, "area_lighting", _skeleton_exclude_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    ctrl = hass.data["area_lighting"]["controllers"]["g_room"]
    _toggles(hass)._demand_response_active = True

    # Scene-entity path (scene.py _apply_skeleton, the reference behavior).
    service_calls.clear()
    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.g_room_all"}, blocking=True
    )
    await hass.async_block_till_done()
    scene_on, scene_off = _on_off(service_calls)

    # Controller path.
    service_calls.clear()
    await ctrl._activate_scene("all", ActivationSource.USER)
    await hass.async_block_till_done()
    ctrl_on, ctrl_off = _on_off(service_calls)

    # n=5 on-bulbs (g_room_2 excluded) -> keep 3: g_room_1, g_room_3, g_room_4.
    assert scene_on == {"light.g_room_1", "light.g_room_3", "light.g_room_4"}
    assert ctrl_on == scene_on
    assert ctrl_off == scene_off
    assert "light.g_room_2" not in (scene_on | scene_off)
    assert "light.g_room_2" not in (ctrl_on | ctrl_off)
    assert ctrl.dr_shed_ids == frozenset({"light.g_room_5", "light.g_room_6"})
    assert ctrl._active_scene_targets["light.g_room_1"]["state"] == "on"
    assert ctrl._active_scene_targets["light.g_room_5"]["state"] == "off"
    # No entry at all: an off-target would make self-heal turn it off.
    assert "light.g_room_2" not in ctrl._active_scene_targets

    # A demand-response re-activation replays the scene, which skips
    # excluded lights entirely, so the physically-on excluded light must
    # survive it untouched.
    service_calls.clear()
    await ctrl.reactivate_for_demand_response()
    await hass.async_block_till_done()
    reactivate_on, reactivate_off = _on_off(service_calls)
    # No command of any kind for the excluded light, and its (seeded) HA
    # state is still on: the re-activation left it fully untouched.
    assert "light.g_room_2" not in (reactivate_on | reactivate_off)
    assert hass.states.get("light.g_room_2").state == "on"


@pytest.mark.integration
async def test_group_excluded_light_turning_on_does_not_latch_manual(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """An on-state-change for a group-excluded light while its skeleton
    scene is active is not a manual override: the scene never manages that
    light, so the area must stay in the scene state."""
    import time as _time

    for i in range(1, 7):
        hass.states.async_set(f"light.g_room_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", _skeleton_exclude_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    ctrl = hass.data["area_lighting"]["controllers"]["g_room"]
    _toggles(hass)._demand_response_active = True

    await ctrl._activate_scene("all", ActivationSource.USER)
    await hass.async_block_till_done()
    # Expire the post-activation grace window so the event is judged on
    # its own merits, not swallowed by the grace skip.
    ctrl._state.last_scene_change_monotonic = _time.monotonic() - 30.0

    hass.states.async_set("light.g_room_2", "on", {"brightness": 180})
    await hass.async_block_till_done()

    assert not ctrl._state.is_manual
    assert ctrl.current_scene == "all"


@pytest.mark.integration
async def test_startup_restore_populates_scene_shed_diagnostics(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A controller restored into a scene with DR active exposes the shed
    set in diagnostics immediately, without driving any lights."""
    from custom_components.area_lighting.controller import AreaLightingController

    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True
    ctrl._state.transition_to_scene("bright", ActivationSource.USER)
    saved = ctrl.state_dict()

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    fresh.load_persisted_state(saved)
    service_calls.clear()
    fresh.reconcile_startup_state()
    await hass.async_block_till_done()

    snap = fresh.diagnostic_snapshot()
    assert set(snap["demand_response_shed"]) == {f"light.bright_room_{i}" for i in (3, 4, 5, 6)}
    assert len(service_calls) == 0


@pytest.mark.integration
async def test_startup_restore_rebuilds_scene_targets_without_driving_lights(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A controller restored into a scene rebuilds its target tracking
    (with the DR shed filter applied and command metadata stamped) so
    manual detection has targets to compare against, without driving any
    lights: physical state is preserved across restart."""
    from custom_components.area_lighting.controller import AreaLightingController

    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True
    ctrl._state.transition_to_scene("bright", ActivationSource.USER)
    saved = ctrl.state_dict()

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    fresh.load_persisted_state(saved)
    service_calls.clear()
    fresh.reconcile_startup_state()
    await hass.async_block_till_done()

    assert fresh._active_scene_targets["light.bright_room_1"]["state"] == "on"
    assert fresh._active_scene_targets["light.bright_room_4"]["state"] == "off"
    for target in fresh._active_scene_targets.values():
        assert "commanded_at" in target
        assert target["transition"] == pytest.approx(0.0)
    assert len(service_calls) == 0


@pytest.mark.integration
async def test_dimmed_manual_relight_of_shed_bulb_latches_manual(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A DR-shed bulb has an explicit off target; the user relighting it
    while the area is dimmed is a genuine manual override, not raise/lower
    stepping, so the dimmed suppression must not swallow it."""
    import time as _time

    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()
    assert "light.bright_room_4" in ctrl.dr_shed_ids

    ctrl._state.mark_dimmed()
    # Expire the area grace and every per-entity settle/glitch window so
    # the event is judged on its own merits.
    ctrl._state.last_scene_change_monotonic = _time.monotonic() - 200.0
    for target in ctrl._active_scene_targets.values():
        target["commanded_at"] = _time.monotonic() - 200.0

    hass.states.async_set("light.bright_room_4", "on", {"brightness": 120})
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert not ctrl._state.dimmed


@pytest.mark.integration
async def test_dimmed_brightness_divergence_on_kept_bulb_stays_suppressed(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """The dimmed suppression still holds for on-target bulbs: raise/lower
    stepping makes brightness divergence expected there, so it must not
    latch manual."""
    import time as _time

    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    # Kept bulb physically on at its scene brightness.
    hass.states.async_set("light.bright_room_1", "on", {"brightness": 200})
    await hass.async_block_till_done()

    ctrl._state.mark_dimmed()
    ctrl._state.last_scene_change_monotonic = _time.monotonic() - 200.0
    for target in ctrl._active_scene_targets.values():
        target["commanded_at"] = _time.monotonic() - 200.0

    # Raise/lower stepping: brightness diverges on an on-target bulb.
    hass.states.async_set("light.bright_room_1", "on", {"brightness": 90})
    await hass.async_block_till_done()

    assert not ctrl._state.is_manual
    assert ctrl._state.dimmed


@pytest.mark.integration
async def test_alert_bypasses_demand_response(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    cfg = _config(6, 6)
    cfg["area_lighting"]["alert_patterns"] = {
        "flash": {"steps": [{"target": "all", "state": "on", "brightness": 255}], "restore": False}
    }
    await _setup(hass, cfg, 6)
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await hass.services.async_call(
        "area_lighting", "alert", {"area_id": "bright_room", "pattern": "flash"}, blocking=True
    )
    await hass.async_block_till_done()

    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    # Alerts bypass DR: every bulb flashes, none are shed.
    assert on == {f"light.bright_room_{i}" for i in range(1, 7)}


def _register_chrono_light_recorder(
    hass: HomeAssistant,
    gate: asyncio.Event | None = None,
    reached: asyncio.Event | None = None,
) -> list[tuple[str, str]]:
    """Replace the mocked light services with one chronological recorder.

    The service_calls fixture groups calls by service, so it cannot answer
    ordering questions ("which command landed last on this bulb?").

    When ``gate`` and ``reached`` are given, the first recorded call sets
    ``reached`` and parks until the test sets ``gate``. That pins the alert
    inside its first light command, so a mid-alert flag flip lands at a
    known point without any wall-clock sleeps.
    """
    calls: list[tuple[str, str]] = []

    async def _record(call) -> None:
        first = not calls
        calls.append((call.service, call.data["entity_id"]))
        if gate is not None and reached is not None and first:
            reached.set()
            await gate.wait()

    hass.services.async_register("light", "turn_on", _record)
    hass.services.async_register("light", "turn_off", _record)
    return calls


def _alert_config() -> dict:
    cfg = _config(6, 6)
    cfg["area_lighting"]["alert_patterns"] = {
        "flash": {
            "steps": [{"target": "all", "state": "on", "brightness": 255}],
            "restore": True,
        }
    }
    return cfg


@pytest.mark.integration
async def test_dr_flip_during_alert_applies_after_restore(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A DR edge arriving mid-alert is deferred, then applied after restore.

    The alert is parked deterministically inside its first light command
    (recorder gate), the flag flips through the real setter, then the
    alert finishes. The flip's re-activation must not fight the running
    alert (it skips alert-owning areas), and the alert's finally block
    must re-drive the area so the shed lands once the captured states are
    restored.
    """
    await _setup(hass, _alert_config(), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    # Lit scene pre-alert: all bulbs physically on, DR off.
    for i in range(1, 7):
        hass.states.async_set(f"light.bright_room_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    gate = asyncio.Event()
    reached = asyncio.Event()
    chrono = _register_chrono_light_recorder(hass, gate=gate, reached=reached)
    alert_task = hass.async_create_task(
        hass.services.async_call(
            "area_lighting",
            "alert",
            {"area_id": "bright_room", "pattern": "flash"},
            blocking=True,
        )
    )
    await reached.wait()
    assert ctrl._alert_active is True
    # Flip DR on mid-alert via the real setter, then let the alert finish.
    await _toggles(hass).async_set_demand_response_active(True)
    gate.set()
    await alert_task
    await hass.async_block_till_done()

    # Final commanded polarity per bulb: restore relights everything,
    # then the post-alert re-activation sheds the tail again.
    final = {eid: svc for svc, eid in chrono}
    assert final["light.bright_room_1"] == "turn_on"
    assert final["light.bright_room_2"] == "turn_on"
    for i in (3, 4, 5, 6):
        assert final[f"light.bright_room_{i}"] == "turn_off"
    assert ctrl.dr_shed_ids == frozenset({f"light.bright_room_{i}" for i in (3, 4, 5, 6)})


@pytest.mark.integration
async def test_dr_off_during_alert_restores_shed(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A DR-off edge arriving mid-alert restores shed bulbs after the alert.

    The flip's re-activation is deferred while the alert owns the lights.
    The flag changed during the alert, so the finally block must re-drive
    the area through its normal activation path so previously shed bulbs
    are relit and the shed set empties.
    """
    await _setup(hass, _alert_config(), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()
    # Physical shed state pre-alert: kept bulbs on, shed tail off.
    for i in (1, 2):
        hass.states.async_set(f"light.bright_room_{i}", "on", {"brightness": 200})
    for i in (3, 4, 5, 6):
        hass.states.async_set(f"light.bright_room_{i}", "off", {})
    assert ctrl.dr_shed_ids == frozenset({f"light.bright_room_{i}" for i in (3, 4, 5, 6)})

    gate = asyncio.Event()
    reached = asyncio.Event()
    chrono = _register_chrono_light_recorder(hass, gate=gate, reached=reached)
    alert_task = hass.async_create_task(
        hass.services.async_call(
            "area_lighting",
            "alert",
            {"area_id": "bright_room", "pattern": "flash"},
            blocking=True,
        )
    )
    await reached.wait()
    assert ctrl._alert_active is True
    # Flip DR off mid-alert via the real setter, then let the alert finish.
    await _toggles(hass).async_set_demand_response_active(False)
    gate.set()
    await alert_task
    await hass.async_block_till_done()

    # Restore put shed bulbs back to their captured-off state; the finally
    # block re-activation must then relight them because DR is no longer
    # active.
    final = {eid: svc for svc, eid in chrono}
    for i in range(1, 7):
        assert final[f"light.bright_room_{i}"] == "turn_on"
    assert ctrl.dr_shed_ids == frozenset()
    assert ctrl._active_scene_targets["light.bright_room_4"]["state"] == "on"
