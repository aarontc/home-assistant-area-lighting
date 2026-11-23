"""Schema and semantic validation of `circadian: true` scene stanzas."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting.config_schema import (
    AREA_SCHEMA,
    SCENE_SCHEMA,
    parse_config,
    validate_circadian_groups,
    validate_scene_circadian_lights,
)

LIGHT = "light.kitchen_overhead"


def _scene(target: dict, entity_id: str = LIGHT) -> dict:
    return {"id": "christmas", "name": "Christmas", "entities": {entity_id: target}}


def _area(target: dict, entity_id: str = LIGHT, **extra) -> dict:
    return {
        "id": "kitchen",
        "name": "Kitchen",
        "circadian_groups": [{"name": "Main"}],
        "lights": [
            {"id": LIGHT, "circadian_group": "Main", "circadian_type": "ct"},
            {"id": "light.kitchen_cabinet"},
        ],
        "scenes": [_scene(target, entity_id)],
        **extra,
    }


def _validate(area: dict) -> None:
    config = parse_config({"areas": [AREA_SCHEMA(area)]})
    validate_circadian_groups(config)
    validate_scene_circadian_lights(config)


def test_circadian_fills_state_on():
    scene = SCENE_SCHEMA(_scene({"circadian": True}))
    assert scene["entities"][LIGHT] == {"circadian": True, "state": "on"}


def test_circadian_accepts_explicit_state_on():
    scene = SCENE_SCHEMA(_scene({"circadian": True, "state": "on"}))
    assert scene["entities"][LIGHT] == {"circadian": True, "state": "on"}


def test_circadian_schema_can_run_twice():
    area = _area({"circadian": True})
    assert AREA_SCHEMA(AREA_SCHEMA(area)) == AREA_SCHEMA(area)


def test_circadian_false_is_rejected():
    with pytest.raises(
        vol.Invalid, match=r"area 'kitchen', scene 'christmas'.*circadian: false.*delete the key"
    ):
        AREA_SCHEMA(_area({"circadian": False, "state": "on"}))


def test_circadian_non_boolean_is_rejected():
    with pytest.raises(vol.Invalid, match=r"area 'kitchen', scene 'christmas'.*circadian"):
        AREA_SCHEMA(_area({"circadian": "maybe"}))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("brightness", 128),
        ("effect", "colorloop"),
        ("color_temp", 300),
        ("color_temp_kelvin", 3000),
        ("hs_color", [0, 100]),
        ("rgb_color", [255, 0, 0]),
        ("rgbw_color", [255, 0, 0, 0]),
        ("rgbww_color", [255, 0, 0, 0, 0]),
        ("xy_color", [0.6, 0.3]),
        ("animate", False),
    ],
)
def test_circadian_rejects_companion_key(key, value):
    with pytest.raises(
        vol.Invalid,
        match=rf"area 'kitchen', scene 'christmas': entity '{LIGHT}'.*circadian.*{key}",
    ):
        AREA_SCHEMA(_area({"circadian": True, key: value}))


def test_circadian_rejects_state_off():
    with pytest.raises(
        vol.Invalid, match=r"area 'kitchen', scene 'christmas'.*circadian.*state: off"
    ):
        AREA_SCHEMA(_area({"circadian": True, "state": "off"}))


def test_valid_circadian_stanza_passes_semantic_validation():
    _validate(_area({"circadian": True}))


def test_circadian_on_an_undeclared_light_is_rejected():
    with pytest.raises(
        vol.Invalid,
        match=r"area 'kitchen', scene 'christmas': entity 'light.elsewhere'.*not declared",
    ):
        _validate(_area({"circadian": True}, entity_id="light.elsewhere"))


def test_circadian_on_a_light_without_a_group_is_rejected():
    with pytest.raises(
        vol.Invalid,
        match=r"entity 'light.kitchen_cabinet'.*circadian_group",
    ):
        _validate(_area({"circadian": True}, entity_id="light.kitchen_cabinet"))


def test_circadian_on_a_cluster_is_rejected():
    cluster = {
        "id": "light.kitchen_zone",
        "circadian_group": "Main",
        "circadian_type": "ct",
        "members": [LIGHT],
    }
    with pytest.raises(vol.Invalid, match=r"entity 'light.kitchen_zone'.*light_clusters"):
        _validate(
            _area({"circadian": True}, entity_id="light.kitchen_zone", light_clusters=[cluster])
        )


def test_circadian_on_a_kelvin_route_light_is_rejected():
    routes = {
        "routes": [
            {"lights": [LIGHT]},
            {"kelvin_range": [2000, 3000], "lights": ["light.kitchen_cabinet"]},
        ]
    }
    with pytest.raises(vol.Invalid, match=rf"entity '{LIGHT}'.*circadian_kelvin_routes"):
        _validate(_area({"circadian": True}, circadian_kelvin_routes=routes))


def test_circadian_on_a_member_of_a_routed_cluster_is_rejected():
    cluster = {"id": "light.kitchen_zone", "members": [LIGHT]}
    routes = {
        "routes": [
            {"lights": ["light.kitchen_zone"]},
            {"kelvin_range": [2000, 3000], "lights": ["light.kitchen_cabinet"]},
        ]
    }
    with pytest.raises(vol.Invalid, match=rf"entity '{LIGHT}'.*circadian_kelvin_routes"):
        _validate(
            _area(
                {"circadian": True},
                light_clusters=[cluster],
                circadian_kelvin_routes=routes,
            )
        )


def test_static_stanzas_are_not_checked():
    _validate(_area({"state": "on", "brightness": 10}, entity_id="light.elsewhere"))


def test_circadian_member_of_a_cluster_with_its_own_stanza_is_rejected():
    cluster = {"id": "light.kitchen_zone", "members": [LIGHT, "light.kitchen_cabinet"]}
    area = _area({"circadian": True}, light_clusters=[cluster])
    area["scenes"][0]["entities"]["light.kitchen_zone"] = {"state": "off"}
    with pytest.raises(vol.Invalid, match=rf"entity '{LIGHT}'.*light.kitchen_zone"):
        _validate(area)
