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
