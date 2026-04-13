"""Diagnostic sensor for Area Lighting.

Exposes a multiline string with the full internal state of all area
controllers, viewable via Developer Tools → States.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_time_interval,
)

from .const import DOMAIN
from .controller import AreaLightingController

_LOGGER = logging.getLogger(__name__)

# Periodic refresh interval. Kept at 1s while the component is under
# active development so countdown values (motion/occupancy remaining)
# visibly tick in the UI. Reduce later if this proves too noisy.
DIAGNOSTIC_REFRESH_INTERVAL = timedelta(seconds=1)


class AreaLightingDiagnosticSensor(SensorEntity):
    """A single sensor exposing all area_lighting controller state."""

    _attr_should_poll = False
    _attr_icon = "mdi:bug-outline"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._attr_name = "Area Lighting Diagnostics"
        self._attr_unique_id = "area_lighting_diagnostics"
        self.entity_id = "sensor.area_lighting_diagnostics"
        self._unsub_refresh = None

    def _build_state_text(self) -> str:
        controllers: dict[str, AreaLightingController] = self.hass.data.get(DOMAIN, {}).get(
            "controllers", {}
        )
        if not controllers:
            return "No controllers loaded"

        lines: list[str] = []
        for area_id in sorted(controllers.keys()):
            ctrl = controllers[area_id]
            snap = ctrl.diagnostic_snapshot()
            lines.append(f"=== {area_id} ({ctrl.area.name}) ===")
            lines.extend(f"  {key}: {snap[key]}" for key in sorted(snap.keys()))
            lines.append("")
        return "\n".join(lines).rstrip()

    @property
    def native_value(self) -> str:
        self._build_state_text()
        # HA limits state strings to 255 chars; expose summary as state
        # and full text as an attribute
        return f"{len(self.hass.data.get(DOMAIN, {}).get('controllers', {}))} areas"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        controllers: dict[str, AreaLightingController] = self.hass.data.get(DOMAIN, {}).get(
            "controllers", {}
        )
        per_area = {area_id: ctrl.diagnostic_snapshot() for area_id, ctrl in controllers.items()}
        return {
            "state_text": self._build_state_text(),
            "per_area": per_area,
        }

    async def async_added_to_hass(self) -> None:
        """Register listeners on every controller so the sensor refreshes."""
        controllers: dict[str, AreaLightingController] = self.hass.data.get(DOMAIN, {}).get(
            "controllers", {}
        )
        for ctrl in controllers.values():
            ctrl.add_state_listener(self._on_controller_change)

        # Periodic refresh so countdown values (motion/occupancy timer
        # remaining_seconds) visibly tick in the UI without waiting for
        # the next state_changed event.
        self._unsub_refresh = async_track_time_interval(
            self.hass,
            self._on_periodic_refresh,
            DIAGNOSTIC_REFRESH_INTERVAL,
        )

    async def async_will_remove_from_hass(self) -> None:
        controllers: dict[str, AreaLightingController] = self.hass.data.get(DOMAIN, {}).get(
            "controllers", {}
        )
        for ctrl in controllers.values():
            ctrl.remove_state_listener(self._on_controller_change)

        if self._unsub_refresh is not None:
            self._unsub_refresh()
            self._unsub_refresh = None

    @callback
    def _on_controller_change(self) -> None:
        self.async_write_ha_state()

    @callback
    def _on_periodic_refresh(self, _now) -> None:
        self.async_write_ha_state()
