"""Service handlers for the Area Lighting integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .controller import AreaLightingController
from .scene_storage import SceneStorage

_LOGGER = logging.getLogger(__name__)

SERVICE_SCHEMA = vol.Schema({
    vol.Required("area_id"): cv.string,
})

SNAPSHOT_SCHEMA = vol.Schema({
    vol.Required("area_id"): cv.string,
    vol.Required("scene"): cv.string,
})

SERVICE_MAP = {
    "lighting_on": "lighting_on",
    "lighting_off": "lighting_off",
    "lighting_off_fade": "lighting_off_fade",
    "lighting_favorite": "lighting_favorite",
    "lighting_raise": "lighting_raise",
    "lighting_lower": "lighting_lower",
    "lighting_circadian": "lighting_circadian",
}


def _get_controller(hass: HomeAssistant, area_id: str) -> AreaLightingController | None:
    controllers: dict[str, AreaLightingController] = hass.data[DOMAIN]["controllers"]
    controller = controllers.get(area_id)
    if not controller:
        _LOGGER.warning("No controller for area_id: %s", area_id)
    return controller


async def async_register_services(hass: HomeAssistant) -> None:
    """Register all area_lighting services."""

    # Lighting action services
    for service_name, method_name in SERVICE_MAP.items():
        async def _handler(call: ServiceCall, _method=method_name) -> None:
            area_id = call.data["area_id"]
            controller = _get_controller(hass, area_id)
            if controller:
                method = getattr(controller, _method)
                await method()

        hass.services.async_register(
            DOMAIN, service_name, _handler, schema=SERVICE_SCHEMA,
        )

    # Snapshot scene service
    async def _handle_snapshot(call: ServiceCall) -> None:
        area_id = call.data["area_id"]
        scene_slug = call.data["scene"]
        storage: SceneStorage = hass.data[DOMAIN]["scene_storage"]
        config = hass.data[DOMAIN]["config"]
        area = config.area_by_id(area_id)
        if not area:
            _LOGGER.warning("snapshot_scene: unknown area_id %s", area_id)
            return

        # Validate scene slug exists for this area
        if scene_slug not in area.scene_slugs:
            _LOGGER.warning(
                "snapshot_scene: scene '%s' not configured for area '%s'",
                scene_slug, area_id,
            )
            return

        # Get all light entity IDs for this area
        entity_ids = [l.id for l in area.all_lights]
        snapshot = await storage.async_snapshot_scene(area_id, scene_slug, entity_ids)
        _LOGGER.info(
            "Snapshot captured for %s/%s: %d lights",
            area_id, scene_slug, len(snapshot),
        )

    hass.services.async_register(
        DOMAIN, "snapshot_scene", _handle_snapshot, schema=SNAPSHOT_SCHEMA,
    )

