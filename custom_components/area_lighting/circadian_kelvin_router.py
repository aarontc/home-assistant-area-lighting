"""Circadian kelvin-routing for Area Lighting.

While the `circadian` scene is active in an area, this module's
`CircadianKelvinRouter` subscribes to a configured source entity's
`colortemp` attribute and dispatches the area's routed lights between
mutually-exclusive routes. The pure `select_route` function is split
out so it can be unit-tested without an HA harness.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CIRCADIAN_KELVIN_HYSTERESIS
from .models import CircadianKelvinRouteConfig, CircadianKelvinRoutesConfig

_LOGGER = logging.getLogger(__name__)


def select_route(
    routes: Sequence[CircadianKelvinRouteConfig],
    colortemp: float | None,
    current_index: int | None,
) -> int:
    """Pick the index of the route that should be active.

    Selection rules:
      - If `colortemp` is None (missing / unavailable), the fallback is
        selected.
      - The currently-active route (`current_index`) stays active while
        `colortemp` is within its declared range expanded by
        CIRCADIAN_KELVIN_HYSTERESIS on each side.
      - Otherwise the first banded route whose strict range contains
        `colortemp` is selected.
      - If no banded route matches, the fallback is selected.
      - The fallback's index is returned when no other route matches.
        If no fallback exists (degenerate input), the first route is
        returned.
    """
    fallback_index = next((i for i, r in enumerate(routes) if r.is_fallback), 0)
    if colortemp is None:
        return fallback_index

    if (
        current_index is not None
        and 0 <= current_index < len(routes)
        and not routes[current_index].is_fallback
    ):
        lo, hi = routes[current_index].kelvin_range  # type: ignore[misc]
        if (lo - CIRCADIAN_KELVIN_HYSTERESIS) <= colortemp <= (hi + CIRCADIAN_KELVIN_HYSTERESIS):
            return current_index

    for i, route in enumerate(routes):
        if route.is_fallback:
            continue
        lo, hi = route.kelvin_range  # type: ignore[misc]
        if lo <= colortemp <= hi:
            return i

    return fallback_index


class CircadianKelvinRouter:
    """Per-area router that swaps routed lights based on a source's colortemp.

    Active only while the area is in the `circadian` scene. Outside of
    that, the state-change listener is deregistered and reconciliation
    is suppressed.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        area_id: str,
        config: CircadianKelvinRoutesConfig,
    ) -> None:
        self._hass = hass
        self._area_id = area_id
        self._config = config
        self._unsub: Any = None
        self._current_index: int | None = None
        self._reconcile_lock = asyncio.Lock()

    async def sync_to_state(self, scene_slug: str | None) -> None:
        """Called after every controller state transition.

        Registers / deregisters the listener and reconciles immediately
        on first entry to circadian.
        """
        if scene_slug == "circadian":
            if self._unsub is None:
                self._unsub = async_track_state_change_event(
                    self._hass,
                    [self._config.source],
                    self._on_source_changed,
                )
            await self._reconcile()
        else:
            if self._unsub is not None:
                self._unsub()
                self._unsub = None
            self._current_index = None

    @callback
    def _on_source_changed(self, event: Event[EventStateChangedData]) -> None:
        """HA fires this for every state change on `source`."""
        self._hass.async_create_task(self._reconcile())

    async def _reconcile(self) -> None:
        """Reconcile light state against the active route, idempotently."""
        async with self._reconcile_lock:
            colortemp = self._read_colortemp()
            new_index = select_route(self._config.routes, colortemp, self._current_index)

            if new_index == self._current_index:
                return

            prev_index = self._current_index
            self._current_index = new_index
            active = self._config.routes[new_index]
            inactive_lights = self._config.all_route_lights - set(active.lights)

            # Diff against current HA state: only issue calls for lights that
            # need to change, so reconciliation is truly idempotent.
            off_calls_to_issue = [
                eid
                for eid in sorted(inactive_lights)
                if (s := self._hass.states.get(eid)) is not None and s.state == "on"
            ]
            on_calls_to_issue = [
                eid
                for eid in sorted(active.lights)
                if (s := self._hass.states.get(eid)) is None or s.state != "on"
            ]

            _LOGGER.info(
                "Area %s: kelvin_router routing -> %s (colortemp=%s, prev=%s, turn_off=%d, turn_on=%d)",
                self._area_id,
                self._describe_route(new_index),
                colortemp,
                self._describe_route(prev_index) if prev_index is not None else "none",
                len(off_calls_to_issue),
                len(on_calls_to_issue),
            )

            tasks: list = [
                self._hass.services.async_call(
                    "light",
                    "turn_off",
                    {
                        "entity_id": entity_id,
                        "transition": int(self._config.crossfade_seconds),
                    },
                    blocking=True,
                )
                for entity_id in off_calls_to_issue
            ]
            tasks.extend(
                self._hass.services.async_call(
                    "light",
                    "turn_on",
                    {
                        "entity_id": entity_id,
                        "transition": int(self._config.crossfade_seconds),
                    },
                    blocking=True,
                )
                for entity_id in on_calls_to_issue
            )
            if tasks:
                await asyncio.gather(*tasks)

    def _describe_route(self, index: int) -> str:
        """Return a human-readable label for a route by index."""
        route = self._config.routes[index]
        if route.is_fallback:
            return f"fallback[{','.join(sorted(route.lights))}]"
        lo, hi = route.kelvin_range  # type: ignore[misc]
        return f"banded[{lo}-{hi}K]"

    def _read_colortemp(self) -> float | None:
        state = self._hass.states.get(self._config.source)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        raw = state.attributes.get("colortemp")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
