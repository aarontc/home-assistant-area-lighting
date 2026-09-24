"""Build light.turn_on arguments from one light's scene target.

Color selection follows Home Assistant's own scene reproduction
(homeassistant/components/light/reproduce_state.py), since scene targets
use the same shape as HA scene entities.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.util.color import color_temperature_mired_to_kelvin

from .const import SCENE_LIGHT_ON_ATTRIBUTES

# light.turn_on takes one of these, tried in Home Assistant's order.
COLOR_ATTRIBUTES = (
    "hs_color",
    "color_temp_kelvin",
    "rgb_color",
    "rgbw_color",
    "rgbww_color",
    "xy_color",
)

# The color argument that restores each light color_mode.
COLOR_ATTRIBUTE_BY_MODE = {
    "color_temp": "color_temp_kelvin",
    "hs": "hs_color",
    "rgb": "rgb_color",
    "rgbw": "rgbw_color",
    "rgbww": "rgbww_color",
    "xy": "xy_color",
}

# Color modes that take no color argument.
COLORLESS_MODES = frozenset({"onoff", "brightness"})


def light_turn_on_data(target: Mapping[str, Any]) -> dict[str, Any]:
    """Return the light.turn_on arguments for one light's scene target.

    Skips None values, which are never meaningful here (and Hue warns when
    `effect=None` is passed). A light reports every color representation at
    once, but light.turn_on takes exactly one, so this keeps the one the
    target's color_mode names, `white` for white mode, or else the first
    one present. Home Assistant 2026.3 dropped the mired `color_temp`
    argument, so config or snapshots that still carry it are sent as
    `color_temp_kelvin`.
    """
    data = {
        attr: target[attr] for attr in SCENE_LIGHT_ON_ATTRIBUTES if target.get(attr) is not None
    }
    mired = data.pop("color_temp", None)
    if mired is not None and "color_temp_kelvin" not in data:
        data["color_temp_kelvin"] = color_temperature_mired_to_kelvin(int(mired))

    colors = {attr: data.pop(attr) for attr in COLOR_ATTRIBUTES if attr in data}
    color_mode = target.get("color_mode")
    mode_attr = COLOR_ATTRIBUTE_BY_MODE.get(color_mode or "")
    if color_mode == "white" and "brightness" in data:
        data["white"] = data["brightness"]
    elif mode_attr in colors:
        data[mode_attr] = colors[mode_attr]
    elif colors and color_mode not in COLORLESS_MODES:
        first = next(iter(colors))
        data[first] = colors[first]
    return data
