"""Schema validation tests for circadian_kelvin_routes."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting.config_schema import AREA_SCHEMA, parse_config


def _minimum_area_dict(**overrides):
    base = {
        "id": "kitchen",
        "name": "Kitchen",
        "circadian_switches": [{"name": "Kitchen"}],
        "lights": [
            {"id": "light.kitchen_fluorescent", "circadian_switch": "Kitchen"},
            {"id": "light.kitchen_strip_1", "circadian_switch": "Kitchen"},
            {"id": "light.kitchen_strip_2", "circadian_switch": "Kitchen"},
        ],
        "scenes": [{"id": "circadian", "name": "Circadian"}],
    }
    base.update(overrides)
    return base


def test_minimum_valid_routes_parses():
    area = _minimum_area_dict(
        circadian_kelvin_routes={
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1", "light.kitchen_strip_2"]},
            ]
        }
    )
    validated = AREA_SCHEMA(area)
    config = parse_config({"areas": [validated]})
    routes = config.areas[0].circadian_kelvin_routes
    assert routes is not None
    assert len(routes.routes) == 2
    assert routes.routes[0].kelvin_range == (4500, 5500)
    assert routes.routes[0].lights == ["light.kitchen_fluorescent"]
    assert routes.routes[1].kelvin_range is None
    assert routes.routes[1].lights == [
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
    ]


def test_explicit_source_and_crossfade_round_trip():
    area = _minimum_area_dict(
        circadian_kelvin_routes={
            "source": "sensor.circadian_values",
            "crossfade_seconds": 5.0,
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ],
        }
    )
    validated = AREA_SCHEMA(area)
    config = parse_config({"areas": [validated]})
    routes = config.areas[0].circadian_kelvin_routes
    assert routes.source == "sensor.circadian_values"
    assert routes.crossfade_seconds == 5.0


def test_omitting_circadian_kelvin_routes_yields_none():
    area = _minimum_area_dict()
    validated = AREA_SCHEMA(area)
    config = parse_config({"areas": [validated]})
    assert config.areas[0].circadian_kelvin_routes is None


def test_route_with_kelvin_range_below_minimum_rejected():
    area = _minimum_area_dict(
        circadian_kelvin_routes={
            "routes": [
                {
                    "kelvin_range": [999, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ]
        }
    )
    with pytest.raises(vol.Invalid):
        AREA_SCHEMA(area)


def test_route_with_negative_crossfade_rejected():
    area = _minimum_area_dict(
        circadian_kelvin_routes={
            "crossfade_seconds": -1.0,
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ],
        }
    )
    with pytest.raises(vol.Invalid):
        AREA_SCHEMA(area)
