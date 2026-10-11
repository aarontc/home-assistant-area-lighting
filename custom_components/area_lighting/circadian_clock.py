"""Built-in circadian clock and the sensor that reports it.

The clock reads sun events for Home Assistant's configured location,
computes the circadian percent and color temperature, and sends
SIGNAL_CIRCADIAN_TICK with them every `interval` seconds.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from . import circadian
from .const import DOMAIN, SIGNAL_CIRCADIAN_TICK
from .models import CircadianConfig

_LOGGER = logging.getLogger(__name__)

# circadian.py's event names are also the names of the astral.sun functions.
_SUN_EVENTS = (circadian.SUNRISE, circadian.SUNSET, circadian.NOON, circadian.MIDNIGHT)


@dataclass(frozen=True)
class CircadianValues:
    percent: float
    kelvin: float


class CircadianClock:
    """Computes circadian values from the sun and ticks every `interval` seconds."""

    def __init__(self, hass: HomeAssistant, config: CircadianConfig) -> None:
        self.hass = hass
        self.config = config
        self.last: CircadianValues | None = None
        self._unsub_interval: Callable[[], None] | None = None
        self._warned = False

    def events(self, now: datetime | None = None) -> dict[str, float]:
        """Return the two sun events at or before `now` and the two after it."""
        now = now or dt_util.utcnow()
        today = dt_util.as_local(now).date()
        events = []
        for days in (-1, 0, 1):
            date = today + timedelta(days=days)
            for name in _SUN_EVENTS:
                when = get_astral_event_date(self.hass, name, date)
                if when is not None:
                    events.append((name, when.timestamp()))
        return circadian.relevant_events(events, now.timestamp())

    def values(self, now: datetime | None = None) -> CircadianValues | None:
        now = now or dt_util.utcnow()
        pct = circadian.percent(self.events(now), now.timestamp())
        if pct is None:
            return None
        kelvin = circadian.kelvin(pct, self.config.min_colortemp, self.config.max_colortemp)
        return CircadianValues(pct, kelvin)

    def async_start(self) -> None:
        self._unsub_interval = async_track_time_interval(
            self.hass,
            self._on_interval,
            timedelta(seconds=self.config.interval),
            cancel_on_shutdown=True,
        )

    def async_stop(self) -> None:
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None

    async def _on_interval(self, _now: datetime) -> None:
        await self.async_tick()

    async def async_tick(self) -> None:
        values = self.values()
        if values is None:
            if not self._warned:
                self._warned = True
                _LOGGER.warning(
                    "Circadian values cannot be computed from the sun events at this "
                    "location; keeping the last values"
                )
            return
        self.last = values
        async_dispatcher_send(self.hass, SIGNAL_CIRCADIAN_TICK, values)


class AreaLightingCircadianSensor(SensorEntity):
    """The circadian percent, with the color temperature as an attribute."""

    _attr_should_poll = False
    _attr_icon = "mdi:theme-light-dark"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_registry_visible_default = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._attr_name = "Area Lighting Circadian"
        self._attr_unique_id = "area_lighting_circadian"
        self.entity_id = "sensor.area_lighting_circadian"

    def _values(self) -> CircadianValues | None:
        clock: CircadianClock = self.hass.data[DOMAIN]["circadian_clock"]
        return clock.last or clock.values()

    @property
    def native_value(self) -> float | None:
        values = self._values()
        return None if values is None else round(values.percent, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        values = self._values()
        return None if values is None else {"colortemp": round(values.kelvin)}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_CIRCADIAN_TICK, self._on_tick)
        )

    @callback
    def _on_tick(self, _values: CircadianValues) -> None:
        self.async_write_ha_state()
