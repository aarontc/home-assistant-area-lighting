"""Unit tests for area id validation in AREA_SCHEMA."""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.core import valid_entity_id

from custom_components.area_lighting.config_schema import AREA_SCHEMA
from custom_components.area_lighting.state_storage import GLOBAL_STATE_KEY


def _minimal_area(area_id: str) -> dict:
    """Return the minimal raw area dict that passes AREA_SCHEMA."""
    return {"id": area_id, "name": "Test Area"}


def test_normal_area_id_passes():
    AREA_SCHEMA(_minimal_area("media_room"))


def test_area_id_with_trailing_underscores_rejected():
    """switch.media_room___night_mode is not a valid entity id."""
    with pytest.raises(vol.Invalid, match="single underscores"):
        AREA_SCHEMA(_minimal_area("media_room__"))


def test_global_state_key_rejected():
    with pytest.raises(vol.Invalid, match="reserved"):
        AREA_SCHEMA(_minimal_area(GLOBAL_STATE_KEY))


def test_double_underscore_prefix_rejected():
    with pytest.raises(vol.Invalid, match="reserved"):
        AREA_SCHEMA(_minimal_area("__internal"))


@pytest.mark.parametrize(
    ("area_id", "collision"),
    [("global", "unique ids"), ("area_lighting", "entity ids")],
)
def test_global_switch_collision_rejected(area_id, collision):
    with pytest.raises(vol.Invalid, match=f"{collision} collide with global master switch"):
        AREA_SCHEMA(_minimal_area(area_id))


@pytest.mark.parametrize(
    ("area_id", "suggestion"),
    [
        ("Media Room", "media_room"),
        ("den-lights", "den_lights"),
        ("_den", "den"),
        ("_global", "global_area"),
        ("_area_lighting", "area_lighting_area"),
        ("éclairage", "eclairage"),
        ("den.room", "den_room"),
        ("den\n", "den"),
        ("den_", "den"),
        ("den__lights", "den_lights"),
        ("", "room"),
    ],
)
def test_invalid_area_id_suggests_valid_id(area_id, suggestion):
    with pytest.raises(vol.Invalid, match=f"(?s)area id .*lowercase.*use '{suggestion}'"):
        AREA_SCHEMA(_minimal_area(area_id))
    AREA_SCHEMA(_minimal_area(suggestion))


@pytest.mark.parametrize("area_id", ["den", "1", "1st_room", "media_room_2"])
def test_valid_area_ids_produce_valid_entity_ids(area_id):
    assert AREA_SCHEMA(_minimal_area(area_id))["id"] == area_id
    assert valid_entity_id(f"switch.{area_id}_night_mode")
