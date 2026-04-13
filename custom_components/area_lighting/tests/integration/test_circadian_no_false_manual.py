"""Circadian lighting updates must NOT transition the area to manual.

While the area is in circadian state, the circadian_lighting HACS
integration fires periodic light.turn_on calls to adjust brightness
and color temperature. Each call produces a state_changed event.
The manual detection handler was wrongly interpreting those as
manual overrides and transitioning the area to manual — which then
disables the circadian switches and breaks the whole circadian cycle.

Fix: skip manual detection entirely while ctrl._state.is_circadian.
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
async def test_brightness_change_in_circadian_does_not_mark_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Simulates a circadian_lighting brightness tick while in circadian."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_circadian(ActivationSource.USER)
    # Expire grace period
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0

    hass.states.async_set(
        "light.network_room_overhead_1",
        "on",
        {"brightness": 200, "color_temp_kelvin": 3000},
    )
    await hass.async_block_till_done()

    # Now simulate a circadian tick: brightness drops
    hass.states.async_set(
        "light.network_room_overhead_1",
        "on",
        {"brightness": 80, "color_temp_kelvin": 2700},
    )
    await hass.async_block_till_done()

    assert ctrl._state.is_circadian, (
        f"expected still circadian, got {ctrl._state.state}"
    )
    assert not ctrl._state.is_manual


@pytest.mark.integration
async def test_color_change_in_circadian_does_not_mark_manual(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_circadian(ActivationSource.USER)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0

    hass.states.async_set(
        "light.network_room_overhead_1",
        "on",
        {"brightness": 150, "color_temp_kelvin": 4000},
    )
    await hass.async_block_till_done()

    hass.states.async_set(
        "light.network_room_overhead_1",
        "on",
        {"brightness": 150, "color_temp_kelvin": 2900},
    )
    await hass.async_block_till_done()

    assert ctrl._state.is_circadian


@pytest.mark.integration
async def test_manual_detection_still_works_in_scene_state(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Sanity check: manual detection still fires when NOT in circadian."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0

    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 200}
    )
    await hass.async_block_till_done()
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 40}
    )
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
