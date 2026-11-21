"""Area scene tracking and global animation selects."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN
from .controller import AreaLightingController
from .global_state import GlobalToggles

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
    entities: list[SelectEntity] = [
        AreaLastSceneSelect(controller) for controller in controllers.values()
    ]
    toggles = hass.data[DOMAIN]["global"]
    entities.extend(
        SceneAnimationSelect(toggles, scene_id) for scene_id in toggles.animated_scene_ids
    )
    async_add_entities(entities)


class AreaLastSceneSelect(SelectEntity):
    """Select entity tracking the last active scene for an area."""

    _attr_entity_registry_visible_default = False

    def __init__(self, controller: AreaLightingController) -> None:
        self._controller = controller
        area = controller.area
        self._attr_unique_id = f"area_lighting_{area.id}_last_scene"
        self._attr_icon = "mdi:palette"
        self.entity_id = f"select.{area.id}_last_scene"
        self.refresh_from_config()

    def refresh_from_config(self) -> None:
        area = self._controller.area
        self._attr_name = f"{area.name} Last Scene"
        self._attr_options = area.last_scene_options
        if self.platform is not None:
            self.async_write_ha_state()

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
        _LOGGER.debug(
            "Area %s: select last_scene set to %s",
            self._controller.area.id,
            option,
        )
        self._controller.current_scene = option

    async def async_added_to_hass(self) -> None:
        self._controller.add_state_listener(self._on_controller_change)

    async def async_will_remove_from_hass(self) -> None:
        self._controller.remove_state_listener(self._on_controller_change)

    @callback
    def _on_controller_change(self) -> None:
        self.async_write_ha_state()


class SceneAnimationSelect(SelectEntity):
    """Choose the animation used by every area with this scene id."""

    _attr_icon = "mdi:palette"

    def __init__(self, toggles: GlobalToggles, scene_id: str) -> None:
        self._toggles = toggles
        self.scene_id = scene_id
        self._attr_unique_id = f"area_lighting_{scene_id}_animation"
        self.entity_id = f"select.area_lighting_{scene_id}_animation"
        self.refresh_from_config()

    def refresh_from_config(self) -> None:
        self._attr_name = f"{self._toggles.scene_animation_name(self.scene_id)} Animation"
        self._attr_options = self._toggles.scene_animation_options(self.scene_id)
        if self.platform is not None:
            self.async_write_ha_state()

    @property
    def current_option(self) -> str | None:
        return self._toggles.scene_animation(self.scene_id)

    async def async_select_option(self, option: str) -> None:
        await self._toggles.async_set_scene_animation(self.scene_id, option)

    async def async_added_to_hass(self) -> None:
        self._toggles.add_state_listener(self._on_global_change)

    async def async_will_remove_from_hass(self) -> None:
        self._toggles.remove_state_listener(self._on_global_change)

    @callback
    def _on_global_change(self) -> None:
        self.async_write_ha_state()
