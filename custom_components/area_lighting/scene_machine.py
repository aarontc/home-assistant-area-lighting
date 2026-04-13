"""Pure-function scene cycling logic for Area Lighting.

All functions in this module are free of Home Assistant dependencies,
making them trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class ActionType(Enum):
    """What the controller should do after the scene machine decides."""

    ACTIVATE_SCENE = auto()
    ACTIVATE_HOLIDAY_SCENE = auto()
    SET_SUN_POSITION = auto()
    SET_SUN_POSITION_INVERTED = auto()
    NOOP = auto()


@dataclass
class SceneAction:
    """Result from the scene machine telling the controller what to do."""

    action: ActionType
    scene_slug: str | None = None

    @classmethod
    def noop(cls) -> SceneAction:
        return cls(action=ActionType.NOOP)

    @classmethod
    def activate(cls, scene_slug: str) -> SceneAction:
        return cls(action=ActionType.ACTIVATE_SCENE, scene_slug=scene_slug)

    @classmethod
    def activate_holiday(cls) -> SceneAction:
        return cls(action=ActionType.ACTIVATE_HOLIDAY_SCENE)

    @classmethod
    def sun_position(cls) -> SceneAction:
        return cls(action=ActionType.SET_SUN_POSITION)

    @classmethod
    def sun_position_inverted(cls) -> SceneAction:
        return cls(action=ActionType.SET_SUN_POSITION_INVERTED)


HOLIDAY_SCENES = frozenset({"christmas", "halloween"})


def _non_holiday_default(
    scene_slugs: set[str],
    night_mode: bool,
) -> SceneAction:
    """Determine the default scene when no holiday is active.

    Checks night mode first, then falls back to circadian > sun position > first generic scene.
    """
    if night_mode and "night" in scene_slugs:
        return SceneAction.activate("night")
    return _non_night_default(scene_slugs)


def _non_night_default(scene_slugs: set[str]) -> SceneAction:
    """Default scene ignoring night mode: circadian > sun position > first generic."""
    if "circadian" in scene_slugs:
        return SceneAction.activate("circadian")
    if "daylight" in scene_slugs and "evening" in scene_slugs:
        return SceneAction.sun_position()
    # Fall back to first generic scene (not off, not ambient)
    generic = sorted(scene_slugs - {"off", "off_internal", "ambient"})
    if generic:
        return SceneAction.activate(generic[0])
    return SceneAction.noop()


def _default_sequence(
    scene_slugs: set[str],
    holiday_mode: str,
    night_mode: bool,
) -> SceneAction:
    """The full default: holiday > night > non-night default."""
    has_holiday = bool(HOLIDAY_SCENES & scene_slugs)
    if has_holiday and holiday_mode != "none" and holiday_mode in scene_slugs:
        return SceneAction.activate_holiday()
    return _non_holiday_default(scene_slugs, night_mode)


def determine_on_action(
    current_scene: str,
    scene_slugs: set[str],
    dimmed: bool,
    triggered_by_motion: bool,
    motion_override_ambient: bool,
    holiday_mode: str,
    night_mode: bool,
) -> SceneAction:
    """Determine what scene to activate when 'on' is pressed/triggered.

    Args:
        current_scene: The area's current scene slug (from last_scene).
        scene_slugs: Set of all scene slugs available in this area.
        dimmed: Whether the scene is currently dimmed (raise/lower was used).
        triggered_by_motion: Whether this was triggered by a motion sensor.
        motion_override_ambient: Whether motion can override ambient scenes.
        holiday_mode: Current holiday mode ('none', 'christmas', 'halloween').
        night_mode: Whether night mode is enabled for the area.
    """
    # 1. Motion-triggered + lights already on → no-op
    #    Exception: motion_override_ambient when in ambient scene
    if triggered_by_motion and current_scene != "off":
        if current_scene != "ambient" or not motion_override_ambient:
            return SceneAction.noop()

    # 2. If dimmed, restore current scene
    if dimmed:
        if current_scene in scene_slugs and current_scene != "off":
            return SceneAction.activate(current_scene)
        # Dimmed but invalid scene → use default
        return _default_sequence(scene_slugs, holiday_mode, night_mode)

    # 3. If off or manual → use default sequence
    if current_scene in ("off", "manual"):
        return _default_sequence(scene_slugs, holiday_mode, night_mode)

    # 4. If in night scene → cycle to non-night default (circadian/sun position)
    if current_scene == "night":
        return _non_night_default(scene_slugs)

    # 5. If in circadian → switch to inverted sun position (daylight/evening)
    if current_scene == "circadian" and "circadian" in scene_slugs:
        return SceneAction.sun_position_inverted()

    # 6. If daylight → evening, evening → daylight
    if current_scene == "daylight" and "evening" in scene_slugs:
        return SceneAction.activate("evening")
    if current_scene == "evening" and "daylight" in scene_slugs:
        return SceneAction.activate("daylight")

    # 7. If in a holiday scene → cycle to non-night default
    if current_scene in HOLIDAY_SCENES:
        return _non_night_default(scene_slugs)

    # 8. Default: holiday > night > circadian
    return _default_sequence(scene_slugs, holiday_mode, night_mode)


def determine_off_action(
    current_scene: str,
    source: str,
    ambient_zone_enabled: bool,
    area_ambience_enabled: bool,
    holiday_mode: str,
    ambient_scene_mode: str,
) -> SceneAction:
    """Determine what to do when 'off' is pressed.

    Source-aware (D7): holiday/ambient scenes that are ambience-owned
    go straight to off; user-owned holiday scenes with ambience active
    fall back to literal ambient.

    Args:
        current_scene: The area's current scene slug.
        source: The activation source of the CURRENT scene (not the off
            event). Used to distinguish user-owned from ambience-owned.
            String value matching ActivationSource enum values.
        ambient_zone_enabled: Whether the ambient zone toggle is on.
        area_ambience_enabled: Whether the area's ambience is enabled.
        holiday_mode: Current holiday mode.
        ambient_scene_mode: Value of input_select.ambient_scene.
    """
    ambience_active = ambient_zone_enabled and area_ambience_enabled
    in_holiday = current_scene in HOLIDAY_SCENES
    in_ambient = current_scene == "ambient"

    # Already in an ambience-owned ambient/holiday state → off for real
    if (in_holiday or in_ambient) and source == "ambience":
        return SceneAction.activate("off_internal")

    if ambience_active:
        # In a user-owned holiday scene → fall back to literal ambient
        if in_holiday:
            return SceneAction.activate("ambient")
        # In a non-ambient-like scene → ambient or holiday-ambient
        if not in_ambient:
            if ambient_scene_mode == "holiday" and holiday_mode != "none":
                return SceneAction.activate_holiday()
            return SceneAction.activate("ambient")

    return SceneAction.activate("off_internal")


def determine_off_fade_action(
    current_scene: str,
    source: str,
    ambient_zone_enabled: bool,
    area_ambience_enabled: bool,
    holiday_mode: str,
    ambient_scene_mode: str,
) -> SceneAction:
    """Determine what to do when fading off (motion/occupancy timer expired).

    Same logic as off but the controller will apply a transition duration.
    """
    return determine_off_action(
        current_scene, source, ambient_zone_enabled, area_ambience_enabled,
        holiday_mode, ambient_scene_mode,
    )


def determine_favorite_action(
    current_scene: str,
    scene_slugs: set[str],
    holiday_mode: str,
) -> SceneAction:
    """Determine what to do when 'favorite' is pressed.

    Port of lighting_favorite.erb.yaml.
    Holiday (if not already on holiday) → night → holiday → night cycle.
    """
    if holiday_mode != "none" and current_scene not in HOLIDAY_SCENES:
        has_holiday = bool(HOLIDAY_SCENES & scene_slugs)
        if has_holiday:
            return SceneAction.activate_holiday()

    return SceneAction.activate("night")


def resolve_sun_position(daylight_enabled: bool) -> str:
    """Resolve sun position to a scene slug.

    Port of global_set_scene_by_sun_position.
    """
    return "daylight" if daylight_enabled else "evening"


def resolve_sun_position_inverted(daylight_enabled: bool) -> str:
    """Resolve inverted sun position to a scene slug.

    Port of global_set_scene_by_sun_position_inverted.
    """
    return "daylight" if not daylight_enabled else "evening"
