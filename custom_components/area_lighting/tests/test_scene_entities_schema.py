"""Unit tests for strict scene `entities` validation in SCENE_SCHEMA.

A per-light key that Area Lighting does not apply (e.g. `color_mode`, which is
read-only on a light and was silently dropped at apply time, or a typo) must
fail loudly at config validation / startup instead of failing silently later.
The allowed color/brightness keys come from SCENE_LIGHT_ON_ATTRIBUTES so the
schema allowlist and the apply paths share one source of truth.
"""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting.config_schema import SCENE_SCHEMA


def _scene(entities: dict) -> dict:
    return {"id": "christmas", "name": "Christmas", "entities": entities}


# ── Supported attributes pass ───────────────────────────────────────────


def test_scene_with_rgbw_color_passes():
    SCENE_SCHEMA(
        _scene(
            {
                "light.theater_screen": {
                    "state": "on",
                    "brightness": 255,
                    "rgbw_color": [255, 0, 0, 0],
                }
            }
        )
    )


def test_scene_with_rgbww_color_passes():
    SCENE_SCHEMA(
        _scene({"light.theater_center": {"state": "on", "rgbww_color": [0, 255, 0, 0, 0]}})
    )


def test_scene_with_hs_color_passes():
    SCENE_SCHEMA(
        _scene({"light.theater_center": {"state": "on", "brightness": 128, "hs_color": [0, 100]}})
    )


def test_scene_off_state_passes():
    SCENE_SCHEMA(_scene({"light.theater_center": {"state": "off"}}))


# ── Unsupported / mistyped keys fail at validation ──────────────────────


def test_scene_rejects_color_mode_key():
    """color_mode is read-only / not a turn_on arg — must error, not be dropped."""
    with pytest.raises(vol.Invalid):
        SCENE_SCHEMA(
            _scene(
                {
                    "light.theater_screen": {
                        "state": "on",
                        "color_mode": "rgbw",
                        "rgbw_color": [255, 0, 0, 0],
                    }
                }
            )
        )


def test_scene_rejects_unknown_attribute_typo():
    with pytest.raises(vol.Invalid):
        SCENE_SCHEMA(_scene({"light.a": {"state": "on", "rgwb_color": [255, 0, 0, 0]}}))


def test_scene_rejects_bad_state_value():
    with pytest.raises(vol.Invalid):
        SCENE_SCHEMA(_scene({"light.a": {"state": "ON"}}))


def test_scene_rejects_non_entity_id_key():
    with pytest.raises(vol.Invalid):
        SCENE_SCHEMA(_scene({"not_an_entity_id": {"state": "on"}}))
