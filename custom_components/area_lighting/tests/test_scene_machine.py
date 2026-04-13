"""Tests for the scene_machine pure cycling logic."""

from __future__ import annotations

from custom_components.area_lighting.scene_machine import (
    ActionType,
    determine_favorite_action,
    determine_off_action,
    determine_on_action,
    resolve_sun_position,
    resolve_sun_position_inverted,
)

# Standard network_room scene set
NR_SCENES = {"ambient", "christmas", "circadian", "daylight", "evening", "night"}


def _on(current, **kwargs):
    """Compact wrapper for determine_on_action with sensible defaults."""
    return determine_on_action(
        current_scene=current,
        scene_slugs=kwargs.get("scenes", NR_SCENES),
        dimmed=kwargs.get("dimmed", False),
        triggered_by_motion=kwargs.get("motion", False),
        motion_override_ambient=kwargs.get("override", False),
        holiday_mode=kwargs.get("holiday", "none"),
        night_mode=kwargs.get("night_mode", False),
    )


# ── lighting_on from OFF ─────────────────────────────────────────────────


def test_on_from_off_uses_circadian_default():
    a = _on("off")
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "circadian"


def test_on_from_off_with_holiday_mode_christmas():
    a = _on("off", holiday="christmas")
    assert a.action == ActionType.ACTIVATE_HOLIDAY_SCENE


def test_on_from_off_with_night_mode_picks_night():
    a = _on("off", night_mode=True)
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "night"


def test_on_from_off_holiday_takes_precedence_over_night():
    a = _on("off", holiday="christmas", night_mode=True)
    assert a.action == ActionType.ACTIVATE_HOLIDAY_SCENE


def test_on_from_off_with_holiday_but_area_lacks_holiday_falls_through():
    """Holiday mode set but area has no christmas scene → use circadian default."""
    scenes_no_holiday = {"daylight", "evening", "circadian"}
    a = _on("off", scenes=scenes_no_holiday, holiday="christmas")
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "circadian"


def test_on_from_manual_uses_default():
    a = _on("manual")
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "circadian"


# ── lighting_on from CIRCADIAN ───────────────────────────────────────────


def test_on_from_circadian_picks_inverted_sun_position():
    a = _on("circadian")
    assert a.action == ActionType.SET_SUN_POSITION_INVERTED


# ── lighting_on from daylight/evening toggle ─────────────────────────────


def test_on_from_daylight_goes_to_evening():
    a = _on("daylight")
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "evening"


def test_on_from_evening_goes_to_daylight():
    a = _on("evening")
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "daylight"


# ── lighting_on from NIGHT ───────────────────────────────────────────────


def test_on_from_night_cycles_out_to_circadian():
    a = _on("night")
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "circadian"


# ── lighting_on from a holiday scene ────────────────────────────────────


def test_on_from_christmas_cycles_to_circadian():
    a = _on("christmas")
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "circadian"


def test_on_from_halloween_cycles_to_circadian():
    a = _on("halloween")
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "circadian"


# ── Motion-triggered no-op behavior ─────────────────────────────────────


def test_motion_on_when_lights_already_on_is_noop():
    a = _on("daylight", motion=True)
    assert a.action == ActionType.NOOP


def test_motion_on_when_off_activates_normally():
    a = _on("off", motion=True)
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "circadian"


def test_motion_on_when_ambient_with_override_proceeds():
    """If motion override_ambient is on and current is ambient, motion can take over."""
    a = _on("ambient", motion=True, override=True)
    # Should NOT be a no-op (override allows it)
    assert a.action != ActionType.NOOP


def test_motion_on_when_ambient_without_override_is_noop():
    a = _on("ambient", motion=True, override=False)
    assert a.action == ActionType.NOOP


# ── Dimmed restore ──────────────────────────────────────────────────────


def test_on_when_dimmed_restores_current_scene():
    a = _on("evening", dimmed=True)
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "evening"


def test_on_when_dimmed_with_invalid_scene_falls_back_to_default():
    a = _on("zzznotreal", dimmed=True)
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "circadian"


# ── lighting_off ────────────────────────────────────────────────────────


def test_off_with_no_ambience_returns_off_internal():
    a = determine_off_action(
        current_scene="daylight",
        source="user",
        ambient_zone_enabled=False,
        area_ambience_enabled=False,
        holiday_mode="none",
        ambient_scene_mode="ambient",
    )
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "off_internal"


def test_off_with_zone_only_no_area_ambience_returns_off_internal():
    a = determine_off_action(
        current_scene="daylight",
        source="user",
        ambient_zone_enabled=True,
        area_ambience_enabled=False,
        holiday_mode="none",
        ambient_scene_mode="ambient",
    )
    assert a.scene_slug == "off_internal"


def test_off_with_both_ambience_flags_returns_ambient():
    a = determine_off_action(
        current_scene="daylight",
        source="user",
        ambient_zone_enabled=True,
        area_ambience_enabled=True,
        holiday_mode="none",
        ambient_scene_mode="ambient",
    )
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "ambient"


def test_off_already_in_ambient_returns_off_internal():
    """If we're already showing ambient, off should actually turn off (no infinite loop)."""
    a = determine_off_action(
        current_scene="ambient",
        source="user",
        ambient_zone_enabled=True,
        area_ambience_enabled=True,
        holiday_mode="none",
        ambient_scene_mode="ambient",
    )
    assert a.scene_slug == "off_internal"


def test_off_in_user_holiday_with_ambience_falls_back_to_ambient():
    """D7: user-owned holiday + ambience active → fall back to literal ambient."""
    a = determine_off_action(
        current_scene="christmas",
        source="user",
        ambient_zone_enabled=True,
        area_ambience_enabled=True,
        holiday_mode="christmas",
        ambient_scene_mode="ambient",
    )
    assert a.scene_slug == "ambient"


def test_off_with_holiday_ambient_mode_returns_holiday_scene():
    a = determine_off_action(
        current_scene="daylight",
        source="user",
        ambient_zone_enabled=True,
        area_ambience_enabled=True,
        holiday_mode="christmas",
        ambient_scene_mode="holiday",
    )
    assert a.action == ActionType.ACTIVATE_HOLIDAY_SCENE


# ── lighting_favorite ───────────────────────────────────────────────────


def test_favorite_no_holiday_picks_night():
    a = determine_favorite_action(
        current_scene="daylight",
        scene_slugs=NR_SCENES,
        holiday_mode="none",
    )
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "night"


def test_favorite_with_holiday_not_yet_holiday_activates_holiday():
    a = determine_favorite_action(
        current_scene="daylight",
        scene_slugs=NR_SCENES,
        holiday_mode="christmas",
    )
    assert a.action == ActionType.ACTIVATE_HOLIDAY_SCENE


def test_favorite_with_holiday_already_in_holiday_picks_night():
    a = determine_favorite_action(
        current_scene="christmas",
        scene_slugs=NR_SCENES,
        holiday_mode="christmas",
    )
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "night"


def test_favorite_with_holiday_in_other_holiday_picks_night():
    """Already in halloween, holiday mode is christmas - should pick night, not double-cycle."""
    a = determine_favorite_action(
        current_scene="halloween",
        scene_slugs=NR_SCENES,
        holiday_mode="christmas",
    )
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "night"


# ── Sun position resolution ────────────────────────────────────────────


def test_sun_position_daylight_enabled():
    assert resolve_sun_position(True) == "daylight"


def test_sun_position_daylight_disabled():
    assert resolve_sun_position(False) == "evening"


def test_sun_position_inverted():
    assert resolve_sun_position_inverted(True) == "evening"
    assert resolve_sun_position_inverted(False) == "daylight"


# ── Source-aware off behavior (D7) ──────────────────────────────────────


def test_off_from_user_christmas_with_ambience_falls_back_to_literal_ambient():
    """User-owned holiday scene + ambience active → literal ambient fallback."""
    a = determine_off_action(
        current_scene="christmas",
        source="user",
        ambient_zone_enabled=True,
        area_ambience_enabled=True,
        holiday_mode="christmas",
        ambient_scene_mode="ambient",
    )
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "ambient"


def test_off_from_ambience_christmas_turns_off_internal():
    """Ambience-owned holiday scene → off_internal (already ambience-owned)."""
    a = determine_off_action(
        current_scene="christmas",
        source="ambience",
        ambient_zone_enabled=True,
        area_ambience_enabled=True,
        holiday_mode="christmas",
        ambient_scene_mode="ambient",
    )
    assert a.action == ActionType.ACTIVATE_SCENE
    assert a.scene_slug == "off_internal"


def test_off_from_ambience_ambient_turns_off_internal():
    """Ambience-owned literal ambient → off_internal."""
    a = determine_off_action(
        current_scene="ambient",
        source="ambience",
        ambient_zone_enabled=True,
        area_ambience_enabled=True,
        holiday_mode="none",
        ambient_scene_mode="ambient",
    )
    assert a.scene_slug == "off_internal"


def test_off_from_user_ambient_with_ambience_still_off_internal():
    """User-owned literal ambient (rare) → off_internal (no useful fallback)."""
    a = determine_off_action(
        current_scene="ambient",
        source="user",
        ambient_zone_enabled=True,
        area_ambience_enabled=True,
        holiday_mode="none",
        ambient_scene_mode="ambient",
    )
    assert a.scene_slug == "off_internal"


def test_off_from_user_holiday_without_ambience_still_off_internal():
    """Holiday scene + ambience NOT active → off_internal (literal off)."""
    a = determine_off_action(
        current_scene="christmas",
        source="user",
        ambient_zone_enabled=False,
        area_ambience_enabled=False,
        holiday_mode="christmas",
        ambient_scene_mode="ambient",
    )
    assert a.scene_slug == "off_internal"
