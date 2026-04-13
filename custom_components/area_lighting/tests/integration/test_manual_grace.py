"""Manual detection grace period tests (D5)."""

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


async def _put_light_on(hass: HomeAssistant, entity_id: str, brightness: int) -> None:
    """Set a light to the 'on' state at the given brightness. Fires
    EVENT_STATE_CHANGED because the starting state is 'off' in the fixture.
    """
    hass.states.async_set(entity_id, "on", {"brightness": brightness})
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_manual_change_within_grace_does_not_mark_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 200}
    )
    hass.states.async_set(
        "light.network_room_overhead_2", "on", {"brightness": 200}
    )
    # Fire a manual-change event inside the grace window
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 100}
    )
    await hass.async_block_till_done()
    assert not ctrl._state.is_manual


@pytest.mark.integration
async def test_manual_change_after_grace_marks_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    # Simulate grace period expiry by backdating the monotonic timestamp
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 200}
    )
    hass.states.async_set(
        "light.network_room_overhead_2", "on", {"brightness": 200}
    )
    await hass.async_block_till_done()
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 100}
    )
    await hass.async_block_till_done()
    assert ctrl._state.is_manual


@pytest.mark.integration
async def test_fresh_controller_has_monotonic_seed(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """A freshly constructed controller has a seeded monotonic timestamp."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl._state.last_scene_change_monotonic is not None


# ── Reproduces the production bug: attribute-only changes (brightness/color) ──
# on an already-on light fire EVENT_STATE_REPORTED instead of EVENT_STATE_CHANGED.
# The handler was only listening on state_changed, so these events were silently
# dropped — which is exactly what the user observed in production.


@pytest.mark.integration
async def test_brightness_attribute_only_change_marks_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Light is already on → brightness attribute drops → must mark manual.

    Exercises EVENT_STATE_REPORTED (not EVENT_STATE_CHANGED): the state
    stays 'on', only the brightness attribute changes. This is the
    production scenario.
    """
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    # Put light already on + light group on (simulates real in-scene state)
    await _put_light_on(hass, "light.network_room_overhead_1", 200)
    await _put_light_on(hass, "light.network_room_overhead_2", 200)
    hass.states.async_set("light.network_room_lights", "on", {})
    await hass.async_block_till_done()
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    # Expire the grace period
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0

    # User drags the slider — state stays 'on', attribute changes.
    # HA fires EVENT_STATE_REPORTED, not EVENT_STATE_CHANGED.
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 80}
    )
    await hass.async_block_till_done()
    assert ctrl._state.is_manual


@pytest.mark.integration
async def test_color_temp_attribute_only_change_marks_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Light is already on → color_temp_kelvin changes → must mark manual."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set(
        "light.network_room_overhead_1",
        "on",
        {"brightness": 200, "color_temp_kelvin": 2700},
    )
    hass.states.async_set(
        "light.network_room_overhead_2",
        "on",
        {"brightness": 200, "color_temp_kelvin": 2700},
    )
    hass.states.async_set("light.network_room_lights", "on", {})
    await hass.async_block_till_done()
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0

    # Attribute-only change: color temp goes to daylight
    hass.states.async_set(
        "light.network_room_overhead_1",
        "on",
        {"brightness": 200, "color_temp_kelvin": 5000},
    )
    await hass.async_block_till_done()
    assert ctrl._state.is_manual


@pytest.mark.integration
async def test_grace_period_is_four_seconds(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Grace period is 4 seconds per user request."""
    from custom_components.area_lighting.const import MANUAL_DETECTION_GRACE_SECONDS

    assert MANUAL_DETECTION_GRACE_SECONDS == 4


@pytest.mark.integration
async def test_manual_detection_ignores_nonexistent_light_group(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The handler must NOT consult any light.<area>_lights group entity.

    The component replaces the legacy templater — it must work on
    installs that never generated a group entity. Setting the fake
    group to 'unknown' (or not setting it at all) must not affect
    whether manual detection fires.
    """
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await _put_light_on(hass, "light.network_room_overhead_1", 200)
    # Note: NO light.network_room_lights entity is set here.
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0

    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 50}
    )
    await hass.async_block_till_done()
    assert ctrl._state.is_manual


@pytest.mark.integration
async def test_manual_change_while_area_is_off_does_not_mark_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """If the controller state is off, a light turning on externally is
    a scene activation, not a manual change — don't force manual."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl._state.is_off
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0

    # Light turns on while we think the area is off
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 100}
    )
    await hass.async_block_till_done()
    # Should NOT be manual — area is off, this is external activation
    assert not ctrl._state.is_manual
