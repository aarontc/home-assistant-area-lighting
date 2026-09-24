"""Unit tests for scene references in config.

Scene references (a light's `scenes`, `cycle`, Lutron `favorite` lists and
`linked_motion` mappings) name scenes by id. An unquoted `off` is a YAML
boolean, which cv.string used to turn into the scene id "False", so every
reference now rejects booleans. Whether a reference names a declared scene
is not checked: the runtime can reach undeclared scenes (holiday and
ambient handling, favorites, cycling, externally defined scenes, and
skeleton activation from light membership), so an undeclared name is not
necessarily a mistake.
"""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting.config_schema import (
    LIGHT_SCHEMA,
    LINKED_MOTION_ENTRY_SCHEMA,
    LUTRON_BUTTON_OVERRIDES_SCHEMA,
    SCENE_SCHEMA,
)


def _link(**overrides) -> dict:
    link = {
        "remote_area": "hall",
        "default": {"local_scene": "night", "remote_scene": "night"},
        "when_remote_scene": {"night": {"local_scene": "circadian"}},
    }
    link.update(overrides)
    return link


def test_quoted_scene_references_pass():
    LIGHT_SCHEMA({"id": "light.den_a", "scenes": ["off", "night"]})
    LUTRON_BUTTON_OVERRIDES_SCHEMA({"favorite": ["night", "off"]})
    assert LINKED_MOTION_ENTRY_SCHEMA(_link(default={"local_scene": "off"}))["default"] == {
        "local_scene": "off"
    }


def test_null_remote_scene_still_accepted():
    link = LINKED_MOTION_ENTRY_SCHEMA(_link(default={"local_scene": "night", "remote_scene": None}))
    assert link["default"]["remote_scene"] is None


@pytest.mark.parametrize(
    ("schema", "value"),
    [
        (LIGHT_SCHEMA, {"id": "light.den_a", "scenes": [False]}),
        (SCENE_SCHEMA, {"id": "night", "name": "Night", "cycle": [False]}),
        (LUTRON_BUTTON_OVERRIDES_SCHEMA, {"favorite": False}),
        (LUTRON_BUTTON_OVERRIDES_SCHEMA, {"favorite": ["night", False]}),
        (LINKED_MOTION_ENTRY_SCHEMA, _link(default={"local_scene": False})),
        (
            LINKED_MOTION_ENTRY_SCHEMA,
            _link(default={"local_scene": "night", "remote_scene": False}),
        ),
        (LINKED_MOTION_ENTRY_SCHEMA, _link(when_remote_scene={False: {"local_scene": "night"}})),
    ],
)
def test_boolean_scene_references_rejected(schema, value):
    """`off` unquoted is YAML False; it must not become the scene "False"."""
    with pytest.raises(vol.Invalid):
        schema(value)
