"""Persistent storage for controller runtime state.

Stores per-area state (last_scene, dimmed flag, user toggles, timer
deadlines) so that controller state survives Home Assistant restarts.

Timer deadlines are stored as timezone-aware UTC datetimes serialized via
``datetime.isoformat()`` — this output is RFC 3339 / ISO 8601 compatible
and round-trips through ``datetime.fromisoformat()``.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "area_lighting.state"
STORAGE_VERSION = 1


class StateStorage:
    """Manages persistent area controller state."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, dict[str, Any]] = {}
        # Structure: { "area_id": { "current_scene": "...", "dimmed": bool, ... } }

    async def async_load(self) -> None:
        """Load stored state."""
        data = await self._store.async_load()
        if data:
            self._data = data
        _LOGGER.debug("Loaded state for %d areas", len(self._data))

    async def async_save(self) -> None:
        """Save state to storage."""
        await self._store.async_save(self._data)

    def get_area_state(self, area_id: str) -> dict[str, Any]:
        """Get stored state for an area (returns empty dict if none)."""
        return self._data.get(area_id, {})

    async def async_save_area_state(
        self,
        area_id: str,
        state: dict[str, Any],
    ) -> None:
        """Save state for an area."""
        _LOGGER.debug(
            "Persisted state for area %s (%d keys)",
            area_id,
            len(state),
        )
        self._data[area_id] = state
        await self.async_save()
