"""Alert pattern execution engine for Area Lighting.

Flashes lights in an area according to a named pattern, then restores
their previous state. Orthogonal to the scene state machine — alerts
don't cause scene transitions, but coordinate with the controller to
suppress manual detection and pause timers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, State

from .cluster_dispatch import select_dispatch_commands
from .models import AlertPattern, AlertStep

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
        elif target == "white" and not _is_color_capable(state):
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


async def _execute_steps(
    hass: HomeAssistant,
    controller: Any,
    pattern: AlertPattern,
    individual_light_ids: list[str],
    clusters: list[tuple[str, list[str]]],
) -> None:
    """Run the step sequence with repeat and start_inverted logic.

    Target filtering classifies *individual* lights by capability
    (color/white via supported_color_modes). The resulting entity set
    is then optimised through ``select_dispatch_commands`` so that Hue
    Zone clusters replace per-light calls where every member of a
    cluster falls within the same target cohort.
    """
    steps = list(pattern.steps)

    for cycle in range(pattern.repeat):
        effective_steps = steps
        if cycle == 0 and pattern.start_inverted and steps:
            first = steps[0]
            targeted = filter_lights_by_target(individual_light_ids, first.target, hass.states.get)
            if targeted:
                on_count = sum(
                    1
                    for eid in targeted
                    if (s := hass.states.get(eid)) is not None and s.state == "on"
                )
                majority_on = on_count > len(targeted) / 2
                first_is_on = first.state == "on"
                if majority_on == first_is_on:
                    effective_steps = list(reversed(steps))

        # Batch consecutive delay-0 steps that target disjoint entity
        # sets so commands like "color on + white off" dispatch
        # concurrently.  If a step targets entities already in the
        # batch (e.g. three_flashes: all-on then all-off), the batch
        # is flushed first so the commands stay sequential.
        batch: list[tuple[list[str], AlertStep]] = []
        batch_entities: set[str] = set()
        for step in effective_steps:
            targeted = filter_lights_by_target(individual_light_ids, step.target, hass.states.get)
            if targeted:
                targeted_set = set(targeted)
                if targeted_set & batch_entities:
                    await _apply_batch(hass, batch, clusters)
                    batch = []
                    batch_entities = set()
                batch.append((targeted, step))
                batch_entities |= targeted_set
            if step.delay > 0:
                if batch:
                    await _apply_batch(hass, batch, clusters)
                    batch = []
                    batch_entities = set()
                await asyncio.sleep(step.delay)
        if batch:
            await _apply_batch(hass, batch, clusters)

    if pattern.delay > 0:
        await asyncio.sleep(pattern.delay)


def _step_state_dict(step: AlertStep) -> dict[str, Any]:
    """Build the target state dict for an alert step."""
    if step.state == "off":
        return {"state": "off"}
    d: dict[str, Any] = {"state": "on", "transition": 0}
    if step.brightness is not None:
        d["brightness"] = step.brightness
    if step.rgb_color is not None:
        d["rgb_color"] = list(step.rgb_color)
    if step.color_temp_kelvin is not None:
        d["color_temp_kelvin"] = step.color_temp_kelvin
    if step.hs_color is not None:
        d["hs_color"] = list(step.hs_color)
    if step.xy_color is not None:
        d["xy_color"] = list(step.xy_color)
    if step.transition is not None:
        d["transition"] = step.transition
    return d


async def _apply_batch(
    hass: HomeAssistant,
    batch: list[tuple[list[str], AlertStep]],
    clusters: list[tuple[str, list[str]]],
) -> None:
    """Execute all light commands in a batch concurrently.

    Each (entity_ids, step) pair is expanded into per-light target
    states, then ``select_dispatch_commands`` coalesces them into
    cluster commands where possible.
    """
    # Build the unified entities dict across all steps in the batch.
    entities: dict[str, dict[str, Any]] = {}
    for targeted_ids, step in batch:
        state_dict = _step_state_dict(step)
        for eid in targeted_ids:
            entities[eid] = state_dict

    commands = select_dispatch_commands(entities, clusters)

    calls: list[Any] = []
    for entity_id, state_dict in commands:
        if state_dict.get("state") == "off":
            calls.append(
                hass.services.async_call(
                    "light", "turn_off", {"entity_id": entity_id}, blocking=True
                )
            )
        else:
            kwargs = {k: v for k, v in state_dict.items() if k != "state"}
            calls.append(
                hass.services.async_call(
                    "light", "turn_on", {"entity_id": entity_id, **kwargs}, blocking=True
                )
            )
    if calls:
        await asyncio.gather(*calls)


async def execute_alert(
    hass: HomeAssistant,
    controller: Any,
    pattern: AlertPattern,
) -> None:
    """Execute an alert pattern on an area's lights.

    1. Set _alert_active flag (suppresses manual detection)
    2. Capture light states
    3. Snapshot + cancel timers
    4. Execute steps x repeat
    5. Restore light states (if pattern.restore)
    6. Restore timer deadlines
    7. Clear _alert_active flag
    """
    # Classify on individual lights (not clusters) so target filtering
    # sees per-entity supported_color_modes.  Cluster optimization is
    # applied at dispatch time via select_dispatch_commands.
    individual_light_ids = [light.id for light in controller.area.lights]
    if not individual_light_ids:
        return

    clusters: list[tuple[str, list[str]]] = [
        (c.id, c.members) for c in controller.area.light_clusters
    ]

    timer_deadlines: dict[str, Any] = {}
    timers = {
        "_motion_timer": controller._motion_timer,
        "_motion_night_timer": controller._motion_night_timer,
        "_occupancy_timer": controller._occupancy_timer,
    }

    controller._alert_active = True
    try:
        captured = capture_light_states(individual_light_ids, hass.states.get)

        for name, timer in timers.items():
            if timer.is_active and timer.deadline_utc is not None:
                timer_deadlines[name] = timer.deadline_utc
            timer.cancel()

        _LOGGER.debug(
            "Area %s: alert started, %d lights captured, %d timers paused",
            controller.area.id,
            len(captured),
            len(timer_deadlines),
        )

        await _execute_steps(hass, controller, pattern, individual_light_ids, clusters)

        if pattern.restore and captured:

            async def _call(domain: str, service: str, **kwargs: Any) -> None:
                await hass.services.async_call(domain, service, kwargs, blocking=True)

            await restore_light_states(captured, _call)

    finally:
        for name, deadline in timer_deadlines.items():
            timers[name].restore(deadline)

        controller._alert_active = False
        _LOGGER.debug("Area %s: alert finished", controller.area.id)
