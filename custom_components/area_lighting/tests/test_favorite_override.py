"""Unit tests for per-remote favorite button scene cycle override."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol

from custom_components.area_lighting.area_state import ActivationSource, AreaState
from custom_components.area_lighting.config_schema import parse_config
from custom_components.area_lighting.controller import AreaLightingController
from custom_components.area_lighting.scene_machine import ActionType, determine_favorite_action

# ── Helpers ───────────────────────────────────────────────────────────────


def _area_with_remotes(
    area_id: str = "bedroom",
    scenes: list[str] | None = None,
    remotes: list[dict] | None = None,
) -> dict:
    """Build a raw area dict with scenes and remotes for parse_config."""
    scene_list = [{"id": s, "name": s.title()} for s in (scenes or ["circadian", "night", "off"])]
    return {
        "id": area_id,
        "name": area_id.title(),
        "scenes": scene_list,
        "lutron_remotes": remotes or [],
    }


def _make_controller(
    scene_slugs: set[str] | None = None,
    current_scene: str = "off",
    source: ActivationSource = ActivationSource.USER,
    dimmed: bool = False,
    holiday_mode: str = "none",
) -> MagicMock:
    """Build a mock controller for lighting_favorite tests."""
    ctrl = MagicMock(spec=AreaLightingController)
    ctrl.area = MagicMock()
    ctrl.area.id = "test_area"
    ctrl.area.scene_slugs = scene_slugs or {"circadian", "night", "off"}

    state = AreaState()
    if current_scene == "circadian":
        state.transition_to_circadian(source)
    elif current_scene == "manual":
        state.transition_to_manual()
    elif current_scene != "off":
        state.transition_to_scene(current_scene, source)
    if dimmed:
        state.mark_dimmed()
    ctrl._state = state

    ctrl._get_holiday_mode = MagicMock(return_value=holiday_mode)
    ctrl._call_service = AsyncMock()
    ctrl._activate_scene = AsyncMock()
    ctrl._resolve_and_activate = AsyncMock()
    ctrl._notify_state_change = MagicMock()
    ctrl.lighting_favorite = AreaLightingController.lighting_favorite.__get__(ctrl)
    return ctrl


# ── Default behavior (no override) ───────────────────────────────────────


NR_SCENES = {"ambient", "christmas", "circadian", "daylight", "evening", "night"}


@pytest.mark.unit
def test_default_holiday_active_activates_holiday() -> None:
    action = determine_favorite_action("night", NR_SCENES, "christmas")
    assert action.action == ActionType.ACTIVATE_HOLIDAY_SCENE


@pytest.mark.unit
def test_default_no_holiday_activates_night() -> None:
    action = determine_favorite_action("off", NR_SCENES, "none")
    assert action.action == ActionType.ACTIVATE_SCENE
    assert action.scene_slug == "night"


@pytest.mark.unit
def test_default_already_on_holiday_activates_night() -> None:
    action = determine_favorite_action("christmas", NR_SCENES, "christmas")
    assert action.action == ActionType.ACTIVATE_SCENE
    assert action.scene_slug == "night"


@pytest.mark.unit
async def test_no_override_uses_determine_favorite_action() -> None:
    ctrl = _make_controller()
    await ctrl.lighting_favorite()
    ctrl._resolve_and_activate.assert_called_once()


# ── Single slug override ─────────────────────────────────────────────────


@pytest.mark.unit
async def test_single_slug_override_activates_scene() -> None:
    ctrl = _make_controller()
    await ctrl.lighting_favorite(favorite_cycle=["night"])
    ctrl._activate_scene.assert_called_once_with("night", ActivationSource.USER)


@pytest.mark.unit
async def test_single_slug_override_wins_over_holiday() -> None:
    ctrl = _make_controller(
        scene_slugs={"circadian", "christmas", "night", "off"},
        holiday_mode="christmas",
    )
    await ctrl.lighting_favorite(favorite_cycle=["circadian"])
    ctrl._activate_scene.assert_called_once_with("circadian", ActivationSource.USER)
    ctrl._resolve_and_activate.assert_not_called()


# ── Cycle override ───────────────────────────────────────────────────────


@pytest.mark.unit
async def test_cycle_from_off_starts_at_first() -> None:
    ctrl = _make_controller(current_scene="off")
    await ctrl.lighting_favorite(favorite_cycle=["reading", "night"])
    ctrl._activate_scene.assert_called_once_with("reading", ActivationSource.USER)


@pytest.mark.unit
async def test_cycle_advances_to_next() -> None:
    ctrl = _make_controller(
        scene_slugs={"circadian", "reading", "night", "off"},
        current_scene="reading",
    )
    await ctrl.lighting_favorite(favorite_cycle=["reading", "night"])
    ctrl._activate_scene.assert_called_once_with("night", ActivationSource.USER)


@pytest.mark.unit
async def test_cycle_wraps_around() -> None:
    ctrl = _make_controller(
        scene_slugs={"circadian", "reading", "night", "off"},
        current_scene="night",
    )
    await ctrl.lighting_favorite(favorite_cycle=["reading", "night"])
    ctrl._activate_scene.assert_called_once_with("reading", ActivationSource.USER)


@pytest.mark.unit
async def test_cycle_from_unknown_scene_starts_at_first() -> None:
    ctrl = _make_controller(current_scene="manual")
    await ctrl.lighting_favorite(favorite_cycle=["reading", "night"])
    ctrl._activate_scene.assert_called_once_with("reading", ActivationSource.USER)


@pytest.mark.unit
async def test_three_item_cycle() -> None:
    ctrl = _make_controller(
        scene_slugs={"circadian", "reading", "night", "evening", "off"},
        current_scene="reading",
    )
    await ctrl.lighting_favorite(favorite_cycle=["reading", "night", "evening"])
    ctrl._activate_scene.assert_called_once_with("night", ActivationSource.USER)


@pytest.mark.unit
async def test_three_item_cycle_last_to_first() -> None:
    ctrl = _make_controller(
        scene_slugs={"circadian", "reading", "night", "evening", "off"},
        current_scene="evening",
    )
    await ctrl.lighting_favorite(favorite_cycle=["reading", "night", "evening"])
    ctrl._activate_scene.assert_called_once_with("reading", ActivationSource.USER)


# ── External scene entity override ───────────────────────────────────────


@pytest.mark.unit
async def test_scene_entity_calls_scene_turn_on() -> None:
    ctrl = _make_controller()
    await ctrl.lighting_favorite(favorite_cycle=["scene.main_bedroom_reading"])
    ctrl._call_service.assert_called_once_with(
        "scene.turn_on", entity_id="scene.main_bedroom_reading"
    )
    ctrl._activate_scene.assert_not_called()


# ── Dimmed interaction ────────────────────────────────────────────────────


@pytest.mark.unit
async def test_override_on_dimmed_area_activates_target() -> None:
    """Override activates target scene via _activate_scene which clears dimmed."""
    ctrl = _make_controller(
        scene_slugs={"circadian", "reading", "night", "off"},
        current_scene="circadian",
        dimmed=True,
    )
    await ctrl.lighting_favorite(favorite_cycle=["reading"])
    ctrl._activate_scene.assert_called_once_with("reading", ActivationSource.USER)


# ── Source ────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_override_uses_user_source() -> None:
    ctrl = _make_controller(
        scene_slugs={"circadian", "reading", "night", "off"},
        current_scene="off",
    )
    await ctrl.lighting_favorite(favorite_cycle=["reading"])
    ctrl._activate_scene.assert_called_once_with("reading", ActivationSource.USER)
    args = ctrl._activate_scene.call_args
    assert args[0][1] == ActivationSource.USER


@pytest.mark.unit
async def test_override_from_motion_state_uses_user_source() -> None:
    ctrl = _make_controller(
        scene_slugs={"circadian", "reading", "night", "off"},
        current_scene="circadian",
        source=ActivationSource.MOTION,
    )
    await ctrl.lighting_favorite(favorite_cycle=["reading"])
    ctrl._activate_scene.assert_called_once_with("reading", ActivationSource.USER)


# ── Schema validation ────────────────────────────────────────────────────


@pytest.mark.unit
def test_schema_accepts_single_slug() -> None:
    raw = {
        "areas": [
            _area_with_remotes(
                scenes=["circadian", "night", "off"],
                remotes=[{"id": "abc", "name": "Remote", "buttons": {"favorite": "night"}}],
            )
        ]
    }
    cfg = parse_config(raw)
    remote = cfg.enabled_areas[0].lutron_remotes[0]
    assert remote.favorite_cycle == ["night"]


@pytest.mark.unit
def test_schema_accepts_list_of_slugs() -> None:
    raw = {
        "areas": [
            _area_with_remotes(
                scenes=["circadian", "reading", "night", "off"],
                remotes=[
                    {
                        "id": "abc",
                        "name": "Remote",
                        "buttons": {"favorite": ["reading", "night"]},
                    }
                ],
            )
        ]
    }
    cfg = parse_config(raw)
    remote = cfg.enabled_areas[0].lutron_remotes[0]
    assert remote.favorite_cycle == ["reading", "night"]


@pytest.mark.unit
def test_schema_accepts_scene_entity_id() -> None:
    raw = {
        "areas": [
            _area_with_remotes(
                remotes=[
                    {
                        "id": "abc",
                        "name": "Remote",
                        "buttons": {"favorite": "scene.custom_reading"},
                    }
                ],
            )
        ]
    }
    cfg = parse_config(raw)
    remote = cfg.enabled_areas[0].lutron_remotes[0]
    assert remote.favorite_cycle == ["scene.custom_reading"]


@pytest.mark.unit
def test_schema_accepts_self_area_scene_entity_with_known_suffix() -> None:
    raw = {
        "areas": [
            _area_with_remotes(
                area_id="bedroom",
                scenes=["circadian", "night", "off"],
                remotes=[
                    {
                        "id": "abc",
                        "name": "Remote",
                        "buttons": {"favorite": "scene.bedroom_night"},
                    }
                ],
            )
        ]
    }
    cfg = parse_config(raw)
    remote = cfg.enabled_areas[0].lutron_remotes[0]
    assert remote.favorite_cycle == ["scene.bedroom_night"]


@pytest.mark.unit
def test_schema_rejects_self_area_scene_entity_with_unknown_suffix() -> None:
    raw = {
        "areas": [
            _area_with_remotes(
                area_id="bedroom",
                scenes=["circadian", "night", "off"],
                remotes=[
                    {
                        "id": "abc",
                        "name": "Remote",
                        "buttons": {"favorite": "scene.bedroom_nonexistent"},
                    }
                ],
            )
        ]
    }
    with pytest.raises(vol.Invalid, match="nonexistent"):
        parse_config(raw)


@pytest.mark.unit
def test_schema_accepts_cross_area_scene_entity() -> None:
    raw = {
        "areas": [
            _area_with_remotes(
                area_id="bedroom",
                scenes=["circadian", "night", "off"],
                remotes=[
                    {
                        "id": "abc",
                        "name": "Remote",
                        "buttons": {"favorite": "scene.kitchen_dinner"},
                    }
                ],
            )
        ]
    }
    cfg = parse_config(raw)
    remote = cfg.enabled_areas[0].lutron_remotes[0]
    assert remote.favorite_cycle == ["scene.kitchen_dinner"]


@pytest.mark.unit
def test_schema_rejects_unknown_slug() -> None:
    raw = {
        "areas": [
            _area_with_remotes(
                scenes=["circadian", "night", "off"],
                remotes=[{"id": "abc", "name": "Remote", "buttons": {"favorite": "nonexistent"}}],
            )
        ]
    }
    with pytest.raises(vol.Invalid, match="nonexistent"):
        parse_config(raw)


@pytest.mark.unit
def test_schema_rejects_unknown_slug_in_list() -> None:
    raw = {
        "areas": [
            _area_with_remotes(
                scenes=["circadian", "night", "off"],
                remotes=[
                    {
                        "id": "abc",
                        "name": "Remote",
                        "buttons": {"favorite": ["night", "bogus"]},
                    }
                ],
            )
        ]
    }
    with pytest.raises(vol.Invalid, match="bogus"):
        parse_config(raw)


@pytest.mark.unit
def test_schema_rejects_scene_entity_in_list() -> None:
    raw = {
        "areas": [
            _area_with_remotes(
                scenes=["circadian", "night", "off"],
                remotes=[
                    {
                        "id": "abc",
                        "name": "Remote",
                        "buttons": {"favorite": ["night", "scene.foo"]},
                    }
                ],
            )
        ]
    }
    with pytest.raises(vol.Invalid, match="scene entity"):
        parse_config(raw)


@pytest.mark.unit
def test_schema_no_favorite_leaves_empty_cycle() -> None:
    raw = {
        "areas": [
            _area_with_remotes(
                remotes=[{"id": "abc", "name": "Remote"}],
            )
        ]
    }
    cfg = parse_config(raw)
    remote = cfg.enabled_areas[0].lutron_remotes[0]
    assert remote.favorite_cycle == []


@pytest.mark.unit
def test_schema_empty_buttons_leaves_empty_cycle() -> None:
    raw = {
        "areas": [
            _area_with_remotes(
                remotes=[{"id": "abc", "name": "Remote", "buttons": {}}],
            )
        ]
    }
    cfg = parse_config(raw)
    remote = cfg.enabled_areas[0].lutron_remotes[0]
    assert remote.favorite_cycle == []
