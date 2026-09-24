"""area_lighting loads the components it registers entities on by itself.

The shared helper_entities fixture sets up binary_sensor, which would hide
a missing manifest dependency, so this test does not use it.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.mark.integration
async def test_occupied_sensor_registers_without_binary_sensor_preloaded(
    hass: HomeAssistant, network_room_config
) -> None:
    assert "binary_sensor" not in hass.config.components

    assert await async_setup_component(hass, "area_lighting", network_room_config)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.network_room_occupied") is not None
