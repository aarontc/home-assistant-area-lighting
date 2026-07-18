"""Unit tests for reserved area id validation in AREA_SCHEMA."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting.config_schema import AREA_SCHEMA
from custom_components.area_lighting.state_storage import GLOBAL_STATE_KEY


def _minimal_area(area_id: str) -> dict:
    """Return the minimal raw area dict that passes AREA_SCHEMA."""
    return {"id": area_id, "name": "Test Area"}


def test_normal_area_id_passes():
    AREA_SCHEMA(_minimal_area("media_room"))


def test_area_id_with_trailing_underscores_passes():
    AREA_SCHEMA(_minimal_area("media_room__"))


def test_global_state_key_rejected():
    with pytest.raises(vol.Invalid, match="reserved"):
        AREA_SCHEMA(_minimal_area(GLOBAL_STATE_KEY))


def test_double_underscore_prefix_rejected():
    with pytest.raises(vol.Invalid, match="reserved"):
        AREA_SCHEMA(_minimal_area("__internal"))
