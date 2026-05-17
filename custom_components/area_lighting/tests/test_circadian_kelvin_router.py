"""Unit tests for circadian-kelvin route data models and selection logic."""

from __future__ import annotations

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
