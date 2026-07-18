"""Switch platform for Area Lighting - user-facing toggles."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN
from .controller import AreaLightingController

_LOGGER = logging.getLogger(__name__)

# (attr_suffix, name_suffix, icon, default)
SWITCH_DEFS = [
    ("motion_light_enabled", "Motion Light Enabled", "mdi:motion-sensor", True),
    ("ambience_enabled", "Ambience Enabled", "mdi:television-ambient-light", False),
    ("night_mode", "Night Mode", "mdi:weather-night", False),
    # 'Shield off' conveys "the ambient guard is disabled" — motion is
    # allowed to take over ambient-like scenes.
    ("motion_override_ambient", "Motion Override Ambient", "mdi:shield-off-outline", False),
    ("occupancy_timeout_enabled", "Occupancy Timeout Enabled", "mdi:timer-cog-outline", True),
]

# (flag, name, icon, unique_id, entity_id)
GLOBAL_SWITCH_DEFS = [
    (
        "motion_lights_enabled",
        "Area Lighting Motion Lights (Global)",
        "mdi:motion-sensor",
        "area_lighting_global_motion_lights_enabled",
        "switch.area_lighting_motion_lights_enabled",
    ),
    (
        "occupancy_timeout_enabled",
        "Area Lighting Occupancy Timeout (Global)",
        "mdi:timer-cog-outline",
        "area_lighting_global_occupancy_timeout_enabled",
        "switch.area_lighting_occupancy_timeout_enabled",
    ),
]


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Area Lighting switch entities."""
    if discovery_info is None:
        return

    controllers: dict[str, AreaLightingController] = hass.data[DOMAIN]["controllers"]
    entities = []
    for controller in controllers.values():
        for attr, name_suffix, icon, _default in SWITCH_DEFS:
            entities.append(AreaLightingSwitch(controller, attr, name_suffix, icon))
    async_add_entities(entities)


class AreaLightingSwitch(SwitchEntity):
    """A switch that reads/writes a boolean property on the controller."""

    def __init__(
        self,
        controller: AreaLightingController,
        attr: str,
        name_suffix: str,
        icon: str,
    ) -> None:
        self._controller = controller
        self._attr_key = attr
        area = controller.area
        self._attr_name = f"{area.name} {name_suffix}"
        self._attr_unique_id = f"area_lighting_{area.id}_{attr}"
        self._attr_icon = icon
        self.entity_id = f"switch.{area.id}_{attr}"

    @property
    def is_on(self) -> bool:
        return bool(getattr(self._controller, self._attr_key))

    async def async_turn_on(self, **kwargs: Any) -> None:
        _LOGGER.debug(
            "Area %s: switch %s set to on",
            self._controller.area.id,
            self._attr_key,
        )
        if self._attr_key == "ambience_enabled":
            await self._controller.async_set_ambience_enabled(True)
        elif self._attr_key == "occupancy_timeout_enabled":
            await self._controller.async_set_occupancy_timeout_enabled(True)
        else:
            setattr(self._controller, self._attr_key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _LOGGER.debug(
            "Area %s: switch %s set to off",
            self._controller.area.id,
            self._attr_key,
        )
        if self._attr_key == "ambience_enabled":
            await self._controller.async_set_ambience_enabled(False)
        elif self._attr_key == "occupancy_timeout_enabled":
            await self._controller.async_set_occupancy_timeout_enabled(False)
        else:
            setattr(self._controller, self._attr_key, False)

    async def async_added_to_hass(self) -> None:
        self._controller.add_state_listener(self._on_controller_change)

    async def async_will_remove_from_hass(self) -> None:
        self._controller.remove_state_listener(self._on_controller_change)

    @callback
    def _on_controller_change(self) -> None:
        self.async_write_ha_state()


class AreaLightingGlobalSwitch(SwitchEntity):
    """A global master switch backed by a GlobalToggles flag."""

    _attr_should_poll = False

    def __init__(self, toggles, flag, name, icon, unique_id, entity_id) -> None:
        self._toggles = toggles
        self._flag = flag
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = unique_id
        self.entity_id = entity_id

    @property
    def is_on(self) -> bool:
        return bool(getattr(self._toggles, self._flag))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, value: bool) -> None:
        if self._flag == "motion_lights_enabled":
            await self._toggles.async_set_motion_lights_enabled(value)
        else:
            await self._toggles.async_set_occupancy_timeout_enabled(value)

    async def async_added_to_hass(self) -> None:
        self._toggles.add_state_listener(self._on_change)

    async def async_will_remove_from_hass(self) -> None:
        self._toggles.remove_state_listener(self._on_change)

    @callback
    def _on_change(self) -> None:
        self.async_write_ha_state()
