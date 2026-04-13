"""Persistent storage for scene light state snapshots.

Stores per-area, per-scene light states (brightness, color, etc.)
in .storage/area_lighting.scenes. This allows users to:
1. Let the component generate skeleton scenes (lights on/off by role)
2. Adjust lights to desired levels
3. Call area_lighting.snapshot_scene to persist the current state
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "area_lighting.scenes"
STORAGE_VERSION = 1


class SceneStorage:
    """Manages persistent scene light state data."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[dict[str, dict[str, dict[str, Any]]]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        # Structure: { "area_id": { "scene_slug": { "entity_id": { state_dict } } } }

    async def async_load(self) -> None:
        """Load stored scene data."""
        data = await self._store.async_load()
        if data:
            self._data = data
        _LOGGER.debug("Loaded scene data for %d areas", len(self._data))

    async def async_save(self) -> None:
        """Save scene data to storage."""
        await self._store.async_save(self._data)

    def get_scene_data(self, area_id: str, scene_slug: str) -> dict[str, Any] | None:
        """Get stored light states for a scene.

        Returns dict of { entity_id: { state attributes } } or None.
        """
        return self._data.get(area_id, {}).get(scene_slug)

    async def async_snapshot_scene(
        self,
        area_id: str,
        scene_slug: str,
        entity_ids: list[str],
    ) -> dict[str, Any]:
        """Capture current light states and persist them.

        Returns the captured state dict.
        """
        snapshot: dict[str, Any] = {}
        for entity_id in entity_ids:
            state = self._hass.states.get(entity_id)
            if not state:
                continue

            entry: dict[str, Any] = {"state": state.state}
            attrs = state.attributes

            # Capture relevant light attributes
            if state.state == "on":
                for attr in (
                    "brightness",
                    "color_temp_kelvin",
                    "color_temp",
                    "hs_color",
                    "rgb_color",
                    "xy_color",
                    "color_mode",
                    "effect",
                ):
                    if attr in attrs and attrs[attr] is not None:
                        val = attrs[attr]
                        # Convert tuples to lists for JSON serialization
                        if isinstance(val, tuple):
                            val = list(val)
                        entry[attr] = val

            snapshot[entity_id] = entry

        # Store it
        if area_id not in self._data:
            self._data[area_id] = {}
        self._data[area_id][scene_slug] = snapshot
        await self.async_save()

        _LOGGER.info(
            "Snapshot saved for %s/%s: %d entities",
            area_id,
            scene_slug,
            len(snapshot),
        )
        return snapshot

    async def async_delete_scene(self, area_id: str, scene_slug: str) -> None:
        """Delete stored scene data."""
        if area_id in self._data and scene_slug in self._data[area_id]:
            del self._data[area_id][scene_slug]
            await self.async_save()

    async def async_import_from_yaml(
        self,
        area_id: str,
        scene_slug: str,
        entities: dict[str, Any],
    ) -> None:
        """Import scene data from existing YAML scene files.

        Used for one-time migration from templater-generated scene files.
        """
        if area_id not in self._data:
            self._data[area_id] = {}
        # Filter to only light entities (skip input_booleans, etc.)
        light_data = {eid: attrs for eid, attrs in entities.items() if eid.startswith("light.")}
        if light_data:
            self._data[area_id][scene_slug] = light_data
            await self.async_save()
