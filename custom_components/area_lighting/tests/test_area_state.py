"""Tests for the AreaState pure state machine."""

from __future__ import annotations

from custom_components.area_lighting.area_state import (
    AMBIENT_LIKE_SCENES,
    ActivationSource,
    AreaState,
    LightingState,
)


# ── Initial state ────────────────────────────────────────────────────────


def test_default_state_is_off():
    s = AreaState()
    assert s.state == LightingState.OFF
    assert s.scene_slug == "off"
    assert s.dimmed is False
    assert s.previous_scene is None
    assert s.is_off is True
    assert s.is_on is False


# ── Transition: OFF → SCENE ──────────────────────────────────────────────


def test_transition_off_to_scene():
    s = AreaState()
    s.transition_to_scene("daylight", ActivationSource.USER)
    assert s.state == LightingState.SCENE
    assert s.scene_slug == "daylight"
    assert s.source == ActivationSource.USER
    assert s.is_on is True
    assert s.is_off is False
    assert s.dimmed is False


def test_transition_to_scene_clears_dimmed():
    s = AreaState()
    s.transition_to_scene("evening", ActivationSource.USER)
    s.mark_dimmed()
    assert s.dimmed is True
    s.transition_to_scene("daylight", ActivationSource.USER)
    assert s.dimmed is False
    assert s.previous_scene is None  # restore target was wiped


# ── Transition: SCENE → CIRCADIAN ────────────────────────────────────────


def test_transition_scene_to_circadian():
    s = AreaState()
    s.transition_to_scene("evening", ActivationSource.USER)
    s.transition_to_circadian(ActivationSource.USER)
    assert s.state == LightingState.CIRCADIAN
    assert s.scene_slug == "circadian"
    assert s.is_circadian is True


# ── Transition: ANY → OFF ────────────────────────────────────────────────


def test_transition_to_off_resets_everything():
    s = AreaState()
    s.transition_to_scene("christmas", ActivationSource.USER)
    s.mark_dimmed()
    s.transition_to_off(ActivationSource.MOTION)
    assert s.state == LightingState.OFF
    assert s.scene_slug == "off"
    assert s.dimmed is False
    assert s.previous_scene is None
    assert s.source == ActivationSource.MOTION


# ── Manual mode ──────────────────────────────────────────────────────────


def test_transition_to_manual():
    s = AreaState()
    s.transition_to_scene("daylight", ActivationSource.USER)
    s.transition_to_manual()
    assert s.state == LightingState.MANUAL
    assert s.scene_slug == "manual"
    assert s.source == ActivationSource.MANUAL
    assert s.is_manual is True


# ── Dimmed flow ──────────────────────────────────────────────────────────


def test_mark_dimmed_remembers_current_scene():
    s = AreaState()
    s.transition_to_scene("evening", ActivationSource.USER)
    s.mark_dimmed()
    assert s.dimmed is True
    assert s.previous_scene == "evening"


def test_mark_dimmed_idempotent_does_not_overwrite_previous():
    s = AreaState()
    s.transition_to_scene("evening", ActivationSource.USER)
    s.mark_dimmed()
    # User raises again - previous_scene should still be evening
    s.mark_dimmed()
    assert s.previous_scene == "evening"


def test_clear_dimmed_returns_previous_and_resets():
    s = AreaState()
    s.transition_to_scene("daylight", ActivationSource.USER)
    s.mark_dimmed()
    prev = s.clear_dimmed()
    assert prev == "daylight"
    assert s.dimmed is False
    assert s.previous_scene is None


def test_clear_dimmed_when_not_dimmed_returns_none():
    s = AreaState()
    s.transition_to_scene("daylight", ActivationSource.USER)
    assert s.clear_dimmed() is None


def test_dimming_then_transitioning_clears_previous():
    """Activating a new scene while dimmed should drop the dimmed restore target."""
    s = AreaState()
    s.transition_to_scene("evening", ActivationSource.USER)
    s.mark_dimmed()
    s.transition_to_scene("night", ActivationSource.USER)
    assert s.dimmed is False
    assert s.previous_scene is None


# ── Ambient/holiday detection ────────────────────────────────────────────


def test_is_ambient_like_for_ambient_christmas_halloween():
    assert AMBIENT_LIKE_SCENES == frozenset({"ambient", "christmas", "halloween"})

    s = AreaState()
    s.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    assert s.is_ambient_like is True

    s.transition_to_scene("christmas", ActivationSource.HOLIDAY)
    assert s.is_ambient_like is True

    s.transition_to_scene("halloween", ActivationSource.HOLIDAY)
    assert s.is_ambient_like is True

    s.transition_to_scene("daylight", ActivationSource.USER)
    assert s.is_ambient_like is False


def test_was_ambient_activated_requires_ambience_source():
    s = AreaState()
    # Christmas activated by user (e.g. remote favorite) → not ambient-activated
    s.transition_to_scene("christmas", ActivationSource.USER)
    assert s.was_ambient_activated is False

    # Christmas activated by ambience mode → ambient-activated
    s.transition_to_scene("christmas", ActivationSource.AMBIENCE)
    assert s.was_ambient_activated is True

    # Ambient by holiday flow → still not "ambient-activated" (different source)
    s.transition_to_scene("ambient", ActivationSource.HOLIDAY)
    assert s.was_ambient_activated is False


def test_was_motion_triggered():
    s = AreaState()
    s.transition_to_scene("circadian", ActivationSource.MOTION)
    assert s.was_motion_triggered is True

    s.transition_to_scene("circadian", ActivationSource.USER)
    assert s.was_motion_triggered is False


# ── Persistence round-trip ───────────────────────────────────────────────


def test_to_dict_from_dict_round_trip():
    s = AreaState()
    s.transition_to_scene("evening", ActivationSource.MOTION)
    s.mark_dimmed()
    data = s.to_dict()

    restored = AreaState.from_dict(data)
    assert restored.state == LightingState.SCENE
    assert restored.scene_slug == "evening"
    assert restored.source == ActivationSource.MOTION
    assert restored.dimmed is True
    assert restored.previous_scene == "evening"


def test_from_dict_with_empty_data_returns_default():
    s = AreaState.from_dict({})
    assert s.state == LightingState.OFF


def test_from_dict_with_invalid_enum_falls_back_to_default():
    s = AreaState.from_dict(
        {"state": "garbage", "scene_slug": "x", "source": "x", "dimmed": False}
    )
    assert s.state == LightingState.OFF


def test_from_dict_with_unknown_source_falls_back():
    s = AreaState.from_dict(
        {"state": "scene", "scene_slug": "evening",
         "source": "from_the_void", "dimmed": False}
    )
    assert s.state == LightingState.OFF


# ── Source preservation ─────────────────────────────────────────────────


def test_source_propagates_through_all_transitions():
    for src in ActivationSource:
        s = AreaState()
        if src == ActivationSource.MANUAL:
            # transition_to_manual hardcodes the source
            s.transition_to_manual()
            assert s.source == ActivationSource.MANUAL
        else:
            s.transition_to_scene("daylight", src)
            assert s.source == src

# ── last_scene_change_monotonic (D15) ───────────────────────────────────


def test_last_scene_change_monotonic_set_on_transition_to_scene():
    import time
    s = AreaState()
    before = time.monotonic()
    s.transition_to_scene("daylight", ActivationSource.USER)
    after = time.monotonic()
    assert s.last_scene_change_monotonic is not None
    assert before <= s.last_scene_change_monotonic <= after


def test_last_scene_change_monotonic_set_on_transition_to_circadian():
    import time
    s = AreaState()
    s.transition_to_circadian(ActivationSource.USER)
    assert s.last_scene_change_monotonic is not None
    assert s.last_scene_change_monotonic <= time.monotonic()


def test_last_scene_change_monotonic_set_on_transition_to_off():
    s = AreaState()
    s.transition_to_scene("daylight", ActivationSource.USER)
    first = s.last_scene_change_monotonic
    s.transition_to_off(ActivationSource.USER)
    second = s.last_scene_change_monotonic
    assert second is not None
    assert second >= first


def test_last_scene_change_monotonic_set_on_transition_to_manual():
    s = AreaState()
    s.transition_to_manual()
    assert s.last_scene_change_monotonic is not None


def test_last_scene_change_monotonic_not_in_to_dict():
    s = AreaState()
    s.transition_to_scene("evening", ActivationSource.USER)
    data = s.to_dict()
    assert "last_scene_change_monotonic" not in data


def test_from_dict_leaves_monotonic_as_none():
    """monotonic values don't survive restart."""
    s = AreaState.from_dict(
        {
            "state": "scene",
            "scene_slug": "evening",
            "source": "user",
            "dimmed": False,
        }
    )
    assert s.last_scene_change_monotonic is None

