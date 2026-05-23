"""Unit tests for `AreaLightingController.state_matches_scene_target`.

Background: manual-detection (`event_handlers._make_manual_detection_handler`)
calls this method to decide whether an incoming light state change is a real
user override or just a late bridge report converging on the scene's target.
A False return demotes the area to `manual`.

Regression covered here: when an `hs_color` target is sent to a bulb whose
native color space is xy (Philips Hue), the value HA reads back from the
bridge is the bulb's actual stored xy inverted to hs. Because the requested
hs sits outside the bulb's gamut (CIE pure red vs. Hue's gamut C red corner),
the round trip shifts the reported hue by ~10°. The old hs comparison
flagged this as a manual override every time, even though the bulb was
doing exactly what was asked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.area_lighting.controller import AreaLightingController
from custom_components.area_lighting.models import AreaConfig, AreaLightingConfig, SceneConfig


def _make_controller() -> AreaLightingController:
    area = AreaConfig(
        id="bedroom",
        name="Bedroom",
        scenes=[SceneConfig(slug="night", name="Night", area_id="bedroom")],
    )
    hass = MagicMock()
    hass.data = {}
    return AreaLightingController(hass, area, AreaLightingConfig(areas=[area]))


def _state(state: str, **attrs) -> SimpleNamespace:
    return SimpleNamespace(state=state, attributes=attrs)


# ── xy-mode bulbs: hs target must not false-positive on gamut clamping ──


def test_hs_target_matches_xy_actual_with_clamped_red():
    """hs=[0,100] target vs Hue's clamped red corner must compare equal.

    Reproduces the production trace: target hs_color=[0,100] is sent;
    Hue stores xy=(0.6915, 0.3083) (its gamut C red corner, the closest
    in-gamut point to CIE pure red); the bulb reports color_mode=xy,
    xy_color=(0.6915, 0.3083), hs_color=(10.118, 100.0). The 10.118°
    hue diff exceeds the 10° tolerance and used to fire manual detection.
    """
    ctrl = _make_controller()
    ctrl._active_scene_targets = {
        "light.bedroom_bedside": {
            "state": "on",
            "brightness": 128,
            "hs_color": [0, 100],
        }
    }

    actual = _state(
        "on",
        brightness=128,
        color_mode="xy",
        xy_color=(0.6915, 0.3083),
        hs_color=(10.118, 100.0),
    )

    assert ctrl.state_matches_scene_target("light.bedroom_bedside", actual)


def test_hs_target_rejects_genuinely_different_color_in_xy_mode():
    """A real color change (e.g. red → blue) must still trip manual."""
    ctrl = _make_controller()
    ctrl._active_scene_targets = {
        "light.bedroom_bedside": {
            "state": "on",
            "brightness": 128,
            "hs_color": [0, 100],
        }
    }

    actual = _state(
        "on",
        brightness=128,
        color_mode="xy",
        xy_color=(0.135, 0.040),  # Hue blue corner
        hs_color=(240.0, 100.0),
    )

    assert not ctrl.state_matches_scene_target("light.bedroom_bedside", actual)


def test_xy_target_matches_xy_actual_within_tolerance():
    """Scene configured with xy_color directly: compare in xy space."""
    ctrl = _make_controller()
    ctrl._active_scene_targets = {
        "light.bedroom_bedside": {
            "state": "on",
            "brightness": 128,
            "xy_color": [0.6915, 0.3083],
        }
    }

    actual = _state(
        "on",
        brightness=128,
        color_mode="xy",
        xy_color=(0.6920, 0.3080),  # tiny bridge jitter
        hs_color=(10.0, 100.0),
    )

    assert ctrl.state_matches_scene_target("light.bedroom_bedside", actual)


# ── hs-mode bulbs: existing tolerance behavior preserved ─────────────────


def test_hs_target_matches_hs_actual_within_hue_tolerance():
    """Bulbs with native hs color (rare): use existing hs comparison."""
    ctrl = _make_controller()
    ctrl._active_scene_targets = {
        "light.bedroom_bedside": {
            "state": "on",
            "brightness": 128,
            "hs_color": [0, 100],
        }
    }

    actual = _state(
        "on",
        brightness=128,
        color_mode="hs",
        hs_color=(5.0, 98.0),
    )

    assert ctrl.state_matches_scene_target("light.bedroom_bedside", actual)


def test_hs_target_rejects_hs_actual_outside_hue_tolerance():
    """In hs mode, hue diff > 10° still indicates a real change."""
    ctrl = _make_controller()
    ctrl._active_scene_targets = {
        "light.bedroom_bedside": {
            "state": "on",
            "brightness": 128,
            "hs_color": [0, 100],
        }
    }

    actual = _state(
        "on",
        brightness=128,
        color_mode="hs",
        hs_color=(45.0, 100.0),
    )

    assert not ctrl.state_matches_scene_target("light.bedroom_bedside", actual)


# ── brightness and on/off (regression guard, behavior unchanged) ─────────


def test_brightness_diff_beyond_tolerance_rejected():
    ctrl = _make_controller()
    ctrl._active_scene_targets = {"light.bedroom_bedside": {"state": "on", "brightness": 128}}

    actual = _state("on", brightness=200)

    assert not ctrl.state_matches_scene_target("light.bedroom_bedside", actual)


def test_off_target_matches_off_actual():
    ctrl = _make_controller()
    ctrl._active_scene_targets = {"light.bedroom_bedside": {"state": "off"}}

    actual = _state("off")

    assert ctrl.state_matches_scene_target("light.bedroom_bedside", actual)


def test_no_target_returns_false():
    ctrl = _make_controller()
    ctrl._active_scene_targets = {}

    actual = _state("on", brightness=128)

    assert not ctrl.state_matches_scene_target("light.bedroom_bedside", actual)
