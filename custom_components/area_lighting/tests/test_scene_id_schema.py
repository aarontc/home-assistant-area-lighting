"""Tests for scene ids and boolean-looking YAML strings."""

import pytest
import voluptuous as vol
from homeassistant.core import valid_entity_id
from homeassistant.util.yaml import parse_yaml

from custom_components.area_lighting.config_schema import AREA_SCHEMA, SCENE_SCHEMA


@pytest.mark.parametrize(
    ("scene_id", "suggestion"),
    [
        ("Movie Night", "movie_night"),
        ("movie-night", "movie_night"),
        ("night_", "night"),
        ("night__", "night"),
        ("___", "unknown"),
        ("éclairage", "eclairage"),
        ("night.scene", "night_scene"),
        ("night\n", "night"),
        ("_night", "night"),
        ("night__2", "night_2"),
        ("", "scene"),
    ],
)
def test_invalid_scene_id_suggests_valid_id(scene_id, suggestion):
    with pytest.raises(vol.Invalid, match=f"(?s)scene id .*lowercase.*use '{suggestion}'"):
        SCENE_SCHEMA({"id": scene_id, "name": "Test scene"})
    SCENE_SCHEMA({"id": suggestion, "name": "Test scene"})


@pytest.mark.parametrize("scene_id", ["off", "night", "1", "night_2"])
def test_valid_scene_ids_produce_valid_entity_ids(scene_id):
    assert SCENE_SCHEMA({"id": scene_id, "name": "Test scene"})["id"] == scene_id
    assert valid_entity_id(f"scene.den_{scene_id}")


@pytest.mark.parametrize("value", ["off", "on", "yes", "no", "true", "false", "Off"])
@pytest.mark.parametrize(
    ("schema", "field"), [(AREA_SCHEMA, "id"), (SCENE_SCHEMA, "id"), (SCENE_SCHEMA, "name")]
)
def test_boolean_yaml_values_require_quotes(schema, field, value):
    other_field = "name: Night" if field == "id" else "id: night"
    raw = parse_yaml(f"{other_field}\n{field}: {value}\n")
    assert isinstance(raw[field], bool)
    with pytest.raises(vol.Invalid, match=r'must be quoted.*id: "off"'):
        schema(raw)

    raw = parse_yaml(f'{other_field}\n{field}: "{value}"\n')
    if field == "id" and value == "Off":
        with pytest.raises(vol.Invalid, match="lowercase"):
            schema(raw)
    else:
        assert schema(raw)[field] == value
