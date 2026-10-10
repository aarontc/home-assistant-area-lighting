"""Schema validation tests for circadian group bounds and sleep options."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting.config_schema import AREA_SCHEMA, parse_config
from custom_components.area_lighting.models import CircadianSwitchConfig


def _parse_group(**options) -> CircadianSwitchConfig:
    area = AREA_SCHEMA(
        {
            "id": "stairs",
            "name": "Stairs",
            "circadian_switches": [{"name": "Main", **options}],
        }
    )
    return parse_config({"areas": [area]}).areas[0].circadian_switches[0]


def test_bounds_default_to_1_and_100():
    group = _parse_group()
    assert group.min_brightness == 1
    assert group.max_brightness == 100


def test_sleep_options_parse():
    group = _parse_group(
        min_brightness=10,
        max_brightness=55,
        sleep_entity="select.theater_last_scene",
        sleep_state=["movie", "intermission"],
        sleep_brightness=5,
        sleep_colortemp=2700,
    )
    assert group.min_brightness == 10
    assert group.max_brightness == 55
    assert group.sleep_entity == "select.theater_last_scene"
    assert group.sleep_state == ["movie", "intermission"]
    assert group.sleep_brightness == 5
    assert group.sleep_colortemp == 2700


def test_sleep_entity_requires_sleep_state():
    with pytest.raises(vol.Invalid, match="sleep_state is required when sleep_entity is set"):
        _parse_group(sleep_entity="select.theater_last_scene")


def test_sleep_defaults_match_circadian_lighting():
    group = _parse_group()
    assert group.sleep_entity is None
    assert group.sleep_state == []
    assert group.sleep_brightness == 1
    assert group.sleep_colortemp == 1000


def test_sleep_state_accepts_a_single_string():
    group = _parse_group(sleep_entity="select.theater_last_scene", sleep_state="movie")
    assert group.sleep_state == ["movie"]
