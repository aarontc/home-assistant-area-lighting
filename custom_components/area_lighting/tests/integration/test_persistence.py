"""Timer deadline persistence + restart recovery tests (D4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


async def _setup_with_config(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_motion_timer_deadline_persists_in_state_dict(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """state_dict() contains a timer_deadlines key with the motion timer's deadline."""
    await _setup_with_config(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)
    ctrl._motion_timer.start()
    data = ctrl.state_dict()
    assert "timer_deadlines" in data
    assert data["timer_deadlines"]["motion_off"] is not None
    deadline = datetime.fromisoformat(data["timer_deadlines"]["motion_off"])
    assert deadline.tzinfo is not None


@pytest.mark.integration
async def test_past_due_motion_timer_fires_on_restore(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """A restored past-due motion timer fires and transitions the area to off."""
    await _setup_with_config(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)

    past = datetime.now(UTC) - timedelta(seconds=30)
    saved = ctrl.state_dict()
    saved["timer_deadlines"] = {
        "motion_off": past.isoformat(),
        "motion_night_off": None,
        "occupancy_off": None,
    }
    from custom_components.area_lighting.controller import AreaLightingController

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    fresh.load_persisted_state(saved)
    await fresh.restore_timers()
    await hass.async_block_till_done()
    assert fresh._state.is_off


@pytest.mark.integration
async def test_future_motion_timer_deadline_rearms_on_restore(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """A future deadline results in an active, re-armed timer."""
    await _setup_with_config(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)

    future = datetime.now(UTC) + timedelta(seconds=300)
    saved = ctrl.state_dict()
    saved["timer_deadlines"] = {
        "motion_off": future.isoformat(),
        "motion_night_off": None,
        "occupancy_off": None,
    }
    from custom_components.area_lighting.controller import AreaLightingController

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    fresh.load_persisted_state(saved)
    await fresh.restore_timers()
    assert fresh._motion_timer.is_active
    fresh.shutdown()


@pytest.mark.integration
async def test_restore_in_manual_with_past_due_occupancy_turns_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Occupancy timeout still fires from manual state across restart."""
    await _setup_with_config(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_manual()

    past = datetime.now(UTC) - timedelta(seconds=60)
    saved = ctrl.state_dict()
    saved["timer_deadlines"] = {
        "motion_off": None,
        "motion_night_off": None,
        "occupancy_off": past.isoformat(),
    }
    from custom_components.area_lighting.controller import AreaLightingController

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    fresh.load_persisted_state(saved)
    await fresh.restore_timers()
    await hass.async_block_till_done()
    assert fresh._state.is_off


@pytest.mark.integration
async def test_restore_no_deadlines_is_noop(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """state with no timer_deadlines key restores cleanly with no timers active."""
    await _setup_with_config(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    saved = ctrl.state_dict()
    saved.pop("timer_deadlines", None)

    from custom_components.area_lighting.controller import AreaLightingController

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    fresh.load_persisted_state(saved)
    await fresh.restore_timers()
    assert not fresh._motion_timer.is_active
    assert not fresh._occupancy_timer.is_active


@pytest.mark.integration
async def test_occupancy_timeout_enabled_round_trips(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """state_dict persists occupancy_timeout_enabled; load_persisted_state restores it."""
    await _setup_with_config(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    # Flip the flag off and serialize
    await ctrl.async_set_occupancy_timeout_enabled(False)
    saved = ctrl.state_dict()
    assert saved["occupancy_timeout_enabled"] is False

    # Rehydrate into a fresh controller
    from custom_components.area_lighting.controller import AreaLightingController

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    assert fresh.occupancy_timeout_enabled is True  # default before load
    fresh.load_persisted_state(saved)
    assert fresh.occupancy_timeout_enabled is False


@pytest.mark.integration
async def test_occupancy_timeout_enabled_defaults_true_when_missing(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """load_persisted_state treats missing key as True (upgrade path)."""
    await _setup_with_config(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    from custom_components.area_lighting.controller import AreaLightingController

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    # Simulate a persistence dict saved before this feature existed
    legacy = {"motion_light_enabled": False, "ambience_enabled": True}
    fresh.load_persisted_state(legacy)
    assert fresh.occupancy_timeout_enabled is True


@pytest.mark.integration
async def test_restart_into_scene_matching_report_does_not_latch_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Restoring into a scene rebuilds _active_scene_targets.

    Persistence stores only the scene slug, never the target tracking.
    Without a rebuild at startup, the first post-grace light state
    report has no target to match, so a report fully CONSISTENT with
    the restored scene falsely latched the area to manual.
    """
    import time

    await _setup_with_config(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    saved = ctrl.state_dict()

    # Simulate the restart on the still-wired controller: in-memory scene
    # tracking is gone; persisted state is loaded and reconciled.
    ctrl._active_scene_targets = {}
    ctrl.load_persisted_state(saved)
    ctrl.reconcile_startup_state()

    # The rebuild stamps command metadata like a normal activation.
    assert ctrl._active_scene_targets
    for target in ctrl._active_scene_targets.values():
        assert "commanded_at" in target

    # Grace and per-entity settle windows are long past.
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 200.0
    for target in ctrl._active_scene_targets.values():
        target["commanded_at"] = time.monotonic() - 200.0

    # A bulb reports on: consistent with the restored skeleton scene.
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 180})
    await hass.async_block_till_done()

    assert not ctrl._state.is_manual
    assert ctrl.current_scene == "daylight"
