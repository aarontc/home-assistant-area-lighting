"""The scene machine's holiday roles in split and unsplit areas.

A split area defines christmas_ambient, which takes the ambient role. In an
unsplit area christmas plays both roles. ACTIVATE_HOLIDAY_SCENE places the
task role and ACTIVATE_HOLIDAY_AMBIENT_SCENE the ambient role; the
controller resolves the second with AreaConfig.holiday_ambient_slug.
"""

from __future__ import annotations

import pytest

from custom_components.area_lighting.area_state import ActivationSource, AreaState
from custom_components.area_lighting.scene_machine import (
    ActionType,
    determine_favorite_action,
    determine_off_action,
    determine_off_fade_action,
    determine_on_action,
    determine_on_sequence_action,
)

UNSPLIT = {"ambient", "christmas", "circadian", "daylight", "evening", "night"}
SPLIT = UNSPLIT | {"christmas_ambient"}
BOTH = pytest.mark.parametrize("scenes", [UNSPLIT, SPLIT], ids=["unsplit", "split"])


def _on(current, scenes, **kwargs):
    return determine_on_action(
        current_scene=current,
        scene_slugs=scenes,
        dimmed=kwargs.get("dimmed", False),
        triggered_by_motion=kwargs.get("motion", False),
        motion_override_ambient=kwargs.get("override", True),
        holiday_mode=kwargs.get("holiday", "christmas"),
        night_mode=False,
    )


def _off(current, *, split, source="user", ambience=True, holiday="christmas", **kwargs):
    split_ambient = f"{holiday}_ambient"
    return determine_off_action(
        current_scene=current,
        source=source,
        ambient_zone_enabled=ambience,
        area_ambience_enabled=ambience,
        holiday_mode=holiday,
        ambient_scene_mode=kwargs.get("ambient_scene", "holiday"),
        has_ambient_scene=kwargs.get("has_ambient", True),
        holiday_ambient_slug=split_ambient if split and holiday != "none" else holiday,
    )


# ── Roles table ─────────────────────────────────────────────────────────


@BOTH
def test_motion_from_off_places_the_task_role(scenes):
    assert _on("off", scenes, motion=True).action == ActionType.ACTIVATE_HOLIDAY_SCENE


@BOTH
@pytest.mark.parametrize("current", ["off", "manual"])
def test_on_button_from_off_or_manual_places_the_task_role(scenes, current):
    assert _on(current, scenes).action == ActionType.ACTIVATE_HOLIDAY_SCENE


@BOTH
def test_favorite_places_the_task_role(scenes):
    a = determine_favorite_action("daylight", scenes, "christmas")
    assert a.action == ActionType.ACTIVATE_HOLIDAY_SCENE


@BOTH
def test_on_button_from_christmas_goes_to_circadian(scenes):
    a = _on("christmas", scenes)
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "circadian"


def test_on_button_from_christmas_ambient_places_the_task_role():
    assert _on("christmas_ambient", SPLIT).action == ActionType.ACTIVATE_HOLIDAY_SCENE


def test_on_sequence_from_christmas_ambient_places_the_task_role():
    a = determine_on_sequence_action("christmas_ambient", SPLIT, "christmas", ["evening"])
    assert a.action == ActionType.ACTIVATE_HOLIDAY_SCENE


def test_favorite_from_christmas_ambient_places_the_task_role():
    a = determine_favorite_action("christmas_ambient", SPLIT, "christmas")
    assert a.action == ActionType.ACTIVATE_HOLIDAY_SCENE


@pytest.mark.parametrize("split", [False, True], ids=["unsplit", "split"])
@pytest.mark.parametrize("decide", [determine_off_action, determine_off_fade_action])
def test_off_with_holiday_ambience_places_the_ambient_role(split, decide):
    a = decide(
        current_scene="daylight",
        source="user",
        ambient_zone_enabled=True,
        area_ambience_enabled=True,
        holiday_mode="christmas",
        ambient_scene_mode="holiday",
        has_ambient_scene=True,
        holiday_ambient_slug="christmas_ambient" if split else "christmas",
    )
    assert a.action == ActionType.ACTIVATE_HOLIDAY_AMBIENT_SCENE


# ── Motion ──────────────────────────────────────────────────────────────


def test_motion_in_christmas_ambient_steps_up_to_the_task_role():
    a = _on("christmas_ambient", SPLIT, motion=True, override=True)
    assert a.action == ActionType.ACTIVATE_HOLIDAY_SCENE


def test_motion_in_christmas_ambient_without_override_does_nothing():
    a = _on("christmas_ambient", SPLIT, motion=True, override=False)
    assert a.action == ActionType.NOOP


@BOTH
def test_motion_in_christmas_does_nothing(scenes):
    assert _on("christmas", scenes, motion=True, override=True).action == ActionType.NOOP


# ── Off rules ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("current", "split"),
    [
        ("ambient", False),
        ("christmas", False),
        ("christmas", True),
        ("christmas_ambient", True),
    ],
)
@pytest.mark.parametrize("ambience", [True, False])
def test_rule_1_ambience_owned_scenes_turn_off(current, split, ambience):
    a = _off(current, split=split, source="ambience", ambience=ambience)
    assert a.scene_slug == "off_internal"


def test_rule_2_1_unsplit_christmas_falls_back_to_ambient():
    a = _off("christmas", split=False)
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "ambient"


def test_rule_2_1_unsplit_christmas_without_ambient_turns_off():
    assert _off("christmas", split=False, has_ambient=False).scene_slug == "off_internal"


def test_rule_2_1_holds_for_another_unsplit_holiday():
    """An unsplit halloween left over from earlier is not relit as christmas."""
    assert _off("halloween", split=True).scene_slug == "ambient"


def test_rule_2_2_a_persons_christmas_ambient_turns_off():
    assert _off("christmas_ambient", split=True).scene_slug == "off_internal"


def test_rule_2_3_split_christmas_goes_to_the_ambient_role():
    a = _off("christmas", split=True)
    assert a.action == ActionType.ACTIVATE_HOLIDAY_AMBIENT_SCENE


def test_rule_2_3_split_christmas_goes_to_ambient_without_holiday_ambience():
    a = _off("christmas", split=True, ambient_scene="ambient")
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "ambient"


def test_rule_2_3_split_christmas_goes_to_ambient_with_no_holiday():
    a = _off("christmas", split=True, holiday="none")
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "ambient"


@pytest.mark.parametrize("current", ["christmas", "christmas_ambient", "evening"])
def test_rule_3_without_ambience_turns_off(current):
    assert _off(current, split=True, ambience=False).scene_slug == "off_internal"


# ── Fallback scene ──────────────────────────────────────────────────────


def test_fallback_never_picks_a_holiday_slug():
    scenes = {"christmas", "christmas_ambient", "halloween", "halloween_ambient", "work"}
    a = _on("night", scenes)
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "work"


def test_fallback_with_only_holiday_slugs_does_nothing():
    assert _on("night", {"christmas", "christmas_ambient"}).action == ActionType.NOOP


# ── Ambience ownership ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "slug", ["ambient", "christmas", "halloween", "christmas_ambient", "halloween_ambient"]
)
def test_was_ambient_activated_covers_every_holiday_role(slug):
    s = AreaState()
    s.transition_to_scene(slug, ActivationSource.AMBIENCE)
    assert s.was_ambient_activated is True


def test_was_ambient_activated_needs_an_ambient_or_holiday_slug():
    s = AreaState()
    s.transition_to_scene("evening", ActivationSource.AMBIENCE)
    assert s.was_ambient_activated is False
