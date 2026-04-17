"""Alert pattern execution engine for Area Lighting.

Flashes lights in an area according to a named pattern, then restores
their previous state. Orthogonal to the scene state machine — alerts
don't cause scene transitions, but coordinate with the controller to
suppress manual detection and pause timers.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, State  # noqa: F401

from .models import AlertPattern, AlertStep  # noqa: F401

_LOGGER = logging.getLogger(__name__)

COLOR_MODES = {"hs", "rgb", "rgbw", "rgbww", "xy"}

_CAPTURE_ATTRS = (
    "brightness",
    "color_mode",
    "color_temp_kelvin",
    "hs_color",
    "rgb_color",
    "xy_color",
)

_RESTORE_ATTRS = (
    "brightness",
    "color_temp_kelvin",
    "hs_color",
    "rgb_color",
    "xy_color",
)


def _is_color_capable(state: State) -> bool:
    modes = state.attributes.get("supported_color_modes") or []
    return bool(set(modes) & COLOR_MODES)


def filter_lights_by_target(
    light_ids: list[str],
    target: str,
    get_state: Callable[[str], State | None],
) -> list[str]:
    result: list[str] = []
    for eid in light_ids:
        state = get_state(eid)
        if state is None or state.state == "unavailable":
            continue
        if target == "all":
            result.append(eid)
        elif target == "color":
            if _is_color_capable(state):
                result.append(eid)
        elif target == "white":
            if not _is_color_capable(state):
                result.append(eid)
    return result


def capture_light_states(
    light_ids: list[str],
    get_state: Callable[[str], State | None],
) -> dict[str, dict[str, Any]]:
    captured: dict[str, dict[str, Any]] = {}
    for eid in light_ids:
        state = get_state(eid)
        if state is None:
            continue
        entry: dict[str, Any] = {"state": state.state}
        for attr in _CAPTURE_ATTRS:
            val = state.attributes.get(attr)
            if val is not None:
                entry[attr] = val
        captured[eid] = entry
    return captured


async def restore_light_states(
    captured: dict[str, dict[str, Any]],
    async_call: Callable[..., Any],
) -> None:
    for eid, data in captured.items():
        if data.get("state") == "off":
            await async_call("light", "turn_off", entity_id=eid)
        else:
            kwargs: dict[str, Any] = {"entity_id": eid, "transition": 0}
            color_mode = data.get("color_mode")
            for attr in _RESTORE_ATTRS:
                val = data.get(attr)
                if val is not None:
                    if attr in ("hs_color", "rgb_color", "xy_color"):
                        if color_mode and attr.startswith(color_mode.split("_")[0]):
                            kwargs[attr] = val
                    else:
                        kwargs[attr] = val
            await async_call("light", "turn_on", **kwargs)
