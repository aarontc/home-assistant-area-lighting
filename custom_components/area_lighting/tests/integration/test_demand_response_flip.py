"""Demand-response flag flips re-drive areas through normal activation.

There is no separate reconcile subsystem and no lock: the global setter
fans out reactivate_for_demand_response, which replays each non-manual,
non-off, non-alert area's current state through the SAME activation path
every other trigger uses. The DR filter applied at target resolution does
the rest. These tests assert the observable outcomes of a flip and the
structural guarantee that activations never wait on demand-response
state.
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _config() -> dict:
    entities = {f"light.den_{i}": {"state": "on", "brightness": 200} for i in range(1, 7)}
    dim_entities = {f"light.den_{i}": {"state": "on", "brightness": 100} for i in range(1, 4)}
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "den",
                    "name": "Den",
                    "event_handlers": True,
                    "lights": [{"id": f"light.den_{i}", "roles": ["dimming"]} for i in range(1, 7)],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "bright", "name": "Bright", "entities": entities},
                        {"id": "dim", "name": "Dim", "entities": dim_entities},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _on_off(service_calls) -> tuple[set, set]:
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
async def test_flip_on_sheds_already_lit_area(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """Flipping DR on via the real setter re-fires the active scene with the
    shed filter applied: the config-order tail goes off, the kept head never
    receives a turn_off, and tracking reflects the shed."""
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    service_calls.clear()
    await _toggles(hass).async_set_demand_response_active(True)
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    shed = {f"light.den_{i}" for i in (3, 4, 5, 6)}
    assert off == shed
    assert not on & shed
    assert ctrl.dr_shed_ids == frozenset(shed)
    assert ctrl._state.scene_slug == "bright"
    assert ctrl._active_scene_targets["light.den_1"]["state"] == "on"
    assert ctrl._active_scene_targets["light.den_4"]["state"] == "off"


@pytest.mark.integration
async def test_flip_off_restores_shed_area(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """Flipping DR off via the real setter re-fires the active scene without
    the filter: the shed bulbs are relit, nothing is turned off, and the shed
    tracking empties."""
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    _toggles(hass)._demand_response_active = True
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()
    # Physical result of the shed activation: kept on, shed off.
    for i in (1, 2):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    for i in (3, 4, 5, 6):
        hass.states.async_set(f"light.den_{i}", "off", {})

    service_calls.clear()
    await _toggles(hass).async_set_demand_response_active(False)
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert {f"light.den_{i}" for i in (3, 4, 5, 6)} <= on
    assert off == set()
    assert ctrl.dr_shed_ids == frozenset()
    assert ctrl._active_scene_targets["light.den_4"]["state"] == "on"


@pytest.mark.integration
async def test_flip_leaves_manual_area_untouched(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """Manual areas keep user intent: a flip issues no light commands and
    does not change state."""
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    ctrl._state.transition_to_manual()

    service_calls.clear()
    await _toggles(hass).async_set_demand_response_active(True)
    await hass.async_block_till_done()

    assert [c for c in service_calls if c.domain == "light"] == []
    assert ctrl._state.is_manual


@pytest.mark.integration
async def test_flip_leaves_off_area_untouched(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    assert ctrl._state.is_off

    service_calls.clear()
    await _toggles(hass).async_set_demand_response_active(True)
    await hass.async_block_till_done()

    assert [c for c in service_calls if c.domain == "light"] == []
    assert ctrl._state.is_off


@pytest.mark.integration
async def test_flip_defers_to_running_alert(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """While an alert owns the lights, a flip issues no commands for that
    area; execute_alert's finally block re-drives it after the restore."""
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()
    ctrl._alert_active = True

    service_calls.clear()
    await _toggles(hass).async_set_demand_response_active(True)
    await hass.async_block_till_done()

    assert [c for c in service_calls if c.domain == "light"] == []
    ctrl._alert_active = False


def _leader_follower_config() -> dict:
    """Two-area config: den leads, study follows; both define bright and dim."""

    def lights(area: str) -> list[dict]:
        return [{"id": f"light.{area}_{i}", "roles": ["dimming"]} for i in range(1, 5)]

    def scenes(area: str) -> list[dict]:
        bright = {f"light.{area}_{i}": {"state": "on", "brightness": 200} for i in range(1, 5)}
        dim = {f"light.{area}_{i}": {"state": "on", "brightness": 80} for i in (1, 2)}
        return [
            {"id": "circadian", "name": "Circadian"},
            {"id": "bright", "name": "Bright", "entities": bright},
            {"id": "dim", "name": "Dim", "entities": dim},
            {"id": "off", "name": "Off"},
        ]

    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "den",
                    "name": "Den",
                    "event_handlers": True,
                    "lights": lights("den"),
                    "scenes": scenes("den"),
                },
                {
                    "id": "study",
                    "name": "Study",
                    "event_handlers": True,
                    "lights": lights("study"),
                    "scenes": scenes("study"),
                    "leader_area_id": "den",
                },
            ]
        }
    }


@pytest.mark.integration
async def test_flip_does_not_propagate_leader_scene_to_follower(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """The setter re-drives EVERY controller itself, followers included, so
    the leader's re-drive must not also propagate its scene to followers: a
    follower independently in a different scene would be overwritten by the
    leader's slug instead of having its own scene re-driven."""
    for area in ("den", "study"):
        for i in range(1, 5):
            hass.states.async_set(f"light.{area}_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", _leader_follower_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    den = hass.data["area_lighting"]["controllers"]["den"]
    study = hass.data["area_lighting"]["controllers"]["study"]

    # Leader in bright; follower independently in dim (its own USER scene).
    for i in range(1, 5):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    for i in (1, 2):
        hass.states.async_set(f"light.study_{i}", "on", {"brightness": 80})
    await den._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()
    await study._activate_scene("dim", ActivationSource.USER)
    await hass.async_block_till_done()
    assert study._state.scene_slug == "dim"

    await _toggles(hass).async_set_demand_response_active(True)
    await hass.async_block_till_done()

    # Each area shed its OWN scene; the follower was not switched to bright.
    assert den._state.scene_slug == "bright"
    assert study._state.scene_slug == "dim"
    assert den.dr_shed_ids == frozenset({"light.den_3", "light.den_4"})
    assert study.dr_shed_ids == frozenset({"light.study_2"})

    await _toggles(hass).async_set_demand_response_active(False)
    await hass.async_block_till_done()

    assert den._state.scene_slug == "bright"
    assert study._state.scene_slug == "dim"
    assert study._state.source == ActivationSource.USER
    assert den.dr_shed_ids == frozenset()
    assert study.dr_shed_ids == frozenset()


@pytest.mark.integration
async def test_activation_never_waits_on_demand_response(
    hass: HomeAssistant, helper_entities, service_calls, monkeypatch
) -> None:
    """The key property of the architecture: normal activations and
    demand-response re-activations share NO lock.

    Structurally, the controller has no _dr_lock (or any DR lock) left.
    Behaviorally, a DR re-activation parked mid-flight must not delay a
    concurrent normal scene activation: the activation runs to completion
    while the re-activation is still parked, then both finish.
    """
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    assert not hasattr(ctrl, "_dr_lock")

    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    # Park the FIRST _apply_light_state call (it comes from the flip's
    # re-activation) until the test releases it; later calls pass through.
    gate = asyncio.Event()
    reached = asyncio.Event()
    original_apply = ctrl._apply_light_state

    async def gated_apply(entity_id, state_data, transition=None):
        if not reached.is_set():
            reached.set()
            await gate.wait()
        await original_apply(entity_id, state_data, transition)

    monkeypatch.setattr(ctrl, "_apply_light_state", gated_apply)

    await _toggles(hass).async_set_demand_response_active(True)
    await reached.wait()  # the re-activation is parked mid-apply

    # A normal activation started now must complete while the DR
    # re-activation is still parked: no shared lock, no waiting.
    activation = hass.async_create_task(ctrl._activate_scene("dim", ActivationSource.USER))
    for _ in range(25):
        await asyncio.sleep(0)
    assert activation.done()
    assert activation.exception() is None
    assert ctrl._state.scene_slug == "dim"

    # Release the parked re-activation; everything drains cleanly.
    gate.set()
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_flip_leaves_dimmed_area_untouched(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A dimmed area keeps user intent, exactly like a manual one.

    Re-driving it would call _activate_scene, whose transition_to_scene
    clears `dimmed` and `previous_scene`, so a room dimmed at 16:55 would
    jump back to full scene brightness at 17:00: demand response making a
    room BRIGHTER at the moment the shed starts, and again at 20:00.

    Dimming is a relative step with no stored level, so the dim cannot be
    re-applied after a re-drive; the only way to honour it is not to re-drive.
    A dimmed room is already drawing well under its scene brightness, and any
    later activation there still sheds normally.
    """
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    await ctrl._activate_scene("bright", ActivationSource.USER)
    ctrl._state.mark_dimmed()
    await hass.async_block_till_done()

    service_calls.clear()
    await _toggles(hass).async_set_demand_response_active(True)
    await hass.async_block_till_done()

    assert [c for c in service_calls if c.domain == "light"] == []
    assert ctrl._state.dimmed is True
    assert ctrl._state.previous_scene == "bright"


@pytest.mark.integration
async def test_flip_off_leaves_dimmed_area_untouched(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """The 20:00 flip must not un-dim either."""
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    await _toggles(hass).async_set_demand_response_active(True)
    await ctrl._activate_scene("bright", ActivationSource.USER)
    ctrl._state.mark_dimmed()
    await hass.async_block_till_done()

    service_calls.clear()
    await _toggles(hass).async_set_demand_response_active(False)
    await hass.async_block_till_done()

    assert [c for c in service_calls if c.domain == "light"] == []
    assert ctrl._state.dimmed is True


@pytest.mark.integration
async def test_flip_off_clears_shed_set_on_skipped_areas(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """Skipping an area's re-drive must still drop its shed set.

    `dr_shed_ids` means "what the current activation shed for demand
    response". Once the flag is off it describes nothing, but the early
    return for manual/off/dimmed areas left it populated. It is read by the
    manual-detection bypasses in event_handlers (an on-report for a shed bulb
    is allowed past the dimmed and circadian skips), so a stale set kept
    changing behaviour long after the window closed.
    """
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    await _toggles(hass).async_set_demand_response_active(True)
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids, "precondition: the scene should have shed bulbs"

    ctrl._state.transition_to_manual()
    await _toggles(hass).async_set_demand_response_active(False)
    await hass.async_block_till_done()

    assert ctrl.dr_shed_ids == frozenset()


@pytest.mark.integration
async def test_flip_off_clears_shed_set_on_dimmed_area(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    await _toggles(hass).async_set_demand_response_active(True)
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids

    ctrl._state.mark_dimmed()
    await _toggles(hass).async_set_demand_response_active(False)
    await hass.async_block_till_done()

    assert ctrl.dr_shed_ids == frozenset()
