"""Circadian kelvin-routing for Area Lighting.

While the `circadian` scene is active in an area, this module's
`CircadianKelvinRouter` dispatches the area's routed lights between
mutually-exclusive routes by the built-in clock's kelvin. The controller's
circadian tick drives each reconcile. The pure `select_route` function is
split out so it can be unit-tested without an HA harness.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from typing import Any

from homeassistant.core import HomeAssistant

from .const import CIRCADIAN_KELVIN_HYSTERESIS, DOMAIN
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
    """Per-area router that swaps routed lights based on the clock's kelvin.

    Active only while the area is in the `circadian` scene. Outside of
    that, reconciliation is suppressed.
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
        self._active = False
        self._current_index: int | None = None
        self._last_shed_ids: frozenset[str] = frozenset()
        self._adopted_off_ids: frozenset[str] = frozenset()
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
        """Called after every controller state transition and circadian tick.

        Activates the router and reconciles while the area is in
        circadian, and deactivates it otherwise.
        """
        if scene_slug == "circadian":
            self._active = True
            await self._reconcile()
        else:
            self.deactivate()

    def deactivate(self) -> None:
        """Deactivate and reset routing state.

        Called at the top of the controller's _stop_circadian (every
        leave-circadian or dim-suspend path) and by sync_to_state for any
        non-circadian scene, so no reconcile runs against the outgoing
        circadian scene.
        """
        self._active = False
        self._current_index = None
        self._last_shed_ids = frozenset()
        self._adopted_off_ids = frozenset()

    def adopt_current_state(self) -> None:
        """Activate with the route whose lights are on, without sending a
        command. Does nothing when already active.

        The current route is the first one with a light that is on, or, when
        no route light is on, the one the clock's kelvin selects. The current
        shed set is taken as the last one, so the next reconcile is stable
        unless the kelvin has crossed a route boundary. Active-route lights
        that are off stay off until the route or shed set changes.
        """
        if self._active:
            return
        ctrl = self._controller()
        cluster_members = self._route_used_cluster_members(ctrl)
        for index, route in enumerate(self._config.routes):
            if any(
                (s := self._hass.states.get(eid)) is not None and s.state == "on"
                for eid in self._expand_clusters(route.lights, cluster_members)
            ):
                self._current_index = index
                break
        else:
            kelvin = ctrl.circadian_kelvin() if ctrl is not None else None
            self._current_index = select_route(self._config.routes, kelvin, None)
        self._last_shed_ids = frozenset(ctrl.dr_shed_ids) if ctrl is not None else frozenset()
        self._adopted_off_ids = frozenset(
            eid
            for eid in self._expand_clusters(
                self._config.routes[self._current_index].lights, cluster_members
            )
            if (s := self._hass.states.get(eid)) is None or s.state != "on"
        )
        self._active = True

    def _controller(self) -> Any:
        controllers = self._hass.data.get(DOMAIN, {}).get("controllers", {})
        return controllers.get(self._area_id)

    async def _reconcile(self) -> None:
        """Reconcile light state against the active route, idempotently."""
        async with self._reconcile_lock:
            if not self._active:
                # Stale reconcile that waited on the lock while deactivate()
                # ran: the area is leaving (or has left) circadian, so
                # driving lights here would fight the incoming scene.
                return
            ctrl = self._controller()
            generation = ctrl.circadian_generation if ctrl is not None else 0
            colortemp = ctrl.circadian_kelvin() if ctrl is not None else None
            prev_index = self._current_index
            new_index = select_route(self._config.routes, colortemp, prev_index)
            # Publish the selection BEFORE the controller refreshes the shed
            # set, so the controller sizes the shed over the route this
            # reconcile is activating (hysteresis-consistent: it reads
            # current_index back and passes it to select_route).
            self._current_index = new_index
            if ctrl is not None:
                # The circadian shed set is sized over the ACTIVE route, so a
                # colortemp change can change it: have the controller refresh
                # it (and converge its non-route circadian lights) before we
                # read it. Never calls back into this router.
                await ctrl.recompute_and_apply_circadian_dr()
            if not self._active:
                # deactivate() ran while the controller call above was in
                # flight: the area is leaving (or has left) circadian, so
                # dispatching route commands now would fight the incoming
                # scene.
                return
            shed = frozenset(ctrl.dr_shed_ids) if ctrl is not None else frozenset()

            stable = new_index == prev_index and shed == self._last_shed_ids
            cluster_members = self._cluster_members_under_dr(ctrl)
            restore_only: frozenset[str] | None = None
            if not cluster_members and self._last_shed_ids and not shed:
                # Demand response just cleared (shed set went non-empty ->
                # empty) and cluster expansion is off again. An aggregate
                # zone still reads "on" from its kept members, so aggregate
                # diffing would skip the turn_on that restores a previously
                # shed member. Force ONE member-level pass over the
                # route-used clusters; the next reconcile resumes
                # aggregate batching.
                cluster_members = self._route_used_cluster_members(ctrl)
                if new_index == prev_index:
                    # Same route: relight ONLY what demand response shed.
                    # A kept active-route light the user manually turned
                    # off while shedding was active was never this
                    # router's to turn off, so the clear must not
                    # force-relight it. On a simultaneous route change the
                    # full new-route bring-up wins instead.
                    restore_only = self._last_shed_ids
            if stable and not cluster_members:
                # Same route, same shed set, no cluster expansion in play:
                # the previous dispatch's targets still stand, so skip the
                # diff entirely (this also leaves manual per-light changes
                # alone on stable reconciles).
                return

            self._last_shed_ids = shed
            if not stable:
                self._adopted_off_ids = frozenset()
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
                # Members that were off when the router adopted the lights
                # at resume are not turned on.
                member_ids = {m for members in cluster_members.values() for m in members}
                active_lights &= member_ids - self._adopted_off_ids
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
            on_candidates = active_lights if restore_only is None else active_lights & restore_only
            on_calls_to_issue = [
                eid
                for eid in sorted(on_candidates)
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

            crossfade = self._config.crossfade_seconds
            if ctrl is None:
                await asyncio.gather(
                    *(
                        self._hass.services.async_call(
                            "light",
                            "turn_off",
                            {"entity_id": entity_id, "transition": crossfade},
                            blocking=True,
                        )
                        for entity_id in off_calls_to_issue
                    )
                )
                return
            # The controller drops a command that starts after an alert took
            # the area or the adjuster stopped, and sends a grouped light its
            # group's values. An alert that ends makes the router forget its
            # route, so a change it interrupted is made again.
            await asyncio.gather(
                *(
                    ctrl.async_circadian_call(
                        generation, "light.turn_off", entity_id=entity_id, transition=crossfade
                    )
                    for entity_id in off_calls_to_issue
                ),
                *(
                    ctrl.async_turn_on_route_light(entity_id, crossfade, generation)
                    for entity_id in on_calls_to_issue
                ),
            )

    def _cluster_members_under_dr(self, ctrl: Any) -> dict[str, list[str]]:
        """Cluster entity id -> member ids, non-empty only under demand
        response. While DR is active a route's cluster entity must be driven
        as its individual members (a zone turn_on would relight shed members
        through the aggregate); without DR clusters stay as-is to preserve
        batching. Only clusters actually used AS route lights are included:
        an unrelated cluster must not drag its members into the stable-path
        convergence (a directly-routed individual light that happens to be a
        member would be needlessly re-driven on stable reconciles)."""
        if ctrl is None or not ctrl.demand_response_active:
            return {}
        return self._route_used_cluster_members(ctrl)

    def _route_used_cluster_members(self, ctrl: Any) -> dict[str, list[str]]:
        """Cluster entity id -> member ids for clusters used AS route lights,
        regardless of demand-response state. Backs both the DR expansion
        above and the one-shot member restore on the DR-clear transition."""
        if ctrl is None:
            return {}
        route_lights = self._config.all_route_lights
        return {
            c.id: list(c.members)
            for c in ctrl.area.light_clusters
            if c.members and c.id in route_lights
        }

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
