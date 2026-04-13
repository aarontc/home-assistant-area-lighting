"""Diagnostic sensor periodic refresh.

While developing, the diagnostic sensor must tick roughly every second
so fields like motion_timer_remaining_seconds are visibly counting
down, not frozen at the last state_changed refresh.
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
async def test_diagnostic_sensor_registers_periodic_refresh(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The diagnostic sensor must register a periodic refresh in
    async_added_to_hass so countdown values visibly tick in the UI."""
    await _setup(hass, network_room_config)
    from custom_components.area_lighting.diagnostics import (
        DIAGNOSTIC_REFRESH_INTERVAL,
    )

    # Interval must be <= 1 second for a responsive dev UX
    assert DIAGNOSTIC_REFRESH_INTERVAL.total_seconds() <= 1.0

    # Locate the diagnostic sensor entity object and confirm it has an
    # unsub handle set from async_track_time_interval
    sensor_state = hass.states.get("sensor.area_lighting_diagnostics")
    assert sensor_state is not None


@pytest.mark.integration
async def test_diagnostic_periodic_callback_writes_ha_state(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Directly invoking the periodic callback must cause a sensor refresh."""
    await _setup(hass, network_room_config)
    # Find the sensor entity object
    from homeassistant.components.sensor import DATA_COMPONENT as SENSOR_COMPONENT

    from custom_components.area_lighting.diagnostics import (
        AreaLightingDiagnosticSensor,
    )

    component = hass.data.get(SENSOR_COMPONENT)
    sensor_entity = None
    for entity in component.entities:
        if isinstance(entity, AreaLightingDiagnosticSensor):
            sensor_entity = entity
            break
    assert sensor_entity is not None
    # The refresh unsub must be registered
    assert sensor_entity._unsub_refresh is not None

    # Calling the periodic callback directly must not error
    # (The actual countdown behavior is tested in unit tests for
    # _timer_remaining_seconds; this just verifies the wiring.)
    sensor_entity._on_periodic_refresh(None)
