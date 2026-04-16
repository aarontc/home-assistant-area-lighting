"""Integration tests for leader/follower area relationships."""

from __future__ import annotations

import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import (
    ActivationSource,
    LightingState,
)


def _bath_closet_config(**closet_extra) -> dict:
    """Return a two-area config with a leader (bath) and a follower (closet).

    `closet_extra` is merged into the closet area dict so individual tests
    can set follow_leader_deactivation, omit leader_area_id, etc.
    """
    closet = {
        "id": "closet",
        "name": "Closet",
        "event_handlers": True,
        "lights": [{"id": "light.closet_overhead", "roles": ["dimming"]}],
        "scenes": [
            {"id": "ambient", "name": "Ambient"},
            {"id": "circadian", "name": "Circadian"},
            {"id": "evening", "name": "Evening"},
            {"id": "off", "name": "Off"},
        ],
        "leader_area_id": "bath",
    }
    closet.update(closet_extra)
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "bath",
                    "name": "Bath",
                    "event_handlers": True,
                    "lights": [{"id": "light.bath_overhead", "roles": ["dimming"]}],
                    "scenes": [
                        {"id": "ambient", "name": "Ambient"},
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "christmas", "name": "Christmas"},
                        {"id": "movie", "name": "Movie"},
                        {"id": "off", "name": "Off"},
                    ],
                },
                closet,
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> bool:
    result = await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    return result


@pytest.mark.integration
async def test_invalid_leader_chain_fails_setup(
    hass: HomeAssistant, helper_entities, caplog
) -> None:
    """A chained config surfaces as a setup error."""
    caplog.set_level(logging.ERROR, logger="custom_components.area_lighting")
    hass.states.async_set("light.bath_overhead", "off")
    hass.states.async_set("light.kitchen_overhead", "off")
    hass.states.async_set("light.closet_overhead", "off")
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "lights": [{"id": "light.kitchen_overhead", "roles": ["dimming"]}],
                    "scenes": [{"id": "off", "name": "Off"}],
                },
                {
                    "id": "bath",
                    "name": "Bath",
                    "lights": [{"id": "light.bath_overhead", "roles": ["dimming"]}],
                    "scenes": [{"id": "off", "name": "Off"}],
                    "leader_area_id": "kitchen",
                },
                {
                    "id": "closet",
                    "name": "Closet",
                    "lights": [{"id": "light.closet_overhead", "roles": ["dimming"]}],
                    "scenes": [{"id": "off", "name": "Off"}],
                    "leader_area_id": "bath",
                },
            ]
        }
    }
    assert not await _setup(hass, cfg)
    assert any("cannot be chained" in rec.message for rec in caplog.records)


@pytest.mark.integration
async def test_controllers_are_wired_after_setup(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.bath_overhead", "off")
    hass.states.async_set("light.closet_overhead", "off")
    assert await _setup(hass, _bath_closet_config())
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    assert closet.leader is bath
    assert bath.followers == [closet]
    assert bath.leader is None
    assert closet.followers == []


@pytest.mark.integration
async def test_controller_with_no_leader_has_no_references(
    hass: HomeAssistant, helper_entities
) -> None:
    hass.states.async_set("light.bath_overhead", "off")
    hass.states.async_set("light.closet_overhead", "off")
    cfg = _bath_closet_config()
    # Drop the leader_area_id from closet
    cfg["area_lighting"]["areas"][1].pop("leader_area_id")
    assert await _setup(hass, cfg)
    controllers = hass.data["area_lighting"]["controllers"]
    assert controllers["closet"].leader is None
    assert controllers["bath"].followers == []


async def _force_scene(ctrl, scene_slug, source=ActivationSource.USER):
    """Set the controller state to `scene_slug` without running side effects."""
    ctrl._state.transition_to_scene(scene_slug, source)


@pytest.mark.integration
async def test_scenario_a_follower_motion_mirrors_leader_evening(
    hass: HomeAssistant, helper_entities
) -> None:
    """Leader in evening; motion on follower → follower activates evening."""
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "off")
    assert await _setup(hass, _bath_closet_config())
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    await _force_scene(bath, "evening")

    await closet.handle_motion_on()
    await hass.async_block_till_done()

    assert closet._state.scene_slug == "evening"
    assert closet._state.state == LightingState.SCENE


@pytest.mark.integration
async def test_scenario_a_leader_off_follower_uses_default(
    hass: HomeAssistant, helper_entities
) -> None:
    """Leader off; motion on follower → follower uses its default on-scene."""
    hass.states.async_set("light.bath_overhead", "off")
    hass.states.async_set("light.closet_overhead", "off")
    assert await _setup(hass, _bath_closet_config())
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    bath._state.transition_to_off(ActivationSource.USER)

    await closet.handle_motion_on()
    await hass.async_block_till_done()

    # Default on-scene from determine_on_action is "circadian"; accept either
    # CIRCADIAN state or a scene-state with slug circadian, but not "evening".
    assert closet._state.scene_slug != "evening"
    assert closet._state.is_on


@pytest.mark.integration
async def test_scenario_a_leader_manual_follower_uses_default(
    hass: HomeAssistant, helper_entities
) -> None:
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "off")
    assert await _setup(hass, _bath_closet_config())
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    bath._state.transition_to_manual()

    await closet.handle_motion_on()
    await hass.async_block_till_done()

    assert closet._state.scene_slug != "evening"
    assert closet._state.is_on


@pytest.mark.integration
async def test_scenario_a_leader_ambient_follower_uses_default(
    hass: HomeAssistant, helper_entities
) -> None:
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "off")
    assert await _setup(hass, _bath_closet_config())
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    bath._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)

    await closet.handle_motion_on()
    await hass.async_block_till_done()

    assert closet._state.scene_slug != "evening"
    assert closet._state.is_on


@pytest.mark.integration
async def test_scenario_a_leader_scene_not_on_follower_uses_default(
    hass: HomeAssistant, helper_entities
) -> None:
    """Leader in christmas (follower has no christmas) → follower uses default."""
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "off")
    assert await _setup(hass, _bath_closet_config())
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    bath._state.transition_to_scene("christmas", ActivationSource.HOLIDAY)

    await closet.handle_motion_on()
    await hass.async_block_till_done()

    # Closet has no christmas scene; expect fallback (not christmas).
    assert closet._state.scene_slug != "christmas"
    assert closet._state.is_on


@pytest.mark.integration
async def test_scenario_b_leader_scene_activates_on_follower(
    hass: HomeAssistant, helper_entities
) -> None:
    """Leader transitions to evening; follower (currently circadian) mirrors."""
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "on")
    assert await _setup(hass, _bath_closet_config())
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    # Both areas in circadian to start
    await closet._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    await bath._activate_scene("evening", ActivationSource.USER)
    await hass.async_block_till_done()

    assert closet._state.scene_slug == "evening"
    assert closet._state.source == ActivationSource.LEADER


@pytest.mark.integration
async def test_scenario_b_follower_off_is_left_alone(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "off")
    assert await _setup(hass, _bath_closet_config())
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    closet._state.transition_to_off(ActivationSource.USER)

    await bath._activate_scene("evening", ActivationSource.USER)
    await hass.async_block_till_done()

    assert closet._state.is_off


@pytest.mark.integration
async def test_scenario_b_follower_manual_is_left_alone(
    hass: HomeAssistant, helper_entities
) -> None:
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "on")
    assert await _setup(hass, _bath_closet_config())
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    closet._state.transition_to_manual()

    await bath._activate_scene("evening", ActivationSource.USER)
    await hass.async_block_till_done()

    assert closet._state.is_manual


@pytest.mark.integration
async def test_scenario_b_leader_off_default_no_propagation(
    hass: HomeAssistant, helper_entities
) -> None:
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "on")
    assert await _setup(hass, _bath_closet_config())
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    await closet._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    # Leader off; default follow_leader_deactivation=false → follower stays on
    from custom_components.area_lighting.const import SCENE_OFF_INTERNAL

    await bath._activate_scene(SCENE_OFF_INTERNAL, ActivationSource.USER)
    await hass.async_block_till_done()

    assert closet._state.is_on


@pytest.mark.integration
async def test_scenario_b_leader_off_with_follow_deactivation_turns_follower_off(
    hass: HomeAssistant, helper_entities
) -> None:
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "on")
    cfg = _bath_closet_config(follow_leader_deactivation=True)
    assert await _setup(hass, cfg)
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    await closet._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    from custom_components.area_lighting.const import SCENE_OFF_INTERNAL

    await bath._activate_scene(SCENE_OFF_INTERNAL, ActivationSource.USER)
    await hass.async_block_till_done()

    assert closet._state.is_off


@pytest.mark.integration
async def test_scenario_b_missing_slug_logs_warning_no_change(
    hass: HomeAssistant, helper_entities, caplog
) -> None:
    """When leader activates an on-scene the follower has no scene for,
    the follower logs a warning and leaves its own state unchanged."""
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "on")
    assert await _setup(hass, _bath_closet_config())
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    await closet._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    caplog.set_level(logging.WARNING)
    await bath._activate_scene("movie", ActivationSource.USER)
    await hass.async_block_till_done()

    # bath defines "movie" but closet does not; follower must stay in circadian
    assert closet._state.scene_slug == "circadian" or closet._state.is_circadian
    assert any("leader bath activated scene movie" in rec.message for rec in caplog.records)


@pytest.mark.integration
async def test_scenario_b_recursion_guard(hass: HomeAssistant, helper_entities) -> None:
    """Follower activation sourced by LEADER does not re-propagate.

    The recursion guard is defense-in-depth: chains are forbidden by the
    schema validator, so a follower has no followers of its own. But this
    test verifies the guard fires by spying on the follower's
    _propagate_to_followers method.
    """
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "on")
    assert await _setup(hass, _bath_closet_config())
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    await closet._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    # Spy on closet's _propagate_to_followers
    calls: list = []
    original = closet._propagate_to_followers

    def recording(slug, reason):
        calls.append((slug, reason))
        return original(slug, reason)

    closet._propagate_to_followers = recording

    await bath._activate_scene("evening", ActivationSource.USER)
    await hass.async_block_till_done()

    assert closet._state.scene_slug == "evening"
    # closet's activation was sourced by LEADER → it must NOT re-propagate
    assert calls == []


@pytest.mark.integration
async def test_follower_timer_runs_independently_of_leader(
    hass: HomeAssistant, helper_entities
) -> None:
    """Leader's timer reset does not touch the follower's timer."""
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "on")
    hass.states.async_set("binary_sensor.bath_motion", "off")
    hass.states.async_set("binary_sensor.closet_motion", "off")

    cfg = _bath_closet_config()
    # Give both areas motion sensors so motion timers exist
    cfg["area_lighting"]["areas"][0]["motion_light_motion_sensor_ids"] = [
        "binary_sensor.bath_motion",
    ]
    cfg["area_lighting"]["areas"][0]["motion_light_timer_durations"] = {"off": "00:08:00"}
    cfg["area_lighting"]["areas"][1]["motion_light_motion_sensor_ids"] = [
        "binary_sensor.closet_motion",
    ]
    cfg["area_lighting"]["areas"][1]["motion_light_timer_durations"] = {"off": "00:08:00"}
    assert await _setup(hass, cfg)
    controllers = hass.data["area_lighting"]["controllers"]
    bath = controllers["bath"]
    closet = controllers["closet"]

    # Start motion on both areas (scenario A will mirror bath's scene onto closet).
    await bath.handle_motion_on()
    await hass.async_block_till_done()
    await closet.handle_motion_on()
    await hass.async_block_till_done()
    await bath.handle_motion_off()
    await closet.handle_motion_off()
    await hass.async_block_till_done()

    bath_deadline_before = bath._motion_timer.deadline_utc
    closet_deadline_before = closet._motion_timer.deadline_utc

    # Leader motion retriggers, resetting bath's timer
    await bath.handle_motion_on()
    await hass.async_block_till_done()
    await bath.handle_motion_off()
    await hass.async_block_till_done()

    # Closet's timer deadline should not have changed
    assert closet._motion_timer.deadline_utc == closet_deadline_before
    # Bath's timer should have been reset (deadline changed)
    assert bath._motion_timer.deadline_utc != bath_deadline_before
