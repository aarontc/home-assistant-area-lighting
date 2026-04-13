"""Smoke test — proves the integration can load under pytest-homeassistant-custom-component."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.mark.integration
async def test_integration_loads(hass: HomeAssistant, helper_entities, network_room_config) -> None:
    """area_lighting sets up cleanly with a minimal config."""
    assert await async_setup_component(hass, "area_lighting", network_room_config)
    await hass.async_block_till_done()
    # Wait for EVENT_HOMEASSISTANT_STARTED callbacks (scene entities, etc.)
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    # The controller for network_room should exist
    assert "area_lighting" in hass.data
    assert "network_room" in hass.data["area_lighting"]["controllers"]
