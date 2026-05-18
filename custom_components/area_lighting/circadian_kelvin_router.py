"""Circadian kelvin-routing for Area Lighting.

While the `circadian` scene is active in an area, this module's
`CircadianKelvinRouter` subscribes to a configured source entity's
`colortemp` attribute and dispatches the area's routed lights between
mutually-exclusive routes. The pure `select_route` function is split
out so it can be unit-tested without an HA harness.
"""

from __future__ import annotations

from collections.abc import Sequence

from .const import CIRCADIAN_KELVIN_HYSTERESIS
from .models import CircadianKelvinRouteConfig


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
