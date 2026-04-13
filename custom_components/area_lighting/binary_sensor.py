"""Binary sensor platform for Area Lighting - occupancy state per area."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .controller import AreaLightingController

_LOGGER = logging.getLogger(__name__)


class AreaOccupiedBinarySensor(BinarySensorEntity):
    """Binary sensor that exposes whether an area is currently occupied.

    True when any occupancy sensor is active or the occupancy timer
    is still running. Always False for areas without occupancy sensors.
    """

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, controller: AreaLightingController) -> None:
        self._controller = controller
        area = controller.area
        self._attr_name = f"{area.name} Occupied"
        self._attr_unique_id = f"area_lighting_{area.id}_occupied"
        self.entity_id = f"binary_sensor.{area.id}_occupied"

    @property
    def is_on(self) -> bool:
        return self._controller.is_occupied

    async def async_added_to_hass(self) -> None:
        self._controller.add_state_listener(self._on_controller_change)

    async def async_will_remove_from_hass(self) -> None:
        self._controller.remove_state_listener(self._on_controller_change)

    @callback
    def _on_controller_change(self) -> None:
        self.async_write_ha_state()
