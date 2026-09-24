"""Unit tests for scene reference validation.

A light's `scenes` list and `linked_motion` mappings name scenes by id. A
reference to an undeclared scene used to be ignored at runtime, silently
leaving a light out of a scene or falling back to a default, so it now
fails validation. So does an unquoted YAML boolean such as `off`, which
used to become the scene id "False".
"""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting.config_schema import (
    AREA_SCHEMA,
    LIGHT_SCHEMA,
    LINKED_MOTION_ENTRY_SCHEMA,
    LUTRON_BUTTON_OVERRIDES_SCHEMA,
    SCENE_SCHEMA,
    parse_config,
    validate_scene_references,
)


def _area(area_id: str, *, scenes: tuple[str, ...] = ("circadian", "night"), **extra) -> dict:
    declared = [{"id": slug, "name": slug.title()} for slug in scenes]
    return AREA_SCHEMA({"id": area_id, "name": area_id.title(), "scenes": declared, **extra})


def _link(**overrides) -> dict:
    link = {
        "remote_area": "hall",
        "default": {"local_scene": "night", "remote_scene": "night"},
        "when_remote_scene": {"night": {"local_scene": "circadian"}},
    }
    link.update(overrides)
    return link


def _validate(*areas: dict) -> None:
    validate_scene_references(parse_config({"areas": list(areas)}))


def test_declared_and_behavioral_scene_references_pass():
    light = {"id": "light.den_a", "scenes": ["night", "off", "circadian"]}
    _validate(_area("den", lights=[light], linked_motion=[_link()]), _area("hall"))


def test_light_listing_an_undeclared_scene_rejected():
    light = {"id": "light.den_a", "scenes": ["evening"]}
    with pytest.raises(vol.Invalid, match=r"light 'light\.den_a' lists scene 'evening'"):
        _validate(_area("den", lights=[light]))


def test_light_cluster_listing_an_undeclared_scene_rejected():
    cluster = {"id": "light.den_zone", "members": ["light.den_a"], "scenes": ["evening"]}
    with pytest.raises(vol.Invalid, match=r"light 'light\.den_zone' lists scene 'evening'"):
        _validate(_area("den", light_clusters=[cluster]))


@pytest.mark.parametrize(
    ("link", "message"),
    [
        (
            _link(default={"local_scene": "evening"}),
            "local_scene 'evening' is not a scene of this area",
        ),
        (
            _link(default={"local_scene": "night", "remote_scene": "evening"}),
            "remote_scene 'evening' is not a scene of area 'hall'",
        ),
        (
            _link(when_remote_scene={"evening": {"local_scene": "night"}}),
            "when_remote_scene key 'evening' is not a scene of area 'hall'",
        ),
        (
            _link(when_remote_scene={"night": {"local_scene": "evening"}}),
            "local_scene 'evening' is not a scene of this area",
        ),
    ],
)
def test_linked_motion_referencing_an_undeclared_scene_rejected(link, message):
    with pytest.raises(vol.Invalid, match=message):
        _validate(_area("den", linked_motion=[link]), _area("hall"))


def test_manual_is_a_remote_condition_but_not_a_target():
    """A remote area in manual reports `manual` as its scene."""
    manual_key = _link(when_remote_scene={"manual": {"local_scene": "night"}})
    _validate(_area("den", linked_motion=[manual_key]), _area("hall"))

    manual_target = _link(default={"local_scene": "manual"})
    with pytest.raises(vol.Invalid, match="local_scene 'manual'"):
        _validate(_area("den", linked_motion=[manual_target]), _area("hall"))


def test_every_holiday_is_reachable_once_one_is_declared():
    """Holiday handling activates the active holiday in any area that
    declares a holiday scene, even one it does not declare."""
    light = {"id": "light.den_a", "scenes": ["halloween"]}
    _validate(_area("den", scenes=("circadian", "christmas"), lights=[light]))

    with pytest.raises(vol.Invalid, match="lists scene 'halloween'"):
        _validate(_area("den", lights=[light]))


def test_unknown_remote_area_still_checks_local_scenes():
    """An unknown remote_area only disables the link, but a bad local scene
    is still an error."""
    ok = _link(remote_area="nowhere", default={"local_scene": "night", "remote_scene": "any"})
    _validate(_area("den", linked_motion=[ok]))

    bad = _link(remote_area="nowhere", default={"local_scene": "evening"})
    with pytest.raises(vol.Invalid, match="local_scene 'evening'"):
        _validate(_area("den", linked_motion=[bad]))


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
