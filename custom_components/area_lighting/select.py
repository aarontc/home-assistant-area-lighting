"""Select platform for Area Lighting - last_scene tracking."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN
from .controller import AreaLightingController

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Area Lighting select entities."""
    if discovery_info is None:
        return

    controllers: dict[str, AreaLightingController] = hass.data[DOMAIN]["controllers"]
    entities = [AreaLastSceneSelect(controller) for controller in controllers.values()]
    async_add_entities(entities)


class AreaLastSceneSelect(SelectEntity):
    """Select entity tracking the last active scene for an area."""

    def __init__(self, controller: AreaLightingController) -> None:
        self._controller = controller
        area = controller.area
        self._attr_name = f"{area.name} Last Scene"
        self._attr_unique_id = f"area_lighting_{area.id}_last_scene"
        self._attr_options = area.last_scene_options
        self._attr_icon = "mdi:palette"
        self.entity_id = f"select.{area.id}_last_scene"

    @property
    def current_option(self) -> str | None:
        return self._controller.current_scene

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose dimmed state so UI users can see raise/lower had an effect.

        The select's own value stays as the scene slug (e.g., 'evening')
        because `dimmed` is an orthogonal modifier, not a different scene.
        """
        return {
            "dimmed": self._controller.dimmed,
            "previous_scene": self._controller._state.previous_scene,
            "source": self._controller._state.source.value,
        }

    async def async_select_option(self, option: str) -> None:
        """Allow manual scene selection via the select entity."""
        self._controller.current_scene = option

    async def async_added_to_hass(self) -> None:
        self._controller.add_state_listener(self._on_controller_change)

    async def async_will_remove_from_hass(self) -> None:
        self._controller.remove_state_listener(self._on_controller_change)

    @callback
    def _on_controller_change(self) -> None:
        self.async_write_ha_state()
