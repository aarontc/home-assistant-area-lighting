"""Clock-based scene animation targets."""

from __future__ import annotations

import math
from collections.abc import Collection

from homeassistant.components.light.const import DEFAULT_MAX_KELVIN, DEFAULT_MIN_KELVIN
from homeassistant.util import color as color_util

from .models import AnimationConfig

ANIMATED_COLOR_KEYS = frozenset({"hs_color", "rgb_color", "rgbw_color", "rgbww_color", "xy_color"})
COLOR_KEYS = ANIMATED_COLOR_KEYS | {"color_temp_kelvin", "color_temp", "color_mode"}
HUE_GAMUT_A = color_util.GamutType(
    color_util.XYPoint(0.704, 0.296),
    color_util.XYPoint(0.2151, 0.7106),
    color_util.XYPoint(0.138, 0.08),
)
HUE_GAMUT_B = color_util.GamutType(
    color_util.XYPoint(0.675, 0.322),
    color_util.XYPoint(0.409, 0.518),
    color_util.XYPoint(0.167, 0.04),
)
HUE_GAMUT_C = color_util.GamutType(
    color_util.XYPoint(0.692, 0.308),
    color_util.XYPoint(0.17, 0.7),
    color_util.XYPoint(0.153, 0.048),
)


def step_number(unix_time: float, period: float) -> int:
    """Return the shared step at UTC epoch seconds."""
    return math.floor(unix_time / period)


def expand_slots(palette: list[dict]) -> list[dict]:
    """Expand smooth weighted round robin, starting at the last first-entry slot."""
    weights = [entry.get("weight", 1) for entry in palette]
    total = sum(weights)
    current = [0] * len(palette)
    indices = []
    start = 0
    for position in range(total):
        for index, weight in enumerate(weights):
            current[index] += weight
        selected = max(range(len(palette)), key=current.__getitem__)
        current[selected] -= total
        indices.append(selected)
        if selected == 0:
            start = position
    return [
        {key: value for key, value in palette[index].items() if key != "weight"}
        for index in indices[start:] + indices[:start]
    ]


def animated_light_ids(entities: dict[str, dict], cluster_ids: Collection[str] = ()) -> list[str]:
    """Select coloured, on lights in the order the scene lists them.

    A circadian target is left out: an rgb light's payload carries rgb_color,
    but the curve drives it.
    """
    return [
        entity_id
        for entity_id, target in entities.items()
        if entity_id.startswith("light.")
        and entity_id not in cluster_ids
        and target.get("state") == "on"
        and target.get("animate") is not False
        and not target.get("circadian")
        and ANIMATED_COLOR_KEYS.intersection(target)
        and (
            "color_mode" not in target
            or target["color_mode"] in {"hs", "rgb", "rgbw", "rgbww", "xy"}
        )
    ]


def target_color_xy(target: dict) -> tuple[float, float] | None:
    """Convert a scene target's colour to xy."""
    if "xy_color" in target:
        x, y = target["xy_color"]
        return float(x), float(y)
    if "hs_color" in target:
        return color_util.color_hs_to_xy(*target["hs_color"])
    if "rgb_color" in target:
        return color_util.color_RGB_to_xy(*target["rgb_color"])
    if "rgbw_color" in target:
        return color_util.color_RGB_to_xy(*color_util.color_rgbw_to_rgb(*target["rgbw_color"]))
    if "rgbww_color" in target:
        red, green, blue, cold_white, warm_white = target["rgbww_color"]
        rgb = color_util.color_rgbww_to_rgb(
            red,
            green,
            blue,
            cold_white,
            warm_white,
            target.get("min_color_temp_kelvin", DEFAULT_MIN_KELVIN),
            target.get("max_color_temp_kelvin", DEFAULT_MAX_KELVIN),
        )
        return color_util.color_RGB_to_xy(*rgb)
    kelvin = target.get("color_temp_kelvin")
    if kelvin is None and "color_temp" in target:
        kelvin = color_util.color_temperature_mired_to_kelvin(target["color_temp"])
    if kelvin is not None:
        red, green, blue = color_util.color_temperature_to_rgb(kelvin)
        return color_util.color_RGB_to_xy(round(red), round(green), round(blue))
    return None


def target_is_desaturated(target: dict) -> bool:
    """Return whether a target is white or has less than 90 percent saturation."""
    if "color_temp_kelvin" in target or "color_temp" in target:
        return True
    if "hs_color" in target:
        saturation: float = target["hs_color"][1]
        return saturation < 90
    if "xy_color" in target:
        return color_util.color_xy_to_hs(*target["xy_color"])[1] < 90
    if "rgb_color" in target:
        rgb = target["rgb_color"]
    elif "rgbw_color" in target:
        rgb = color_util.color_rgbw_to_rgb(*target["rgbw_color"])
    elif "rgbww_color" in target:
        red, green, blue, cold_white, warm_white = target["rgbww_color"]
        rgb = color_util.color_rgbww_to_rgb(
            red,
            green,
            blue,
            cold_white,
            warm_white,
            target.get("min_color_temp_kelvin", DEFAULT_MIN_KELVIN),
            target.get("max_color_temp_kelvin", DEFAULT_MAX_KELVIN),
        )
    else:
        return False
    return color_util.color_RGB_to_hs(*rgb)[1] < 90


def point_segment_distance(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    """Return the shortest distance from a point to a closed segment."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.dist(point, start)
    fraction = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared
    fraction = max(0.0, min(1.0, fraction))
    return math.dist(point, (start[0] + fraction * dx, start[1] + fraction * dy))


def point_on_animation_segment(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> bool:
    """Accept a fade report before or after Hue clamps its endpoints."""
    if point_segment_distance(point, start, end) <= 0.03:
        return True
    for gamut in (HUE_GAMUT_A, HUE_GAMUT_B, HUE_GAMUT_C):
        clamped_start = (
            start
            if color_util.check_point_in_lamps_reach(start, gamut)
            else color_util.get_closest_point_to_point(start, gamut)
        )
        clamped_end = (
            end
            if color_util.check_point_in_lamps_reach(end, gamut)
            else color_util.get_closest_point_to_point(end, gamut)
        )
        if point_segment_distance(point, clamped_start, clamped_end) <= 0.03:
            return True
    return False


def brightness_in_range(brightness: float | None, *values: float | None) -> bool:
    """Allow brightness between the endpoints, with report tolerance."""
    endpoints = [value for value in values if value is not None]
    return (
        brightness is None
        or not endpoints
        or min(endpoints) - 10 <= brightness <= max(endpoints) + 10
    )


def build_step_targets(
    entities: dict[str, dict],
    animation: AnimationConfig,
    step: int,
    cluster_ids: Collection[str] = (),
) -> dict[str, dict]:
    """Overlay the current palette slots without changing the scene data."""
    targets = {
        entity_id: {key: value for key, value in target.items() if key != "animate"}
        for entity_id, target in entities.items()
    }
    slots = expand_slots(animation.palette)
    for index, entity_id in enumerate(animated_light_ids(entities, cluster_ids)):
        target = targets[entity_id]
        for key in COLOR_KEYS:
            target.pop(key, None)
        target.update(slots[(index + step) % len(slots)])
    return targets
