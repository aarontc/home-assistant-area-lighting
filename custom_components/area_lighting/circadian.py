"""Circadian curve: percent, color temperature, brightness and payloads.

The formulas come from circadian_lighting 2.1.6 by claytonjn
(https://github.com/claytonjn/hass-circadian_lighting), licensed under
Apache-2.0 like this repository.

Percent runs from -100 at solar midnight through 0 at sunrise and sunset
to +100 at solar noon, as a half-parabola between each pair of events.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Mapping
from typing import Any

from homeassistant.util.color import color_temperature_to_rgb

from .const import CIRCADIAN_BRIGHTNESS, CIRCADIAN_CT, CIRCADIAN_RGB

SUNRISE = "sunrise"
SUNSET = "sunset"
NOON = "noon"
MIDNIGHT = "midnight"


def relevant_events(events: Iterable[tuple[str, float]], now_ts: float) -> dict[str, float]:
    """Return the two sun events at or before `now_ts` and the two after it."""
    ordered = sorted(events, key=lambda event: event[1])
    i = bisect.bisect_right([ts for _, ts in ordered], now_ts)
    return dict(ordered[max(i - 2, 0) : i + 2])


def percent(events: Mapping[str, float], now_ts: float) -> float | None:
    """Return the curve's percent at `now_ts`, or None if it cannot be computed.

    `events` is the window from `relevant_events`. Unlike circadian_lighting,
    a time exactly on sunrise or sunset gives 0 instead of an error.
    """
    try:
        sunrise = events[SUNRISE]
        sunset = events[SUNSET]
        noon = events[NOON]
        midnight = events[MIDNIGHT]
    except KeyError:
        return None

    if sunrise <= now_ts <= sunset:
        h, k = noon, 100
        x = sunrise if now_ts < noon else sunset
    elif sunset <= now_ts <= sunrise:
        h, k = midnight, -100
        x = sunset if now_ts < midnight else sunrise
    else:
        return None

    if h == x:
        return None
    return k * (1 - ((now_ts - h) / (h - x)) ** 2)


def kelvin(pct: float, min_colortemp: float, max_colortemp: float) -> float:
    """Color temperature: linear in percent by day, the minimum all night."""
    if pct > 0:
        return min_colortemp + (max_colortemp - min_colortemp) * pct / 100
    return min_colortemp


def brightness_pct(pct: float, min_brightness: float, max_brightness: float) -> float:
    """Brightness percentage: the maximum by day, following the curve at night."""
    if pct > 0:
        return max_brightness
    return min_brightness + (max_brightness - min_brightness) * (100 + pct) / 100


def to_brightness(brightness_pct: float) -> int:
    """Convert a percentage to light.turn_on's 1-255 `brightness`."""
    return min(255, max(1, round(brightness_pct * 2.55)))


def turn_on_payload(circadian_type: str, kelvin: float, brightness_pct: float) -> dict[str, Any]:
    """Build light.turn_on data for a light of the given circadian type."""
    payload: dict[str, Any] = {"brightness": to_brightness(brightness_pct)}
    if circadian_type == CIRCADIAN_CT:
        payload["color_temp_kelvin"] = round(kelvin)
    elif circadian_type == CIRCADIAN_RGB:
        payload["rgb_color"] = tuple(round(c) for c in color_temperature_to_rgb(kelvin))
    elif circadian_type != CIRCADIAN_BRIGHTNESS:
        raise ValueError(f"unknown circadian_type {circadian_type!r}")
    return payload
