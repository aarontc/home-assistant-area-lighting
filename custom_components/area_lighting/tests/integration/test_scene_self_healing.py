"""Glitch-aware scene self-healing.

Out-of-band Hue changes (power-on default, RF dropout, unavailable→on
recovery) should be auto-healed back to the active scene rather than
latching the area to `manual`. Genuine manual changes (long after our
last command, bulb reachable throughout) still latch `manual`.
"""

from __future__ import annotations

import time

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_scene_self_heal_enabled_default_true(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The flag defaults to on and is exposed on the controller."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl.scene_self_heal_enabled is True


@pytest.mark.integration
async def test_scene_self_heal_flag_false_disables(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Setting scene_self_heal: false in config disables the feature."""
    network_room_config["area_lighting"]["scene_self_heal"] = False
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl.scene_self_heal_enabled is False


def _on_target(commanded_offset: float = 0.0) -> dict:
    """A simple on-state scene target stamped `commanded_offset` seconds ago."""
    return {
        "state": "on",
        "brightness": 10,
        "color_temp_kelvin": 2700,
        "commanded_at": time.monotonic() - commanded_offset,
        "transition": 0.0,
    }


def _light_turn_on_calls(service_calls, entity_id: str) -> list:
    return [
        c
        for c in service_calls
        if c.domain == "light" and c.service == "turn_on" and c.data.get("entity_id") == entity_id
    ]


@pytest.mark.integration
async def test_reassert_reapplies_target_and_restamps(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """handle_scene_drift_reassert issues a turn_on with the target attrs."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    eid = "light.network_room_overhead_1"
    ctrl._active_scene_targets = {eid: _on_target(commanded_offset=200.0)}

    service_calls.clear()
    await ctrl.handle_scene_drift_reassert(eid, "glitch_window")
    await hass.async_block_till_done()

    calls = _light_turn_on_calls(service_calls, eid)
    assert len(calls) == 1
    assert calls[0].data.get("brightness") == 10
    assert calls[0].data.get("color_temp_kelvin") == 2700
    # commanded_at re-stamped to ~now so the heal doesn't self-trigger.
    assert time.monotonic() - ctrl._active_scene_targets[eid]["commanded_at"] < 1.0
    assert not ctrl._state.is_manual


@pytest.mark.integration
async def test_reassert_loop_cap_gives_up_and_raises_issue(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """After SCENE_HEAL_MAX_ATTEMPTS heals, give up: latch manual + Repairs issue."""
    from homeassistant.helpers import issue_registry as ir

    from custom_components.area_lighting.const import (
        DOMAIN,
        SCENE_DRIFT_ISSUE_ID,
        SCENE_HEAL_MAX_ATTEMPTS,
    )

    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    eid = "light.network_room_overhead_1"

    for _ in range(SCENE_HEAL_MAX_ATTEMPTS):
        ctrl._active_scene_targets = {eid: _on_target(commanded_offset=200.0)}
        await ctrl.handle_scene_drift_reassert(eid, "glitch_window")
        await hass.async_block_till_done()
    assert not ctrl._state.is_manual  # healed each time so far

    # One more → over the cap → give up.
    ctrl._active_scene_targets = {eid: _on_target(commanded_offset=200.0)}
    await ctrl.handle_scene_drift_reassert(eid, "glitch_window")
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, f"{SCENE_DRIFT_ISSUE_ID}_network_room") is not None


async def _activate_with_target(hass, ctrl, eid, commanded_offset, transition):
    """Put the area in the ambient scene with one on-target for `eid`,
    seeded as 'on' so a later async_set fires a state-change event."""
    hass.states.async_set(eid, "on", {"brightness": 10, "color_temp_kelvin": 2700})
    await hass.async_block_till_done()
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 120.0  # area grace gone
    ctrl._active_scene_targets = {
        eid: {
            "state": "on",
            "brightness": 10,
            "color_temp_kelvin": 2700,
            "commanded_at": time.monotonic() - commanded_offset,
            "transition": transition,
        }
    }


@pytest.mark.integration
async def test_divergence_inside_glitch_window_heals(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """A jump 110s after a 60s-fade command (settle+60=124s) -> heal, not manual."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    eid = "light.network_room_overhead_1"
    await _activate_with_target(hass, ctrl, eid, commanded_offset=110.0, transition=60.0)

    service_calls.clear()
    hass.states.async_set(eid, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()

    assert not ctrl._state.is_manual
    assert _light_turn_on_calls(service_calls, eid), "expected a heal re-assert"


@pytest.mark.integration
async def test_divergence_after_glitch_window_marks_manual(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """A jump 200s after a 60s-fade command (> settle+60=124s) -> manual latch."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    eid = "light.network_room_overhead_1"
    await _activate_with_target(hass, ctrl, eid, commanded_offset=200.0, transition=60.0)

    service_calls.clear()
    hass.states.async_set(eid, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert not _light_turn_on_calls(service_calls, eid), "must not heal a real manual change"


@pytest.mark.integration
async def test_recovery_from_unavailable_heals_regardless_of_window(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """unavailable->on at a divergent value, long after command -> heal (tier 1)."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    eid = "light.network_room_overhead_1"
    await _activate_with_target(hass, ctrl, eid, commanded_offset=3600.0, transition=0.0)

    hass.states.async_set(eid, "unavailable", {})
    await hass.async_block_till_done()
    service_calls.clear()
    hass.states.async_set(eid, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()

    assert not ctrl._state.is_manual
    assert _light_turn_on_calls(service_calls, eid), "expected recovery heal"


@pytest.mark.integration
async def test_kill_switch_off_falls_back_to_manual(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """With scene_self_heal disabled, an in-window glitch latches manual."""
    network_room_config["area_lighting"]["scene_self_heal"] = False
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    eid = "light.network_room_overhead_1"
    await _activate_with_target(hass, ctrl, eid, commanded_offset=110.0, transition=60.0)

    service_calls.clear()
    hass.states.async_set(eid, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert not _light_turn_on_calls(service_calls, eid)


@pytest.mark.integration
async def test_post_settle_selfcheck_heals_during_fade_glitch(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """A glitch that lands during the fade (ignored by the event path as
    'settling') is healed by the one-shot post-settle self-check."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    eid = "light.network_room_overhead_1"
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    # Target commanded "now"; bulb currently sits at a divergent value.
    ctrl._active_scene_targets = {eid: _on_target(commanded_offset=0.0)}
    hass.states.async_set(eid, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()

    service_calls.clear()
    # Fire the self-check callback directly (the scheduling is asserted below).
    ctrl._run_post_settle_selfcheck()
    await hass.async_block_till_done()

    assert _light_turn_on_calls(service_calls, eid), "self-check should heal the drift"


@pytest.mark.integration
async def test_activating_scene_schedules_selfcheck(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """A visual-scene activation schedules exactly one pending self-check."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("daylight", ActivationSource.USER, transition=5.0)
    assert ctrl._heal_selfcheck_handle is not None
    # Tidy up the pending loop.call_later so it doesn't fire after the test.
    ctrl._heal_selfcheck_handle.cancel()
    ctrl._heal_selfcheck_handle = None


@pytest.mark.integration
async def test_diagnostic_snapshot_exposes_heal_fields(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    snap = ctrl.diagnostic_snapshot()
    assert snap["scene_self_heal_enabled"] is True
    assert snap["scene_heal_attempts"] == {}


@pytest.mark.integration
async def test_all_off_clears_heal_state_and_issue(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    from homeassistant.helpers import issue_registry as ir

    from custom_components.area_lighting.const import DOMAIN, SCENE_DRIFT_ISSUE_ID

    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._heal_attempts = {"light.network_room_overhead_1": [time.monotonic()]}
    ctrl._raise_scene_drift_issue("light.network_room_overhead_1")

    await ctrl.handle_lights_all_off()
    await hass.async_block_till_done()

    assert ctrl._heal_attempts == {}
    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, f"{SCENE_DRIFT_ISSUE_ID}_network_room") is None


@pytest.mark.integration
async def test_incident_replay_right_w_glitch_and_left_w_recovery(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """Reproduce the upstairs_bathroom incident with the two fixture lights.

    ambient fade applied; ~110s later one bulb jumps to 228/3086 (Hue glitch),
    the other goes unavailable then recovers. With self-healing on, the area
    stays in 'ambient' and never latches 'manual'.
    """
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    right = "light.network_room_overhead_1"
    left = "light.network_room_overhead_2"

    for eid in (right, left):
        hass.states.async_set(eid, "on", {"brightness": 10, "color_temp_kelvin": 2700})
    await hass.async_block_till_done()

    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 120.0
    commanded = time.monotonic() - 110.0
    ctrl._active_scene_targets = {
        right: {
            "state": "on",
            "brightness": 10,
            "color_temp_kelvin": 2700,
            "commanded_at": commanded,
            "transition": 60.0,
        },
        left: {
            "state": "on",
            "brightness": 10,
            "color_temp_kelvin": 2700,
            "commanded_at": commanded,
            "transition": 60.0,
        },
    }

    # right_w glitches to a foreign value inside the heal window -> healed.
    hass.states.async_set(right, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()
    assert not ctrl._state.is_manual
    assert _light_turn_on_calls(service_calls, right)

    # left_w drops out, then recovers to a divergent value -> healed.
    hass.states.async_set(left, "unavailable", {})
    await hass.async_block_till_done()
    hass.states.async_set(left, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()
    assert not ctrl._state.is_manual
    assert _light_turn_on_calls(service_calls, left)

    assert ctrl.current_scene == "ambient"
