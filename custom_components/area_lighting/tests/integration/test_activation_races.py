"""Activations, timers and heals that interleave across an await."""

from __future__ import annotations

import asyncio
import time

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource

MOTION_SENSOR = "binary_sensor.network_room_motion_sensor_motion"


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _linked_config() -> dict:
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "theater",
                    "name": "Theater",
                    "event_handlers": True,
                    "lights": [{"id": "light.theater_overhead", "roles": ["dimming"]}],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "stairs", "name": "Stairs"},
                    ],
                },
                {
                    "id": "stairs",
                    "name": "Stairs",
                    "event_handlers": True,
                    "lights": [{"id": "light.stairs_upper", "roles": ["dimming"]}],
                    "scenes": [{"id": "circadian", "name": "Circadian"}],
                    "motion_light_motion_sensor_ids": ["binary_sensor.stairs_motion"],
                    "linked_motion": [
                        {
                            "remote_area": "theater",
                            "default": {"local_scene": "circadian", "remote_scene": None},
                            "when_remote_scene": {
                                "off": {"local_scene": "circadian", "remote_scene": "stairs"},
                            },
                        }
                    ],
                },
            ]
        }
    }


@pytest.mark.integration
async def test_scene_off_service_turns_off_ambience_owned_ambient(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """scene.turn_on on the off scene turns off an area ambience put in ambient.

    The scene tracker used to set the area off before lighting_off read it,
    so lighting_off saw a user-owned off and activated ambient again.
    """
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)

    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.network_room_off"}, blocking=True
    )
    await hass.async_block_till_done()

    assert ctrl._state.is_off


@pytest.mark.integration
async def test_off_yields_to_activation_made_while_lights_turn_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Off then On within the off's await leaves the area on, not reading off."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("evening", ActivationSource.USER)

    original_turn_off = ctrl._turn_off_all_lights

    async def turn_off_then_press_on(*args, **kwargs):
        await original_turn_off(*args, **kwargs)
        await ctrl._activate_scene("circadian", ActivationSource.USER)

    ctrl._turn_off_all_lights = turn_off_then_press_on

    await ctrl.lighting_force_off()
    await hass.async_block_till_done()

    assert ctrl._state.is_circadian
    assert ctrl._state.source == ActivationSource.USER


async def _motion_lit_with_timer_due(hass: HomeAssistant, ctrl) -> None:
    ctrl.motion_light_enabled = True
    hass.states.async_set(MOTION_SENSOR, "on")
    await ctrl.handle_motion_on()
    hass.states.async_set(MOTION_SENSOR, "off")
    await ctrl.handle_motion_off()
    await hass.async_block_till_done()
    assert ctrl._state.source == ActivationSource.MOTION


@pytest.mark.integration
async def test_motion_during_fade_relights_the_area(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Motion that arrives while the motion timer's fade awaits is not lost."""
    hass.states.async_set(MOTION_SENSOR, "off")
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await _motion_lit_with_timer_due(hass, ctrl)

    original_turn_off = ctrl._turn_off_all_lights

    async def turn_off_with_motion(*args, **kwargs):
        hass.states.async_set(MOTION_SENSOR, "on")
        await ctrl.handle_motion_on()
        await original_turn_off(*args, **kwargs)

    ctrl._turn_off_all_lights = turn_off_with_motion

    await ctrl._on_motion_timer()
    await hass.async_block_till_done()

    assert not ctrl._state.is_off
    assert ctrl._state.source == ActivationSource.MOTION


@pytest.mark.integration
async def test_motion_during_linked_cleanup_skips_the_fade(
    hass: HomeAssistant, helper_entities
) -> None:
    """Fresh motion while the timer cleans up linked areas keeps the room lit."""
    for eid in ("light.theater_overhead", "light.stairs_upper", "binary_sensor.stairs_motion"):
        hass.states.async_set(eid, "off")
    await _setup(hass, _linked_config())
    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await stairs_ctrl.handle_motion_off()
    await hass.async_block_till_done()
    assert theater_ctrl._state.scene_slug == "stairs"

    original_activate = theater_ctrl._activate_scene

    async def activate_with_stairs_motion(*args, **kwargs):
        await original_activate(*args, **kwargs)
        hass.states.async_set("binary_sensor.stairs_motion", "on")
        await stairs_ctrl.handle_motion_on()

    theater_ctrl._activate_scene = activate_with_stairs_motion

    await stairs_ctrl._on_motion_timer()
    await hass.async_block_till_done()

    assert stairs_ctrl._state.is_circadian
    assert stairs_ctrl._state.source == ActivationSource.MOTION
    # The new motion found the theater off and linked it again.
    assert theater_ctrl._state.scene_slug == "stairs"
    assert "theater" in stairs_ctrl._linked_activated_scenes


@pytest.mark.integration
async def test_heal_does_not_overwrite_a_newer_scene_target(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """A heal that finishes after a new scene leaves the new scene's target."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    eid = "light.network_room_overhead_1"
    old_target = {
        "state": "on",
        "brightness": 10,
        "color_temp_kelvin": 2700,
        "commanded_at": time.monotonic() - 200.0,
        "transition": 0.0,
    }
    new_target = {**old_target, "brightness": 200}
    ctrl._active_scene_targets = {eid: old_target}

    original_apply = ctrl._apply_light_state

    async def apply_then_switch_scene(*args, **kwargs):
        await original_apply(*args, **kwargs)
        ctrl._active_scene_targets = {eid: new_target}

    ctrl._apply_light_state = apply_then_switch_scene

    await ctrl.handle_scene_drift_reassert(eid, "glitch_window")

    assert ctrl._active_scene_targets[eid] is new_target


@pytest.mark.integration
async def test_motion_during_fade_is_not_replayed_when_motion_lighting_is_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The replay passes the same gates the motion handler applies."""
    hass.states.async_set(MOTION_SENSOR, "off")
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await _motion_lit_with_timer_due(hass, ctrl)

    original_turn_off = ctrl._turn_off_all_lights

    async def turn_off_with_motion_then_disable(*args, **kwargs):
        hass.states.async_set(MOTION_SENSOR, "on")
        await ctrl.handle_motion_on()
        ctrl.motion_light_enabled = False
        await original_turn_off(*args, **kwargs)

    ctrl._turn_off_all_lights = turn_off_with_motion_then_disable

    await ctrl._on_motion_timer()
    await hass.async_block_till_done()

    assert ctrl._state.is_off


@pytest.mark.integration
async def test_motion_during_fade_does_not_replace_a_newer_user_scene(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """A scene chosen during the fade stays; the motion replay leaves it alone."""
    hass.states.async_set(MOTION_SENSOR, "off")
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await _motion_lit_with_timer_due(hass, ctrl)

    original_turn_off = ctrl._turn_off_all_lights

    async def turn_off_with_motion_and_press(*args, **kwargs):
        hass.states.async_set(MOTION_SENSOR, "on")
        await ctrl.handle_motion_on()
        await original_turn_off(*args, **kwargs)
        await ctrl._activate_scene("evening", ActivationSource.USER)

    ctrl._turn_off_all_lights = turn_off_with_motion_and_press

    await ctrl._on_motion_timer()
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "evening"
    assert ctrl._state.source == ActivationSource.USER


@pytest.mark.integration
async def test_circadian_scene_service_clears_the_previous_scene_targets(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """scene.<area>_circadian after a visual scene leaves no stale targets.

    Stale targets let the post-settle self-check restore the old scene's
    brightness over circadian.
    """
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("evening", ActivationSource.USER)
    assert ctrl._active_scene_targets

    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.network_room_circadian"}, blocking=True
    )
    await hass.async_block_till_done()

    assert ctrl._state.is_circadian
    assert ctrl._active_scene_targets == {}


@pytest.mark.integration
async def test_motion_during_fade_is_not_replayed_over_a_scene_still_applying(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """No replay while another activation is still applying its scene."""
    hass.states.async_set(MOTION_SENSOR, "off")
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await _motion_lit_with_timer_due(hass, ctrl)

    release = asyncio.Event()
    original_apply = ctrl._apply_scene_data
    original_turn_off = ctrl._turn_off_all_lights
    press = None
    replays = 0

    async def count_replay():
        nonlocal replays
        replays += 1

    async def slow_apply(scene_slug, *args, **kwargs):
        if scene_slug == "evening":
            await release.wait()
        await original_apply(scene_slug, *args, **kwargs)

    async def turn_off_with_motion_and_press(*args, **kwargs):
        nonlocal press
        hass.states.async_set(MOTION_SENSOR, "on")
        await ctrl.handle_motion_on()
        await original_turn_off(*args, **kwargs)
        press = hass.async_create_task(ctrl._activate_scene("evening", ActivationSource.USER))
        await asyncio.sleep(0)
        # The sensor stays on from here, so any later call is the replay.
        ctrl.handle_motion_on = count_replay

    ctrl._apply_scene_data = slow_apply
    ctrl._turn_off_all_lights = turn_off_with_motion_and_press

    await ctrl._on_motion_timer()
    assert replays == 0

    release.set()
    await press
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "evening"
    assert ctrl._state.source == ActivationSource.USER
