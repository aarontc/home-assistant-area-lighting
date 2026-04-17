"""Data models for the Area Lighting integration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CircadianSwitchConfig:
    """Configuration for a circadian lighting switch."""

    name: str
    area_id: str
    area_name: str
    max_brightness: int | None = None
    min_brightness: int | None = None

    @property
    def short_name(self) -> str:
        return self.name

    @property
    def full_name(self) -> str:
        return f"{self.area_name} {self.name} Circadian"

    @property
    def entity_id(self) -> str:
        slug = self.full_name.lower().replace(" ", "_").replace("-", "_")
        return f"switch.circadian_lighting_{slug}"


@dataclass
class LightConfig:
    """Configuration for an individual light or light cluster.

    A cluster is a LightConfig whose `members` is non-empty — typically
    a Hue Zone entity that represents multiple physical lights. Clusters
    are used by the scene dispatcher to batch commands: if every member
    of a cluster is in the same target-state cohort, a single service
    call to the cluster replaces N per-light calls.
    """

    id: str
    circadian_switch: str | None = None
    circadian_type: str | None = None
    roles: list[str] = field(default_factory=list)
    scenes: list[str] = field(default_factory=list)
    # Member entity IDs — non-empty marks this LightConfig as a cluster.
    members: list[str] = field(default_factory=list)

    def in_scene(self, scene_slug: str) -> bool:
        """Whether this light participates in the given scene."""
        return not self.scenes or scene_slug in self.scenes

    @property
    def is_cluster(self) -> bool:
        return bool(self.members)


@dataclass
class SceneConfig:
    """Configuration for a scene in an area."""

    slug: str
    name: str
    area_id: str
    group_exclude: list[str] = field(default_factory=list)
    cycle: list[str] | None = None
    entities: dict[str, dict] | None = None  # Inline light state data
    icon: str | None = None

    @property
    def entity_id(self) -> str:
        return f"scene.{self.area_id}_{self.slug}"

    @property
    def off_internal_entity_id(self) -> str:
        """Entity ID for the off_internal variant (only meaningful for 'off' scenes)."""
        return f"scene.{self.area_id}_off_internal"


@dataclass
class MotionLightCondition:
    """A condition that must be met for motion lighting to activate.

    Exactly one of ``entity_id`` or ``entity_ids`` must be set. When
    ``entity_ids`` is set, ``aggregate`` selects the reduction mode
    applied across the sensors' numeric values before the above/below
    comparison.
    """

    entity_id: str | None = None
    entity_ids: list[str] | None = None
    aggregate: str | None = None  # "average" | "min" | "max"
    state: str | None = None
    attribute: str | None = None
    above: float | None = None
    below: float | None = None


@dataclass
class LutronRemoteConfig:
    """Configuration for a Lutron Pico remote."""

    id: str
    name: str
    additional_actions: dict[str, list[dict]] = field(default_factory=dict)
    buttons: dict[str, str] = field(default_factory=dict)


@dataclass
class LinkedMotionMapping:
    """A mapping from a remote area's scene to local/remote scene overrides."""

    local_scene: str
    remote_scene: str | None  # None means don't touch the remote area


@dataclass
class LinkedMotionConfig:
    """Cross-area motion coordination config for one linked area."""

    remote_area: str  # area_id of the remote area
    default: LinkedMotionMapping
    when_remote_scene: dict[str, LinkedMotionMapping] = field(default_factory=dict)

    def resolve(self, remote_scene_slug: str) -> LinkedMotionMapping:
        """Look up the mapping for the remote area's current scene."""
        return self.when_remote_scene.get(remote_scene_slug, self.default)


@dataclass
class AlertStep:
    """One step in an alert pattern animation."""

    target: str  # "all", "color", "white"
    state: str  # "on", "off"
    delay: float = 0.0
    brightness: int | None = None
    rgb_color: tuple[int, int, int] | None = None
    color_temp_kelvin: int | None = None
    hs_color: tuple[float, float] | None = None
    xy_color: tuple[float, float] | None = None
    transition: float | None = None


@dataclass
class AlertPattern:
    """Named alert/flash pattern configuration."""

    steps: list[AlertStep]
    delay: float = 0.0
    repeat: int = 1
    start_inverted: bool = False
    restore: bool = True


@dataclass
class AreaConfig:
    """Configuration for an area/room."""

    id: str
    name: str
    enabled: bool = True
    event_handlers: bool = False  # Whether to register motion/remote/light event handlers
    icon: str | None = None
    special: str | None = None
    ambient_lighting_zone: str | None = None
    # D3: per-area override for the brightness step percentage
    brightness_step_pct: int | None = None
    # D6: per-area night-mode fade override
    night_fadeout_seconds: float | None = None
    # Leader/follower: when set, this area follows the named leader's scene state.
    leader_area_id: str | None = None
    # When True, follower also follows leader transitions to off/ambient.
    follow_leader_deactivation: bool = False

    circadian_switches: list[CircadianSwitchConfig] = field(default_factory=list)
    lights: list[LightConfig] = field(default_factory=list)
    light_clusters: list[LightConfig] = field(default_factory=list)
    scenes: list[SceneConfig] = field(default_factory=list)
    lutron_remotes: list[LutronRemoteConfig] = field(default_factory=list)

    motion_light_motion_sensor_ids: list[str] | None = None
    motion_light_conditions: list[MotionLightCondition] = field(default_factory=list)
    motion_light_timer_durations: dict[str, str] = field(default_factory=dict)

    occupancy_light_sensor_ids: list[str] | None = None
    occupancy_light_timer_durations: dict[str, str] = field(default_factory=dict)

    linked_motion: list[LinkedMotionConfig] = field(default_factory=list)

    @property
    def all_lights(self) -> list[LightConfig]:
        """All lights and light clusters combined."""
        return self.lights + self.light_clusters

    @property
    def scene_slugs(self) -> set[str]:
        return {s.slug for s in self.scenes}

    @property
    def has_holiday_scenes(self) -> bool:
        return bool({"christmas", "halloween"} & self.scene_slugs)

    @property
    def has_ambient_scene(self) -> bool:
        return "ambient" in self.scene_slugs

    @property
    def has_circadian_scene(self) -> bool:
        return "circadian" in self.scene_slugs

    @property
    def has_motion_lighting(self) -> bool:
        return self.motion_light_motion_sensor_ids is not None

    @property
    def has_occupancy_lighting(self) -> bool:
        return self.occupancy_light_sensor_ids is not None

    @property
    def last_scene_options(self) -> list[str]:
        """Options for the last_scene select entity."""
        slugs = {s.slug for s in self.scenes if s.slug != "off_internal"}
        # Always include these even if not explicitly configured as scenes
        slugs.add("off")
        slugs.add("manual")
        return sorted(slugs)

    def lights_with_role(self, role: str) -> list[LightConfig]:
        return [light for light in self.all_lights if role in light.roles]

    def lights_in_scene(self, scene_slug: str) -> list[LightConfig]:
        return [light for light in self.all_lights if light.in_scene(scene_slug)]

    def circadian_switch_for_light(self, light: LightConfig) -> CircadianSwitchConfig | None:
        if not light.circadian_switch:
            return None
        for cs in self.circadian_switches:
            if cs.short_name == light.circadian_switch:
                return cs
        return None


@dataclass
class AreaLightingConfig:
    """Top-level configuration for the area_lighting integration."""

    areas: list[AreaConfig] = field(default_factory=list)
    alert_patterns: dict[str, AlertPattern] = field(default_factory=dict)

    @property
    def enabled_areas(self) -> list[AreaConfig]:
        return [a for a in self.areas if a.enabled and a.special != "global"]

    def area_by_id(self, area_id: str) -> AreaConfig | None:
        for area in self.areas:
            if area.id == area_id:
                return area
        return None
