"""Per-entity transition tracking for manual detection.

Background: a scene activation may carry a long fade (e.g.
`lighting_off_fade` runs with `transition = motion_fadeout_seconds`,
typically 60 s). During the fade, an xy-native Hue bulb keeps
reporting `state=on, brightness=…` with the brightness gradually
dropping toward zero. The area-wide 4 s grace expires long before
the fade does, so the comparison in `state_matches_scene_target`
(target says `state=off` for the post-fade endpoint, actual still
reports `state=on, brightness=mid-fade`) demoted the area to
`manual` — a visible misbehavior in the upstairs bathroom seen in
production at 2026-05-27 05:55 UTC.

Fix: every entry in `_active_scene_targets` records the monotonic
timestamp at which the scene was commanded and the transition
duration that went with it. Manual detection consults those
per-entity values and skips comparisons until the commanded
transition has elapsed (plus a small grace buffer).
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
async def test_active_scene_targets_record_commanded_at_and_transition(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """After _activate_scene runs, every target carries the dispatch metadata."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    before = time.monotonic()
    await ctrl._activate_scene("daylight", ActivationSource.USER, transition=60.0)
    after = time.monotonic()

    assert ctrl._active_scene_targets, (
        "expected _resolve_scene_targets to populate at least one entry for daylight"
    )
    for entity_id, target in ctrl._active_scene_targets.items():
        assert "commanded_at" in target, f"{entity_id}: missing commanded_at"
        assert "transition" in target, f"{entity_id}: missing transition"
        assert before <= target["commanded_at"] <= after
        assert target["transition"] == pytest.approx(60.0)


@pytest.mark.integration
async def test_active_scene_targets_record_zero_transition_when_unset(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """A scene activation without a transition records transition=0."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    await ctrl._activate_scene("daylight", ActivationSource.USER)

    assert ctrl._active_scene_targets
    for target in ctrl._active_scene_targets.values():
        assert target["transition"] == pytest.approx(0.0)


@pytest.mark.integration
async def test_divergence_during_long_fade_does_not_mark_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """A light still mid-transition must not trip manual detection.

    Reproduces the 05:55 UTC 2026-05-27 trace: ambient scene activated
    with a 60 s fade; 10 s later the bulb still reports `state=on`
    with a brightness that diverges from the target. With the area-wide
    grace already expired, the per-entity transition window must
    keep us in the scene state.
    """
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    # Seed an "on" starting state so the test fires EVENT_STATE_REPORTED
    # on the brightness-only change below.
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 200})
    await hass.async_block_till_done()

    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    # Area-wide grace is gone (4 s default, we backdate well past it).
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0
    # Per-entity target: commanded 10 s ago with a 60 s transition.
    ctrl._active_scene_targets = {
        "light.network_room_overhead_1": {
            "state": "on",
            "brightness": 100,
            "commanded_at": time.monotonic() - 10.0,
            "transition": 60.0,
        }
    }

    # Mid-fade report: brightness still 180, target is 100 — diff > 10.
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 180})
    await hass.async_block_till_done()

    assert not ctrl._state.is_manual, (
        "expected to stay in scene state while inside the commanded transition window"
    )


@pytest.mark.integration
async def test_divergence_after_long_fade_marks_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Once the per-entity transition window elapses, real divergence still fires."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 200})
    await hass.async_block_till_done()

    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 200.0
    # Commanded 200 s ago with a 60 s transition; the buffer is well past.
    ctrl._active_scene_targets = {
        "light.network_room_overhead_1": {
            "state": "on",
            "brightness": 100,
            "commanded_at": time.monotonic() - 200.0,
            "transition": 60.0,
        }
    }

    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 180})
    await hass.async_block_till_done()

    assert ctrl._state.is_manual


@pytest.mark.integration
async def test_off_target_still_on_mid_fade_does_not_mark_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The original production shape: target=off, actual=on while fading.

    Mirrors the ambient off-fade case where the scene says vanity_left
    should be off but the bulb keeps reporting on@decreasing-brightness
    for the duration of a 60 s transition.
    """
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 200})
    await hass.async_block_till_done()

    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0
    # ambient scene's vanity_left equivalent: target=off, still mid-fade.
    ctrl._active_scene_targets = {
        "light.network_room_overhead_1": {
            "state": "off",
            "commanded_at": time.monotonic() - 10.0,
            "transition": 60.0,
        }
    }

    # Bulb reports it's still on at half its starting brightness.
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 53})
    await hass.async_block_till_done()

    assert not ctrl._state.is_manual
