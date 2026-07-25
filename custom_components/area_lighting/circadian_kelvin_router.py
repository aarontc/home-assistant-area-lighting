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
from collections.abc import Iterable, Sequence
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CIRCADIAN_KELVIN_HYSTERESIS, DOMAIN
from .models import CircadianKelvinRouteConfig, CircadianKelvinRoutesConfig

_LOGGER = logging.getLogger(__name__)


def read_source_colortemp(hass: HomeAssistant, source: str) -> float | None:
    """Read the routing colortemp from `source`'s `colortemp` attribute.

    Returns None when the entity is missing/unavailable/unknown or the
    attribute is absent/unparseable. This is the ONLY colortemp-read policy
    for kelvin routing, shared by the router's reconcile and the
    controller's shed sizing, so both always select the same route (no
    per-component fallback sources).
    """
    state = hass.states.get(source)
    if state is None or state.state in ("unavailable", "unknown"):
        return None
    raw = state.attributes.get("colortemp")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


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
        self._last_shed_ids: frozenset[str] = frozenset()
        self._reconcile_lock = asyncio.Lock()

    @property
    def current_index(self) -> int | None:
        """Index of the currently-active route (None while inactive).

        Exposed so the controller can size the circadian demand-response
        shed set over the SAME route this router considers active
        (select_route with this index applies the hysteresis grace).
        """
        return self._current_index

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
            self.deactivate()

    def deactivate(self) -> None:
        """Deregister the source listener and reset routing state.

        Called at the top of the controller's _disable_circadian_switches
        (every leave-circadian or dim-suspend path) and by sync_to_state
        for any non-circadian scene, so the switch-off cannot fire the
        listener and enqueue a reconcile against the outgoing circadian
        scene.
        """
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._current_index = None
        self._last_shed_ids = frozenset()

    @callback
    def _on_source_changed(self, event: Event[EventStateChangedData]) -> None:
        """HA fires this for every state change on `source`."""
        self._hass.async_create_task(self._reconcile())

    def _controller(self) -> Any:
        controllers = self._hass.data.get(DOMAIN, {}).get("controllers", {})
        return controllers.get(self._area_id)

    async def _reconcile(self) -> None:
        """Reconcile light state against the active route, idempotently."""
        async with self._reconcile_lock:
            if self._unsub is None:
                # Stale reconcile enqueued before deactivate() deregistered
                # the listener: the area is leaving (or has left) circadian,
                # so driving lights here would fight the incoming scene. The
                # direct call from sync_to_state("circadian") is unaffected:
                # it runs after _unsub is set.
                return
            colortemp = self._read_colortemp()
            prev_index = self._current_index
            new_index = select_route(self._config.routes, colortemp, prev_index)
            # Publish the selection BEFORE the controller refreshes the shed
            # set, so the controller sizes the shed over the route this
            # reconcile is activating (hysteresis-consistent: it reads
            # current_index back and passes it to select_route).
            self._current_index = new_index
            ctrl = self._controller()
            if ctrl is not None:
                # The circadian shed set is sized over the ACTIVE route, so a
                # colortemp change can change it: have the controller refresh
                # it (and converge its non-route circadian lights) before we
                # read it. Never calls back into this router.
                await ctrl.recompute_and_apply_circadian_dr()
            if self._unsub is None:
                # deactivate() ran while the controller call above was in
                # flight: the area is leaving (or has left) circadian, so
                # dispatching route commands now would fight the incoming
                # scene.
                return
            shed = frozenset(ctrl.dr_shed_ids) if ctrl is not None else frozenset()

            stable = new_index == prev_index and shed == self._last_shed_ids
            cluster_members = self._cluster_members_under_dr(ctrl)
            if stable and not cluster_members:
                # Same route, same shed set, no cluster expansion in play:
                # the previous dispatch's targets still stand, so skip the
                # diff entirely (this also leaves manual per-light changes
                # alone on stable reconciles).
                return

            self._last_shed_ids = shed
            active = self._config.routes[new_index]
            active_lights = self._expand_clusters(active.lights, cluster_members) - shed
            inactive_lights = (
                self._expand_clusters(self._config.all_route_lights, cluster_members)
                - active_lights
            )
            if stable:
                # Same route and shed set, but cluster routes expand to
                # members whose physical state can drift while stable (e.g.
                # a shed member relit through the aggregate zone): converge
                # ONLY the expanded members against live state; every other
                # route light keeps the stable-path hands-off behavior.
                member_ids = {m for members in cluster_members.values() for m in members}
                active_lights &= member_ids
                inactive_lights &= member_ids
                if not active_lights and not inactive_lights:
                    return

            # Diff against current HA state: only issue calls for lights that
            # need to change, so reconciliation is truly idempotent.
            off_calls_to_issue = [
                eid
                for eid in sorted(inactive_lights)
                if (s := self._hass.states.get(eid)) is not None and s.state == "on"
            ]
            on_calls_to_issue = [
                eid
                for eid in sorted(active_lights)
                if (s := self._hass.states.get(eid)) is None or s.state != "on"
            ]

            if stable and not off_calls_to_issue and not on_calls_to_issue:
                # Member state already consistent: keep the stable path
                # silent, exactly like the no-expansion early return.
                return

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

    def _cluster_members_under_dr(self, ctrl: Any) -> dict[str, list[str]]:
        """Cluster entity id -> member ids, non-empty only under demand
        response. While DR is active a route's cluster entity must be driven
        as its individual members (a zone turn_on would relight shed members
        through the aggregate); without DR clusters stay as-is to preserve
        batching."""
        if ctrl is None or not ctrl.demand_response_active:
            return {}
        return {c.id: list(c.members) for c in ctrl.area.light_clusters if c.members}

    @staticmethod
    def _expand_clusters(
        entity_ids: Iterable[str],
        cluster_members: dict[str, list[str]],
    ) -> set[str]:
        """Replace cluster entity ids with their members; others pass through."""
        out: set[str] = set()
        for entity_id in entity_ids:
            members = cluster_members.get(entity_id)
            out.update(members if members else (entity_id,))
        return out

    def _describe_route(self, index: int) -> str:
        """Return a human-readable label for a route by index."""
        route = self._config.routes[index]
        if route.is_fallback:
            return f"fallback[{','.join(sorted(route.lights))}]"
        lo, hi = route.kelvin_range  # type: ignore[misc]
        return f"banded[{lo}-{hi}K]"

    def _read_colortemp(self) -> float | None:
        return read_source_colortemp(self._hass, self._config.source)
