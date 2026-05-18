"""Unit tests for circadian-kelvin route data models and selection logic."""

from __future__ import annotations

from custom_components.area_lighting.circadian_kelvin_router import select_route
from custom_components.area_lighting.models import (
    CircadianKelvinRouteConfig,
    CircadianKelvinRoutesConfig,
)


def test_banded_route_is_not_fallback():
    route = CircadianKelvinRouteConfig(lights=["light.foo"], kelvin_range=(4500, 5500))
    assert route.is_fallback is False


def test_fallback_route_has_no_range():
    route = CircadianKelvinRouteConfig(lights=["light.bar", "light.baz"])
    assert route.is_fallback is True
    assert route.kelvin_range is None


def test_routes_config_exposes_fallback_and_all_lights():
    banded = CircadianKelvinRouteConfig(lights=["light.fluor"], kelvin_range=(4500, 5500))
    fallback = CircadianKelvinRouteConfig(lights=["light.strip_1", "light.strip_2"])
    cfg = CircadianKelvinRoutesConfig(
        routes=[banded, fallback],
        source="switch.circadian_kitchen",
        crossfade_seconds=2.0,
    )
    assert cfg.fallback_route is fallback
    assert cfg.all_route_lights == {
        "light.fluor",
        "light.strip_1",
        "light.strip_2",
    }


def _routes():
    """Returns [banded_cool, banded_warm, fallback]."""
    return [
        CircadianKelvinRouteConfig(lights=["light.fluor"], kelvin_range=(4500, 5500)),
        CircadianKelvinRouteConfig(lights=["light.warm_strip"], kelvin_range=(2700, 3500)),
        CircadianKelvinRouteConfig(lights=["light.fallback_strip"]),
    ]


def test_selects_banded_route_when_in_range():
    assert select_route(_routes(), colortemp=5000, current_index=None) == 0


def test_selects_other_banded_route():
    assert select_route(_routes(), colortemp=3000, current_index=None) == 1


def test_selects_fallback_when_between_bands():
    assert select_route(_routes(), colortemp=4000, current_index=None) == 2


def test_selects_fallback_when_above_all_bands():
    assert select_route(_routes(), colortemp=6500, current_index=None) == 2


def test_selects_fallback_when_below_all_bands():
    assert select_route(_routes(), colortemp=2000, current_index=None) == 2


def test_selects_fallback_when_colortemp_is_none():
    assert select_route(_routes(), colortemp=None, current_index=0) == 2


def test_hysteresis_keeps_active_route_at_upper_edge():
    # current = banded_cool [4500, 5500]; colortemp = 5520 (within +25K)
    assert select_route(_routes(), colortemp=5520, current_index=0) == 0


def test_hysteresis_keeps_active_route_at_lower_edge():
    assert select_route(_routes(), colortemp=4480, current_index=0) == 0


def test_hysteresis_releases_route_when_clearly_outside():
    # 5526 > 5500 + 25 → not banded_cool any more. 5526 not in [2700, 3500].
    # No banded match → fallback.
    assert select_route(_routes(), colortemp=5526, current_index=0) == 2


def test_no_hysteresis_when_entering_new_banded_route():
    # current = fallback. Entering banded_cool requires strict containment.
    # 5520 is outside [4500, 5500] without hysteresis grace for entry.
    assert select_route(_routes(), colortemp=5520, current_index=2) == 2


def test_fallback_only_when_one_route():
    # Degenerate case used by the validator-disallowed config, but the
    # selector should not crash.
    routes = [CircadianKelvinRouteConfig(lights=["light.x"])]
    assert select_route(routes, colortemp=5000, current_index=None) == 0
