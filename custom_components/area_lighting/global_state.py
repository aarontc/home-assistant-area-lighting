"""Global (all-area) master toggles for area_lighting.

Owns three booleans: motion-triggered lights-on, occupancy-timeout
lights-off, and demand-response shedding, each gating every area.
Persisted via the StateStorage reserved global key. Reaches controllers
lazily through hass.data so it holds no back-references.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .state_storage import StateStorage

_LOGGER = logging.getLogger(__name__)


class GlobalToggles:
    """Holds, persists, and broadcasts the three global master flags."""

    def __init__(self, hass: HomeAssistant, state_storage: StateStorage) -> None:
        self._hass = hass
        self._state_storage = state_storage
        self._motion_lights_enabled = True
        self._occupancy_timeout_enabled = True
        self._demand_response_active = False
        self._listeners: list[Callable[[], None]] = []

    @property
    def motion_lights_enabled(self) -> bool:
        return self._motion_lights_enabled

    @property
    def occupancy_timeout_enabled(self) -> bool:
        return self._occupancy_timeout_enabled

    @property
    def demand_response_active(self) -> bool:
        return self._demand_response_active

    # ── listener plumbing (mirrors controller.add_state_listener) ──
    def add_state_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def remove_state_listener(self, cb: Callable[[], None]) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def _schedule_save(self) -> None:
        self._hass.async_create_task(self._state_storage.async_save_global_state(self.state_dict()))

    # ── persistence ──
    def state_dict(self) -> dict:
        return {
            "motion_lights_enabled": self._motion_lights_enabled,
            "occupancy_timeout_enabled": self._occupancy_timeout_enabled,
            "demand_response_active": self._demand_response_active,
        }

    def load_persisted_state(self, data: dict) -> None:
        if not data:
            return
        if "motion_lights_enabled" in data:
            self._motion_lights_enabled = bool(data["motion_lights_enabled"])
        if "occupancy_timeout_enabled" in data:
            self._occupancy_timeout_enabled = bool(data["occupancy_timeout_enabled"])
        if "demand_response_active" in data:
            self._demand_response_active = bool(data["demand_response_active"])

    # ── setters (called by the switch entities) ──
    async def async_set_motion_lights_enabled(self, enabled: bool) -> None:
        if self._motion_lights_enabled == enabled:
            return
        self._motion_lights_enabled = enabled
        # No cross-controller side effect: _check_conditions reads the flag
        # live on the next motion edge.
        self._notify()
        self._schedule_save()

    async def async_set_occupancy_timeout_enabled(self, enabled: bool) -> None:
        if self._occupancy_timeout_enabled == enabled:
            return
        self._occupancy_timeout_enabled = enabled
        controllers = self._hass.data.get(DOMAIN, {}).get("controllers", {})
        for ctrl in controllers.values():
            if enabled:
                ctrl.enforce_occupancy_timer()
            else:
                ctrl.cancel_occupancy_timer()
        self._notify()
        self._schedule_save()

    async def async_set_demand_response_active(self, enabled: bool) -> None:
        if self._demand_response_active == enabled:
            return
        self._demand_response_active = enabled
        controllers = self._hass.data.get(DOMAIN, {}).get("controllers", {})
        for ctrl in controllers.values():
            self._hass.async_create_task(ctrl.async_reconcile_demand_response())
        self._notify()
        self._schedule_save()
