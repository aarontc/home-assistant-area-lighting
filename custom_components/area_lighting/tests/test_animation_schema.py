"""Scene animation configuration validation and parsing."""

import pytest
import voluptuous as vol

from custom_components.area_lighting import CONFIG_SCHEMA
from custom_components.area_lighting.config_schema import (
    ANIMATIONS_SCHEMA,
    AREA_SCHEMA,
    SCENE_SCHEMA,
    parse_config,
    validate_scene_animations,
)


def _animation(**overrides):
    return {"period": 30, "palette": [{"hs_color": [0, 100]}], **overrides}


def _scene(**overrides):
    return {
        "id": "christmas",
        "name": "Christmas",
        "animations": ["drift"],
        "entities": {"light.bulb": {"state": "on", "hs_color": [0, 100]}},
        **overrides,
    }


def test_top_level_schema_and_parser_keep_animation_configuration():
    raw = CONFIG_SCHEMA(
        {
            "area_lighting": {
                "animations": {"drift": _animation()},
                "areas": [{"id": "kitchen", "name": "Kitchen", "scenes": [_scene()]}],
            }
        }
    )["area_lighting"]
    config = parse_config(raw)
    validate_scene_animations(config)
    assert config.animations["drift"].period == 30
    assert config.animations["drift"].transition == 30
    assert config.animations["drift"].palette == [{"hs_color": [0.0, 100.0], "weight": 1}]
    assert config.areas[0].scenes[0].animations == ["drift"]


def test_parser_defaults_without_animation_configuration():
    config = parse_config(
        {
            "areas": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "scenes": [{"id": "evening", "name": "Evening"}],
                }
            ]
        }
    )
    assert config.animations == {}
    assert config.areas[0].scenes[0].animations == []


@pytest.mark.parametrize(
    "name",
    ["off", "UPPER", "double__under", "_leading", "trailing_", "has space", "hyphen-name", False],
)
def test_invalid_animation_names_rejected(name):
    with pytest.raises(vol.Invalid):
        ANIMATIONS_SCHEMA({name: _animation()})


@pytest.mark.parametrize("name", ["red", "christmas_2", "2_party"])
def test_valid_animation_names_accepted(name):
    assert name in ANIMATIONS_SCHEMA({name: _animation()})


@pytest.mark.parametrize(
    "change",
    [
        {"period": 0.99},
        {"period": -1},
        {"period": True},
        {"period": "30"},
        {"period": float("inf")},
        {"period": float("nan")},
        {"transition": -1},
        {"transition": 31},
        {"transition": False},
        {"transition": float("nan")},
        {"palette": []},
        {"palette": {}},
        {"palette": [{"hs_color": [0, 100], "extra": 1}]},
        {"unexpected": 1},
    ],
)
def test_invalid_animation_fields_rejected(change):
    with pytest.raises(vol.Invalid):
        ANIMATIONS_SCHEMA({"drift": _animation(**change)})


@pytest.mark.parametrize("missing", ["period", "palette"])
def test_required_animation_fields(missing):
    animation = _animation()
    del animation[missing]
    with pytest.raises(vol.Invalid, match=missing):
        ANIMATIONS_SCHEMA({"drift": animation})


@pytest.mark.parametrize("transition", [0, 0.5, 30])
def test_transition_bounds_and_fractional_seconds_accepted(transition):
    assert (
        ANIMATIONS_SCHEMA({"drift": _animation(transition=transition)})["drift"]["transition"]
        == transition
    )
    assert ANIMATIONS_SCHEMA({"drift": _animation(period=1.5)})["drift"]["transition"] == 1.5


@pytest.mark.parametrize(
    "entry",
    [
        {"hs_color": [360, 0]},
        {"rgb_color": [0, 128, 255]},
        {"xy_color": [0.1, 0.8]},
        {"color_temp_kelvin": 5000, "brightness": 1, "weight": 2},
        {"color_temp_kelvin": 2700, "brightness": 255},
    ],
)
def test_palette_color_forms_accepted(entry):
    validated = ANIMATIONS_SCHEMA({"drift": _animation(palette=[entry])})
    assert validated["drift"]["palette"][0]["weight"] == entry.get("weight", 1)


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"brightness": 100},
        {"hs_color": [0, 100], "rgb_color": [255, 0, 0]},
        {"rgbw_color": [1, 2, 3, 4]},
        {"color_temp": 370},
        {"hs_color": [0]},
        {"hs_color": [361, 100]},
        {"hs_color": [0, 101]},
        {"rgb_color": [-1, 0, 0]},
        {"rgb_color": [256, 0, 0]},
        {"xy_color": [1.1, 0.2]},
        {"color_temp_kelvin": 0},
        {"hs_color": [0, 100], "brightness": 0},
        {"hs_color": [0, 100], "brightness": 256},
        {"hs_color": [0, 100], "brightness": 1.5},
        {"hs_color": [0, 100], "brightness": True},
        {"hs_color": [0, 100], "weight": 0},
        {"hs_color": [0, 100], "weight": -1},
        {"hs_color": [0, 100], "weight": 1.5},
        {"hs_color": [0, 100], "weight": True},
    ],
)
def test_invalid_palette_entries_rejected(entry):
    with pytest.raises(vol.Invalid):
        ANIMATIONS_SCHEMA({"drift": _animation(palette=[entry])})


@pytest.mark.parametrize(
    "scene",
    [
        _scene(animations="drift"),
        _scene(animations=["drift", "drift"]),
        _scene(animations=["UPPER"]),
        _scene(animations=["off"]),
        {"id": "christmas", "name": "Christmas", "animations": ["drift"]},
        _scene(entities=None),
        _scene(entities={"light.bulb": {"state": "on", "animate": "maybe"}}),
        _scene(animations=[], entities={"light.bulb": {"state": "on", "animate": False}}),
    ],
)
def test_invalid_scene_animation_options_name_area_scene_and_key(scene):
    with pytest.raises(vol.Invalid, match=r"area 'kitchen'.*scene 'christmas'.*animat"):
        AREA_SCHEMA({"id": "kitchen", "name": "Kitchen", "scenes": [scene]})


def test_animate_opt_out_accepted_only_with_animations():
    scene = SCENE_SCHEMA(_scene(entities={"light.bulb": {"state": "on", "animate": False}}))
    assert scene["entities"]["light.bulb"]["animate"] is False
    with pytest.raises(vol.Invalid, match="animate requires animations"):
        SCENE_SCHEMA(
            {"id": "evening", "name": "Evening", "entities": {"light.bulb": {"animate": False}}}
        )


def test_unknown_animation_reference_names_area_scene_and_key():
    config = parse_config({"areas": [{"id": "kitchen", "name": "Kitchen", "scenes": [_scene()]}]})
    with pytest.raises(
        vol.Invalid, match=r"area 'kitchen', scene 'christmas': animations.*'drift'"
    ):
        validate_scene_animations(config)


def test_empty_animation_list_stays_static_and_schema_can_run_twice():
    area = {
        "id": "kitchen",
        "name": "Kitchen",
        "scenes": [{"id": "evening", "name": "Evening"}, _scene(animations=[])],
    }
    assert AREA_SCHEMA(AREA_SCHEMA(area)) == AREA_SCHEMA(area)
