"""Motion-ended handling + fadeout split (bug 8/9).

Two closely-related bugs:

1. When a motion sensor goes to 'off', the timer never starts if any
   other sensor in the area is 'unavailable' or 'unknown'. The all_off
   check was too strict (required explicit STATE_OFF on every sensor).

2. fadeout_seconds is overloaded — it's used for motion/occupancy timer
   expiry today, but users want a separate 'manual' fadeout for
   remote-off and off-scene activation. Refactored into:
    - manual_fadeout_seconds (remote off / off scene)
    - motion_fadeout_seconds (motion + occupancy timer expiry)
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


# ── Motion ended: unavailable sensors must not block timer start ────────


@pytest.mark.integration
async def test_motion_off_starts_timer_when_all_sensors_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Baseline: single sensor transitions on → off, timer starts."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    sensor = "binary_sensor.network_room_motion_sensor_motion"

    # Prime the scene so motion_off has something to time out
    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)
    hass.states.async_set(sensor, "on")
    await hass.async_block_till_done()
    hass.states.async_set(sensor, "off")
    await hass.async_block_till_done()

    assert ctrl._motion_timer.is_active


@pytest.mark.integration
async def test_motion_off_starts_timer_when_single_sensor_unavailable(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Only sensor goes on → unavailable → off. Timer should start.

    (Old bug: 'unavailable' → False in the all_off walrus, so handler
    never ran.)
    """
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    sensor = "binary_sensor.network_room_motion_sensor_motion"

    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)
    hass.states.async_set(sensor, "on")
    await hass.async_block_till_done()
    hass.states.async_set(sensor, "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set(sensor, "off")
    await hass.async_block_till_done()

    assert ctrl._motion_timer.is_active


@pytest.mark.integration
async def test_motion_off_starts_timer_with_mixed_state_sensors(
    hass: HomeAssistant, helper_entities
) -> None:
    """Two sensors: sensor A goes on → off, sensor B is 'unknown'.
    Timer should still start because no sensor is actively reporting motion."""
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "test_area",
                    "name": "Test Area",
                    "event_handlers": True,
                    "circadian_switches": [
                        {"name": "Main", "max_brightness": 100, "min_brightness": 50}
                    ],
                    "lights": [
                        {
                            "id": "light.test_area_a",
                            "circadian_switch": "Main",
                            "circadian_type": "ct",
                            "roles": ["color", "dimming"],
                        }
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                    ],
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.test_area_motion_a",
                        "binary_sensor.test_area_motion_b",
                    ],
                }
            ]
        }
    }
    hass.states.async_set(
        "switch.circadian_lighting_test_area_main_circadian",
        "off",
        {"brightness": 75.0, "colortemp": 3500},
    )
    hass.states.async_set("light.test_area_a", "off", {})
    await _setup(hass, cfg)
    ctrl = hass.data["area_lighting"]["controllers"]["test_area"]

    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)
    hass.states.async_set("binary_sensor.test_area_motion_a", "on")
    hass.states.async_set("binary_sensor.test_area_motion_b", "unknown")
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.test_area_motion_a", "off")
    await hass.async_block_till_done()

    assert ctrl._motion_timer.is_active, (
        "timer should have started — sensor A is off, sensor B is unknown, "
        "no sensor is actively reporting motion"
    )


# ── Occupancy ended: same fix applies ───────────────────────────────────


@pytest.mark.integration
async def test_occupancy_off_starts_timer_with_unknown_sensor(
    hass: HomeAssistant, helper_entities
) -> None:
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "test_area",
                    "name": "Test Area",
                    "event_handlers": True,
                    "lights": [{"id": "light.test_area_a", "roles": ["color"]}],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                    ],
                    "occupancy_light_sensor_ids": [
                        "binary_sensor.test_area_occupancy_a",
                        "binary_sensor.test_area_occupancy_b",
                    ],
                }
            ]
        }
    }
    hass.states.async_set("light.test_area_a", "on", {})
    await _setup(hass, cfg)
    ctrl = hass.data["area_lighting"]["controllers"]["test_area"]

    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    hass.states.async_set("binary_sensor.test_area_occupancy_a", "on")
    hass.states.async_set("binary_sensor.test_area_occupancy_b", "unknown")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.test_area_occupancy_a", "off")
    await hass.async_block_till_done()

    assert ctrl._occupancy_timer.is_active


# ── Diagnostic snapshot exposes the motion event state ──────────────────


@pytest.mark.integration
async def test_diagnostic_exposes_last_motion_event(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """After a motion on/off cycle, the diagnostic snapshot must show
    what happened so users can see it in the diagnostics sensor."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    await ctrl.handle_motion_on()
    await hass.async_block_till_done()
    snap = ctrl.diagnostic_snapshot()
    assert snap.get("last_motion_event") == "motion_on"

    await ctrl.handle_motion_off()
    await hass.async_block_till_done()
    snap = ctrl.diagnostic_snapshot()
    assert snap.get("last_motion_event") == "motion_off"


# ── fadeout_seconds → manual_fadeout_seconds + motion_fadeout_seconds ───


@pytest.mark.integration
async def test_controller_exposes_both_fadeout_properties(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert hasattr(ctrl, "manual_fadeout_seconds")
    assert hasattr(ctrl, "motion_fadeout_seconds")
    # Old property should be gone
    assert not hasattr(ctrl, "fadeout_seconds"), (
        "ctrl.fadeout_seconds should have been renamed — "
        "use manual_fadeout_seconds or motion_fadeout_seconds"
    )


@pytest.mark.integration
async def test_two_fadeout_number_entities_registered(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    assert hass.states.get(
        "number.network_room_manual_fadeout_seconds"
    ) is not None
    assert hass.states.get(
        "number.network_room_motion_fadeout_seconds"
    ) is not None
    # Old entity id should be gone
    assert hass.states.get(
        "number.network_room_motion_light_fadeout_seconds"
    ) is None


@pytest.mark.integration
async def test_lighting_off_uses_manual_fadeout(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """lighting_off (remote-off / scene-off) applies the manual fadeout
    as the transition value on light service calls."""
    from unittest.mock import AsyncMock

    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.manual_fadeout_seconds = 7.5
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)

    spy = AsyncMock(wraps=ctrl._turn_off_all_lights)
    ctrl._turn_off_all_lights = spy
    await ctrl.lighting_off()

    # Called with transition=7.5
    assert spy.await_count == 1
    call = spy.call_args
    # transition is a positional or kwarg
    if call.args:
        assert call.args[0] == 7.5
    else:
        assert call.kwargs.get("transition") == 7.5


@pytest.mark.integration
async def test_motion_timer_expiry_uses_motion_fadeout(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Motion timer expiry uses motion_fadeout_seconds, not manual."""
    from unittest.mock import AsyncMock

    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.motion_fadeout_seconds = 3.0
    ctrl.manual_fadeout_seconds = 20.0
    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)

    spy = AsyncMock(wraps=ctrl._turn_off_all_lights)
    ctrl._turn_off_all_lights = spy
    await ctrl._on_motion_timer()

    assert spy.await_count == 1
    call = spy.call_args
    transition = call.args[0] if call.args else call.kwargs.get("transition")
    assert transition == 3.0, f"expected motion fadeout 3.0, got {transition}"


@pytest.mark.integration
async def test_occupancy_timer_expiry_also_uses_motion_fadeout(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Occupancy timer expiry shares the motion fadeout (per user spec)."""
    from unittest.mock import AsyncMock

    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.motion_fadeout_seconds = 2.5
    ctrl.manual_fadeout_seconds = 15.0
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)

    spy = AsyncMock(wraps=ctrl._turn_off_all_lights)
    ctrl._turn_off_all_lights = spy
    await ctrl._on_occupancy_timer()

    assert spy.await_count == 1
    call = spy.call_args
    transition = call.args[0] if call.args else call.kwargs.get("transition")
    assert transition == 2.5


@pytest.mark.integration
async def test_fadeout_legacy_key_migrates(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Persisted state with legacy 'fadeout_seconds' key migrates to
    manual_fadeout_seconds (not motion_fadeout_seconds — the old key
    was the thing that became the manual fade when the refactor split)."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    from custom_components.area_lighting.controller import AreaLightingController
    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    fresh.load_persisted_state({"fadeout_seconds": 9.0})
    # The old single fadeout was used by motion timer expiry, so it
    # should migrate to motion_fadeout_seconds
    assert fresh.motion_fadeout_seconds == 9.0
