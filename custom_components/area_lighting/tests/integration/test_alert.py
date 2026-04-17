"""Integration tests for alert mode."""

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


@pytest.mark.integration
async def test_alert_active_defaults_false(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Controller starts with _alert_active == False."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl._alert_active is False


@pytest.mark.integration
async def test_alert_active_in_diagnostic_snapshot(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """diagnostic_snapshot includes alert_active."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    snap = ctrl.diagnostic_snapshot()
    assert "alert_active" in snap
    assert snap["alert_active"] is False


@pytest.mark.integration
async def test_manual_detection_suppressed_when_alert_active(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Light state changes during an alert do not trigger manual detection."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("daylight", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl.current_scene == "daylight"

    ctrl._alert_active = True

    hass.states.async_set(
        "light.network_room_overhead_1",
        "on",
        {"brightness": 50, "color_mode": "color_temp", "color_temp_kelvin": 4000},
    )
    await hass.async_block_till_done()

    assert ctrl.current_scene == "daylight"
    ctrl._alert_active = False
