"""Schema validation tests for circadian group bounds and sleep options."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting.config_schema import (
    AREA_SCHEMA,
    parse_config,
    validate_circadian_groups,
)
from custom_components.area_lighting.models import CircadianGroupConfig


def _parse_group(**options) -> CircadianGroupConfig:
    area = AREA_SCHEMA(
        {
            "id": "stairs",
            "name": "Stairs",
            "circadian_groups": [{"name": "Main", **options}],
        }
    )
    return parse_config({"areas": [area]}).areas[0].circadian_groups[0]


def _validate_area(groups: list[dict], lights: list[dict]) -> None:
    area = AREA_SCHEMA(
        {"id": "stairs", "name": "Stairs", "circadian_groups": groups, "lights": lights}
    )
    validate_circadian_groups(parse_config({"areas": [area]}))


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


def test_valid_groups_pass_validation():
    _validate_area(
        [{"name": "Main", "min_brightness": 10, "max_brightness": 10}],
        [
            {"id": "light.stairs", "circadian_group": "Main", "circadian_type": "ct"},
            {"id": "light.stairs_plain"},
        ],
    )


def test_circadian_switches_key_is_rejected_with_rename_hint():
    with pytest.raises(
        vol.Invalid, match=r"circadian_switches was renamed to circadian_groups in 3\.0\.0"
    ):
        AREA_SCHEMA({"id": "stairs", "name": "Stairs", "circadian_switches": [{"name": "Main"}]})


def test_circadian_switch_light_key_is_rejected_with_rename_hint():
    with pytest.raises(
        vol.Invalid, match=r"circadian_switch was renamed to circadian_group in 3\.0\.0"
    ):
        AREA_SCHEMA(
            {
                "id": "stairs",
                "name": "Stairs",
                "lights": [{"id": "light.stairs", "circadian_switch": "Main"}],
            }
        )


def test_routes_source_is_rejected_with_removal_hint():
    with pytest.raises(
        vol.Invalid, match=r"circadian_kelvin_routes\.source was removed in 3\.0\.0"
    ):
        AREA_SCHEMA(
            {
                "id": "stairs",
                "name": "Stairs",
                "lights": [{"id": "light.a"}, {"id": "light.b"}],
                "circadian_kelvin_routes": {
                    "source": "sensor.circadian_values",
                    "routes": [
                        {"kelvin_range": [4000, 6000], "lights": ["light.a"]},
                        {"lights": ["light.b"]},
                    ],
                },
            }
        )


def test_unknown_group_is_rejected():
    with pytest.raises(vol.Invalid, match="circadian_group 'Upper' is not declared"):
        _validate_area(
            [{"name": "Main"}],
            [{"id": "light.stairs", "circadian_group": "Upper", "circadian_type": "ct"}],
        )


def test_type_without_group_is_rejected():
    with pytest.raises(vol.Invalid, match="circadian_type is set without circadian_group"):
        _validate_area([{"name": "Main"}], [{"id": "light.stairs", "circadian_type": "ct"}])


def test_group_without_type_is_rejected():
    with pytest.raises(vol.Invalid, match="circadian_group is set without circadian_type"):
        _validate_area([{"name": "Main"}], [{"id": "light.stairs", "circadian_group": "Main"}])


def test_duplicate_group_name_is_rejected():
    with pytest.raises(vol.Invalid, match="circadian group 'Main' is declared more than once"):
        _validate_area([{"name": "Main"}, {"name": "Main"}], [])


def test_min_brightness_above_max_is_rejected():
    with pytest.raises(
        vol.Invalid, match=r"min_brightness \(60\) must not be above max_brightness \(50\)"
    ):
        _validate_area([{"name": "Main", "min_brightness": 60, "max_brightness": 50}], [])
