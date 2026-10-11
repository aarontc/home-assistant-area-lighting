"""Unit tests for the per-remote ``buttons.on`` sequence."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol

from custom_components.area_lighting import CONFIG_SCHEMA
from custom_components.area_lighting.area_state import ActivationSource, AreaState
from custom_components.area_lighting.config_schema import parse_config
from custom_components.area_lighting.controller import AreaLightingController
from custom_components.area_lighting.scene_machine import (
    ActionType,
    determine_on_sequence_action,
)

DEN_SCENES = {"ambient", "christmas", "circadian", "daylight", "evening", "night", "study"}


def _on(current: str, sequence: list[str] | None = None, holiday: str = "none"):
    return determine_on_sequence_action(
        current_scene=current,
        scene_slugs=DEN_SCENES,
        holiday_mode=holiday,
        on_sequence=sequence or ["study"],
    )


def _assert_scene(action, slug: str) -> None:
    assert action.action == ActionType.ACTIVATE_SCENE
    assert action.scene_slug == slug


# ── Scene machine ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("current", ["off", "manual", "ambient", "night"])
def test_no_holiday_starts_with_first_scene(current: str) -> None:
    _assert_scene(_on(current), "study")


@pytest.mark.unit
@pytest.mark.parametrize("current", ["off", "manual", "ambient", "night"])
def test_holiday_comes_first(current: str) -> None:
    assert _on(current, holiday="christmas").action == ActionType.ACTIVATE_HOLIDAY_SCENE


@pytest.mark.unit
def test_holiday_scene_moves_to_first_scene() -> None:
    _assert_scene(_on("christmas", holiday="christmas"), "study")


@pytest.mark.unit
def test_holiday_the_area_lacks_is_skipped() -> None:
    _assert_scene(_on("off", holiday="halloween"), "study")


@pytest.mark.unit
def test_leftover_holiday_scene_moves_to_first_scene() -> None:
    _assert_scene(_on("christmas"), "study")


@pytest.mark.unit
def test_single_scene_sequence_continues_to_circadian() -> None:
    _assert_scene(_on("study", holiday="christmas"), "circadian")


@pytest.mark.unit
def test_sequence_advances_in_order() -> None:
    _assert_scene(_on("study", sequence=["study", "ambient"]), "ambient")
    _assert_scene(_on("ambient", sequence=["study", "ambient"]), "circadian")


@pytest.mark.unit
def test_circadian_goes_to_inverted_sun_position() -> None:
    assert _on("circadian").action == ActionType.SET_SUN_POSITION_INVERTED


@pytest.mark.unit
@pytest.mark.parametrize(("current", "target"), [("daylight", "evening"), ("evening", "daylight")])
def test_daylight_and_evening_alternate(current: str, target: str) -> None:
    _assert_scene(_on(current), target)
    _assert_scene(_on(current, holiday="christmas"), target)


@pytest.mark.unit
def test_no_circadian_falls_back_to_sun_position() -> None:
    action = determine_on_sequence_action(
        current_scene="study",
        scene_slugs={"study", "daylight", "evening"},
        holiday_mode="none",
        on_sequence=["study"],
    )
    assert action.action == ActionType.SET_SUN_POSITION


# ── Controller ───────────────────────────────────────────────────────────


def _make_controller(current_scene: str = "off", dimmed: bool = False) -> MagicMock:
    ctrl = MagicMock(spec=AreaLightingController)
    ctrl.area = MagicMock()
    ctrl.area.id = "den"
    ctrl.area.scene_slugs = DEN_SCENES
    state = AreaState()
    if current_scene != "off":
        state.transition_to_scene(current_scene, ActivationSource.USER)
    if dimmed:
        state.mark_dimmed()
    ctrl._state = state
    ctrl._resolve_leader_on_slug = MagicMock(return_value=None)
    ctrl._get_holiday_mode = MagicMock(return_value="none")
    ctrl._motion_override_ambient = False
    ctrl._night_mode = True
    ctrl._activate_scene = AsyncMock()
    ctrl._resolve_and_activate = AsyncMock()
    ctrl.lighting_on = AreaLightingController.lighting_on.__get__(ctrl)
    return ctrl


@pytest.mark.unit
async def test_sequence_overrides_night_mode() -> None:
    ctrl = _make_controller()
    await ctrl.lighting_on(on_sequence=["study"])
    action = ctrl._resolve_and_activate.call_args.args[0]
    _assert_scene(action, "study")


@pytest.mark.unit
async def test_without_sequence_night_mode_still_applies() -> None:
    ctrl = _make_controller()
    await ctrl.lighting_on()
    action = ctrl._resolve_and_activate.call_args.args[0]
    _assert_scene(action, "night")


@pytest.mark.unit
async def test_dimmed_restores_current_scene() -> None:
    ctrl = _make_controller(current_scene="evening", dimmed=True)
    await ctrl.lighting_on(on_sequence=["study"])
    ctrl._activate_scene.assert_called_once_with("evening", ActivationSource.USER)


@pytest.mark.unit
async def test_sequence_overrides_leader_mirroring() -> None:
    ctrl = _make_controller()
    ctrl._resolve_leader_on_slug.return_value = "night"
    await ctrl.lighting_on(on_sequence=["study"])
    ctrl._activate_scene.assert_not_called()
    _assert_scene(ctrl._resolve_and_activate.call_args.args[0], "study")


@pytest.mark.unit
async def test_motion_ignores_sequence() -> None:
    ctrl = _make_controller()
    await ctrl.lighting_on(ActivationSource.MOTION, on_sequence=["study"])
    action = ctrl._resolve_and_activate.call_args.args[0]
    _assert_scene(action, "night")


# ── Schema ───────────────────────────────────────────────────────────────


def _config(buttons: dict) -> dict:
    return {
        "areas": [
            {
                "id": "den",
                "name": "Den",
                "scenes": [{"id": s, "name": s.title()} for s in sorted(DEN_SCENES)]
                + [{"id": "off", "name": "Off"}],
                "lutron_remotes": [
                    {"id": "abc123", "name": "Entry Remote", "buttons": buttons},
                ],
            }
        ]
    }


def _parse(buttons: dict):
    """Validate through the integration's schema, then parse, as setup does."""
    return parse_config(CONFIG_SCHEMA({"area_lighting": _config(buttons)})["area_lighting"])


def _remote(buttons: dict):
    return _parse(buttons).areas[0].lutron_remotes[0]


@pytest.mark.unit
def test_schema_accepts_list() -> None:
    assert _remote({"on": ["study", "night"]}).on_sequence == ["study", "night"]


@pytest.mark.unit
def test_schema_accepts_single_slug() -> None:
    assert _remote({"on": "study"}).on_sequence == ["study"]


@pytest.mark.unit
def test_schema_default_is_empty() -> None:
    assert _remote({}).on_sequence == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "buttons",
    [
        {"on": ["reading"]},
        {"on": ["off"]},
        {"on": ["circadian"]},
        {"on": ["study", "daylight"]},
        {"on": ["evening"]},
        {"on": ["study", "christmas"]},
        {"on": ["study", "study"]},
        {"on": []},
        {"on": ["scene.den_study"]},
        {True: ["study"]},
    ],
)
def test_schema_rejects(buttons: dict) -> None:
    with pytest.raises(vol.Invalid):
        _parse(buttons)


@pytest.mark.unit
def test_schema_unquoted_on_key_names_the_fix() -> None:
    with pytest.raises(vol.Invalid, match="quote the key"):
        _parse({True: ["study"]})
