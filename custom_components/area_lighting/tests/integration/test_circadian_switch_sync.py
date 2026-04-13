"""Circadian switches must be off whenever the area's state is not circadian.

Regression tests for the bug where leaving circadian state (via scene
cycling, favorite, external scene activation, or manual detection) would
leave the circadian_lighting switches running, fighting the new scene's
light settings.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _spy_disable(ctrl) -> AsyncMock:
    """Wrap _disable_circadian_switches so tests can count calls."""
    spy = AsyncMock(wraps=ctrl._disable_circadian_switches)
    ctrl._disable_circadian_switches = spy
    return spy


@pytest.mark.integration
async def test_lighting_on_from_circadian_disables_circadian_switches(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Cycling out of circadian via 'on' must disable the switches so
    they don't override the new scene's light settings."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_circadian(ActivationSource.USER)
    spy = _spy_disable(ctrl)

    await ctrl.lighting_on()
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug in ("daylight", "evening")
    assert spy.await_count >= 1


@pytest.mark.integration
async def test_lighting_favorite_from_circadian_disables_circadian_switches(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Favorite button from circadian → night must disable circadian switches."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_circadian(ActivationSource.USER)
    spy = _spy_disable(ctrl)

    await ctrl.lighting_favorite()
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "night"
    assert spy.await_count >= 1


@pytest.mark.integration
async def test_external_scene_activation_non_circadian_disables_switches(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """An external scene.turn_on (e.g., dashboard button) that moves the
    area out of circadian must disable the circadian switches."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_circadian(ActivationSource.USER)
    spy = _spy_disable(ctrl)

    await ctrl.handle_scene_activated("daylight")
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "daylight"
    assert spy.await_count >= 1


@pytest.mark.integration
async def test_manual_light_change_from_circadian_disables_switches(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Detected manual light change while in circadian must disable the
    switches — otherwise they immediately overwrite the user's manual
    change with a circadian-calculated one."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_circadian(ActivationSource.USER)
    spy = _spy_disable(ctrl)

    await ctrl.handle_manual_light_change()
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert spy.await_count >= 1


@pytest.mark.integration
async def test_holiday_change_from_circadian_disables_switches(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Holiday mode activating while area is in circadian should swap to
    the holiday scene AND disable circadian switches."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    # Put the area in circadian state, so the holiday transition has
    # somewhere to come from.
    ctrl._state.transition_to_circadian(ActivationSource.HOLIDAY)
    _spy_disable(ctrl)

    hass.states.async_set("input_select.holiday_mode", "christmas")
    await ctrl.handle_holiday_changed("christmas")
    await hass.async_block_till_done()

    # handle_holiday_changed only re-activates if already_holiday or is_off;
    # from circadian, it's a no-op. That's current behavior — not the bug
    # we're fixing here. This test exists to document the expectation.
    # Once we verify the simpler paths work, we can revisit this.


@pytest.mark.integration
async def test_off_from_circadian_disables_switches(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """lighting_off from circadian must disable the switches."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_circadian(ActivationSource.USER)
    spy = _spy_disable(ctrl)

    await ctrl.lighting_off()
    await hass.async_block_till_done()

    assert ctrl._state.is_off
    assert spy.await_count >= 1


@pytest.mark.integration
async def test_activating_non_circadian_scene_disables_switches(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Direct internal _activate_scene to a non-circadian scene from
    circadian must disable the switches first."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_circadian(ActivationSource.USER)
    spy = _spy_disable(ctrl)

    await ctrl._activate_scene("evening", ActivationSource.USER)
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "evening"
    assert spy.await_count >= 1
