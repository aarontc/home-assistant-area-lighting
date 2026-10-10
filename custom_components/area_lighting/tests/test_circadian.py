"""Unit tests for the circadian curve in circadian.py."""

from __future__ import annotations

import bisect
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import astral.sun
import pytest
from astral import Observer
from homeassistant.util.color import color_temperature_to_rgb

from custom_components.area_lighting.circadian import (
    brightness_pct,
    kelvin,
    percent,
    relevant_events,
    to_brightness,
    turn_on_payload,
)

HOUR = 3600.0


def _events(
    *,
    midnight: float = 0,
    sunrise: float = 6,
    noon: float = 12,
    sunset: float = 18,
) -> list[tuple[str, float]]:
    """Sun events on days -1, 0 and 1, each given as an hour of its day."""
    events = []
    for day in (-1, 0, 1):
        base = day * 24
        events += [
            ("midnight", (base + midnight) * HOUR),
            ("sunrise", (base + sunrise) * HOUR),
            ("noon", (base + noon) * HOUR),
            ("sunset", (base + sunset) * HOUR),
        ]
    return events


def _percent_at(hours: float, events: list[tuple[str, float]] | None = None) -> float | None:
    now_ts = hours * HOUR
    return percent(relevant_events(events or _events(), now_ts), now_ts)


def test_relevant_events_returns_two_before_and_two_after():
    events = list(reversed(_events()))
    assert relevant_events(events, 9 * HOUR) == {
        "midnight": 0,
        "sunrise": 6 * HOUR,
        "noon": 12 * HOUR,
        "sunset": 18 * HOUR,
    }
    assert relevant_events(events, 20 * HOUR) == {
        "noon": 12 * HOUR,
        "sunset": 18 * HOUR,
        "midnight": 24 * HOUR,
        "sunrise": 30 * HOUR,
    }


def test_percent_is_100_at_noon_and_minus_100_at_midnight():
    assert _percent_at(12) == 100
    assert _percent_at(0) == -100
    assert _percent_at(24) == -100


def test_percent_is_75_at_quarter_points():
    assert _percent_at(9) == pytest.approx(75)
    assert _percent_at(15) == pytest.approx(75)
    assert _percent_at(21) == pytest.approx(-75)
    assert _percent_at(3) == pytest.approx(-75)


def test_percent_is_zero_exactly_at_sunrise_and_sunset():
    assert _percent_at(6) == 0
    assert _percent_at(18) == 0


def test_percent_uses_separate_half_parabolas_when_noon_is_off_centre():
    events = _events(midnight=1, sunrise=6, noon=13, sunset=18)
    assert _percent_at(13, events) == 100
    assert _percent_at(9.5, events) == pytest.approx(75)
    assert _percent_at(15.5, events) == pytest.approx(75)
    assert _percent_at(21.5, events) == pytest.approx(-75)
    assert _percent_at(27.5, events) == pytest.approx(-75)


def test_percent_is_none_when_an_event_is_missing():
    window = relevant_events(_events(), 9 * HOUR)
    del window["noon"]
    assert percent(window, 9 * HOUR) is None
    assert percent({}, 9 * HOUR) is None


def test_kelvin_is_minimum_overnight_and_linear_by_day():
    assert kelvin(-50, 2500, 5500) == 2500
    assert kelvin(0, 2500, 5500) == 2500
    assert kelvin(50, 2500, 5500) == 4000
    assert kelvin(100, 2500, 5500) == 5500


def test_brightness_is_maximum_by_day_and_follows_the_curve_overnight():
    assert brightness_pct(50, 10, 90) == 90
    assert brightness_pct(0, 10, 90) == 90
    assert brightness_pct(-50, 10, 90) == 50
    assert brightness_pct(-100, 10, 90) == 10


def test_to_brightness_rounds_and_clamps():
    assert to_brightness(75) == 191
    assert to_brightness(1) == 3
    assert to_brightness(100) == 255
    assert to_brightness(0) == 1
    assert to_brightness(101) == 255


def test_payload_per_circadian_type():
    r, g, b = (round(c) for c in color_temperature_to_rgb(2700.4))
    assert turn_on_payload("ct", 2700.4, 75) == {
        "brightness": 191,
        "color_temp_kelvin": 2700,
    }
    assert turn_on_payload("rgb", 2700.4, 75) == {
        "brightness": 191,
        "rgb_color": (r, g, b),
    }
    assert turn_on_payload("brightness", 2700.4, 75) == {"brightness": 191}
    with pytest.raises(ValueError, match="unknown circadian_type"):
        turn_on_payload("xy", 2700.4, 75)


def _reference_percent(events: list[tuple[str, float]], now_ts: float) -> float:
    """circadian_lighting 2.1.6, `_async_relevant_events` and
    `async_calc_percent`, transliterated from
    haconf:custom_components/circadian_lighting/__init__.py:254-298.
    """
    events = sorted(events, key=lambda x: x[1])
    index_now = bisect.bisect([ts for _, ts in events], now_ts)
    today = dict(events[index_now - 2 : index_now + 2])

    if today["sunrise"] < now_ts < today["sunset"]:
        h = today["noon"]
        k = 100
        x = today["sunrise"] if now_ts < today["noon"] else today["sunset"]
    elif today["sunset"] < now_ts < today["sunrise"]:
        h = today["midnight"]
        k = -100
        x = today["sunset"] if now_ts < today["midnight"] else today["sunrise"]

    y = 0
    a = (y - k) / (h - x) ** 2
    return a * (now_ts - h) ** 2 + k


@pytest.mark.parametrize("day", [date(2026, 6, 21), date(2026, 12, 21)])
def test_percent_matches_circadian_lighting_reference(day: date):
    tz = ZoneInfo("America/Los_Angeles")
    observer = Observer(47.6, -122.3, 0)
    events = []
    for offset in (-1, 0, 1):
        d = day + timedelta(days=offset)
        for name, fn in (
            ("sunrise", astral.sun.sunrise),
            ("sunset", astral.sun.sunset),
            ("noon", astral.sun.noon),
            ("midnight", astral.sun.midnight),
        ):
            events.append((name, fn(observer, d, tz).timestamp()))

    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    for step in range(96):
        now_ts = (start + timedelta(minutes=15 * step)).timestamp()
        ours = percent(relevant_events(events, now_ts), now_ts)
        assert ours is not None
        assert ours == pytest.approx(_reference_percent(events, now_ts), abs=1e-9)
