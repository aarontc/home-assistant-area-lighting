"""Glitch-aware scene self-healing.

Out-of-band Hue changes (power-on default, RF dropout, unavailable→on
recovery) should be auto-healed back to the active scene rather than
latching the area to `manual`. Genuine manual changes (long after our
last command, bulb reachable throughout) still latch `manual`.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


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
