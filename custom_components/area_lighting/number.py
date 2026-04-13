"""Number platform for Area Lighting - fadeout seconds configuration."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
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
    """Set up the Area Lighting number entities."""
    if discovery_info is None:
        return

    controllers: dict[str, AreaLightingController] = hass.data[DOMAIN]["controllers"]
    entities = []
    for controller in controllers.values():
        entities.append(AreaManualFadeoutNumber(controller))
        entities.append(AreaMotionFadeoutNumber(controller))
        entities.append(AreaMotionTimeoutNumber(controller))
        entities.append(AreaMotionNightTimeoutNumber(controller))
        entities.append(AreaOccupancyTimeoutNumber(controller))
        entities.append(AreaOccupancyNightTimeoutNumber(controller))
    async_add_entities(entities)


class _BaseAreaNumber(NumberEntity):
    """Shared base for area_lighting number entities bound to a controller.

    Subclasses define `_attr_name`, `entity_id`, display range/step, and
    implement `native_value` / `async_set_native_value` to read/write
    the underlying controller property.
    """

    def __init__(self, controller: AreaLightingController) -> None:
        self._controller = controller

    async def async_added_to_hass(self) -> None:
        self._controller.add_state_listener(self._on_controller_change)

    async def async_will_remove_from_hass(self) -> None:
        self._controller.remove_state_listener(self._on_controller_change)

    @callback
    def _on_controller_change(self) -> None:
        self.async_write_ha_state()


class _BaseFadeoutSeconds(_BaseAreaNumber):
    """Shared config for the two fadeout-in-seconds number entities."""

    _attr_native_min_value = 0.0
    _attr_native_max_value = 90.0
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "s"
    _attr_mode = NumberMode.SLIDER

    _controller_attr: str = ""

    @property
    def native_value(self) -> float:
        return getattr(self._controller, self._controller_attr)

    async def async_set_native_value(self, value: float) -> None:
        setattr(self._controller, self._controller_attr, float(value))


class AreaManualFadeoutNumber(_BaseFadeoutSeconds):
    """Fade duration for manual off (remote off button / off scene)."""

    _controller_attr = "manual_fadeout_seconds"
    # Manual = user-initiated (remote button / off scene).
    _attr_icon = "mdi:remote"

    def __init__(self, controller: AreaLightingController) -> None:
        super().__init__(controller)
        area = controller.area
        self._attr_name = f"{area.name} Manual Fadeout Seconds"
        self._attr_unique_id = f"area_lighting_{area.id}_manual_fadeout_seconds"
        self.entity_id = f"number.{area.id}_manual_fadeout_seconds"


class AreaMotionFadeoutNumber(_BaseFadeoutSeconds):
    """Fade duration for motion + occupancy timer expiry."""

    _controller_attr = "motion_fadeout_seconds"
    # Motion pause = "motion just ended and we're fading out".
    _attr_icon = "mdi:motion-pause-outline"

    def __init__(self, controller: AreaLightingController) -> None:
        super().__init__(controller)
        area = controller.area
        self._attr_name = f"{area.name} Motion Fadeout Seconds"
        self._attr_unique_id = f"area_lighting_{area.id}_motion_fadeout_seconds"
        self.entity_id = f"number.{area.id}_motion_fadeout_seconds"


class _BaseTimeoutMinutes(_BaseAreaNumber):
    """Shared config for the four timeout-in-minutes number entities.

    Stores seconds under the hood; presents minutes to the UI.
    """

    # Subclasses must set: _attr_name, _attr_unique_id, entity_id, _attr_icon
    _attr_native_min_value = 1.0
    _attr_native_max_value = 180.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.BOX

    # Name of the float-seconds attribute on the controller
    _controller_attr: str = ""

    @property
    def native_value(self) -> float:
        seconds = getattr(self._controller, self._controller_attr)
        return round(seconds / 60.0, 2)

    async def async_set_native_value(self, value: float) -> None:
        setattr(self._controller, self._controller_attr, float(value) * 60.0)


class AreaMotionTimeoutNumber(_BaseTimeoutMinutes):
    """Motion-off timeout in minutes (normal mode)."""

    _controller_attr = "motion_off_duration_seconds"

    def __init__(self, controller: AreaLightingController) -> None:
        super().__init__(controller)
        area = controller.area
        self._attr_name = f"{area.name} Motion Timeout"
        self._attr_unique_id = f"area_lighting_{area.id}_motion_timeout_minutes"
        # Full hourglass = countdown until motion-off fires.
        self._attr_icon = "mdi:timer-sand"
        self.entity_id = f"number.{area.id}_motion_timeout_minutes"


class AreaMotionNightTimeoutNumber(_BaseTimeoutMinutes):
    """Motion-off timeout in minutes (night mode)."""

    _controller_attr = "motion_night_off_duration_seconds"

    def __init__(self, controller: AreaLightingController) -> None:
        super().__init__(controller)
        area = controller.area
        self._attr_name = f"{area.name} Motion Night Timeout"
        self._attr_unique_id = f"area_lighting_{area.id}_motion_night_timeout_minutes"
        # Emptied hourglass = shorter night variant of the normal timeout.
        self._attr_icon = "mdi:timer-sand-empty"
        self.entity_id = f"number.{area.id}_motion_night_timeout_minutes"


class AreaOccupancyTimeoutNumber(_BaseTimeoutMinutes):
    """Occupancy timeout in minutes (normal mode)."""

    _controller_attr = "occupancy_off_duration_seconds"
    _attr_native_max_value = 720.0  # up to 12 hours

    def __init__(self, controller: AreaLightingController) -> None:
        super().__init__(controller)
        area = controller.area
        self._attr_name = f"{area.name} Occupancy Timeout"
        self._attr_unique_id = f"area_lighting_{area.id}_occupancy_timeout_minutes"
        # Account + clock = person-timer, the best semantic match.
        self._attr_icon = "mdi:account-clock"
        self.entity_id = f"number.{area.id}_occupancy_timeout_minutes"


class AreaOccupancyNightTimeoutNumber(_BaseTimeoutMinutes):
    """Occupancy timeout in minutes (night mode)."""

    _controller_attr = "occupancy_night_off_duration_seconds"
    _attr_native_max_value = 720.0

    def __init__(self, controller: AreaLightingController) -> None:
        super().__init__(controller)
        area = controller.area
        self._attr_name = f"{area.name} Occupancy Night Timeout"
        self._attr_unique_id = f"area_lighting_{area.id}_occupancy_night_timeout_minutes"
        # Outline variant = night/lighter version of AreaOccupancyTimeoutNumber.
        self._attr_icon = "mdi:account-clock-outline"
        self.entity_id = f"number.{area.id}_occupancy_night_timeout_minutes"
