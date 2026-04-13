"""Motion retrigger: new motion during timer countdown must reset the timer.

Reported bug: while the motion-off timer is counting down, a new motion
event on the sensor is being ignored. The user expects the timer to
reset (so the lights stay on through continuous activity).
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


SENSOR = "binary_sensor.network_room_motion_sensor_motion"


@pytest.mark.integration
async def test_motion_on_during_timer_cancels_timer(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Motion triggers scene, motion ends (timer starts), motion fires
    again — the timer must be cancelled so the countdown resets on
    the next motion-end."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    # Drive the controller directly to the 'motion activated a scene,
    # then motion ended, timer counting down' state.
    await ctrl.handle_motion_on()
    assert ctrl._state.source == ActivationSource.MOTION
    await ctrl.handle_motion_off()
    assert ctrl._motion_timer.is_active

    # New motion arrives while the timer is still counting down.
    await ctrl.handle_motion_on()

    # Timer should now be cancelled — we're in the "motion again,
    # lights stay on" phase. The next handle_motion_off will restart
    # the full countdown.
    assert not ctrl._motion_timer.is_active
    assert not ctrl._motion_night_timer.is_active
    # Source is still MOTION, scene is still the motion-activated one
    assert ctrl._state.source == ActivationSource.MOTION
    assert not ctrl._state.is_off


@pytest.mark.integration
async def test_motion_on_event_bypasses_condition_check_when_source_is_motion(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Going through the real sensor state-change path: trigger
    motion → timer counts down → new sensor-on must still reach
    handle_motion_on and cancel the timer.

    (Before the fix, _check_conditions rejected motion-on events when
    current_scene != 'off', so the retrigger path was dead.)
    """
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.motion_light_enabled = True

    # Initial motion via the real event path
    hass.states.async_set(SENSOR, "off")
    await hass.async_block_till_done()
    hass.states.async_set(SENSOR, "on")
    await hass.async_block_till_done()
    assert ctrl._state.source == ActivationSource.MOTION
    assert not ctrl._state.is_off

    # Motion ends → timer starts
    hass.states.async_set(SENSOR, "off")
    await hass.async_block_till_done()
    assert ctrl._motion_timer.is_active

    # Motion re-fires → must cancel the timer
    hass.states.async_set(SENSOR, "on")
    await hass.async_block_till_done()
    assert not ctrl._motion_timer.is_active


@pytest.mark.integration
async def test_motion_retrigger_restarts_full_duration_on_next_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """After retrigger (on → off → on), the next off should restart the
    timer with the full duration, not whatever was left."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    await ctrl.handle_motion_on()
    await ctrl.handle_motion_off()
    assert ctrl._motion_timer.is_active
    deadline1 = ctrl._motion_timer.deadline_utc

    # Retrigger
    await ctrl.handle_motion_on()
    assert not ctrl._motion_timer.is_active

    # Motion ends again — timer restarts with FULL duration
    await ctrl.handle_motion_off()
    assert ctrl._motion_timer.is_active
    deadline2 = ctrl._motion_timer.deadline_utc
    assert deadline2 is not None
    assert deadline1 is not None
    # The new deadline should be at/after the old one (full fresh duration)
    assert deadline2 >= deadline1


@pytest.mark.integration
async def test_motion_on_in_user_scene_does_not_hijack_scene(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """When the current scene was activated by USER (not motion),
    a motion-on event should NOT change the scene or start a timer.
    This guards against motion hijacking a user-owned scene."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    # User activates evening via the remote-like path
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    assert ctrl._state.scene_slug == "evening"
    assert ctrl._state.source == ActivationSource.USER

    # Motion fires
    hass.states.async_set(SENSOR, "off")
    await hass.async_block_till_done()
    hass.states.async_set(SENSOR, "on")
    await hass.async_block_till_done()

    # Scene should be unchanged, no timer running
    assert ctrl._state.scene_slug == "evening"
    assert ctrl._state.source == ActivationSource.USER
    assert not ctrl._motion_timer.is_active
    assert not ctrl._motion_night_timer.is_active
