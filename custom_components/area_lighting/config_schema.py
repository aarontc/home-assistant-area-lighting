"""Voluptuous schemas for the Area Lighting integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .const import ALL_ROLES, CIRCADIAN_BRIGHTNESS, CIRCADIAN_CT, CIRCADIAN_RGB
from .models import (
    AreaConfig,
    AreaLightingConfig,
    CircadianSwitchConfig,
    LightConfig,
    LinkedMotionConfig,
    LinkedMotionMapping,
    LutronRemoteConfig,
    MotionLightCondition,
    SceneConfig,
)

CIRCADIAN_SWITCH_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("max_brightness"): vol.All(int, vol.Range(min=1, max=100)),
        vol.Optional("min_brightness"): vol.All(int, vol.Range(min=1, max=100)),
    }
)

LIGHT_SCHEMA = vol.Schema(
    {
        vol.Required("id"): cv.entity_id,
        vol.Optional("circadian_switch"): cv.string,
        vol.Optional("circadian_type"): vol.In([CIRCADIAN_CT, CIRCADIAN_BRIGHTNESS, CIRCADIAN_RGB]),
        vol.Optional("roles", default=[]): vol.All(cv.ensure_list, [vol.In(ALL_ROLES)]),
        vol.Optional("scenes", default=[]): vol.All(cv.ensure_list, [cv.string]),
        # Cluster members — if set, this LightConfig represents a Hue Zone
        # or similar batch target. Scene dispatch will coalesce per-light
        # commands into a single cluster command when all members share
        # the same target state.
        vol.Optional("members", default=[]): vol.All(cv.ensure_list, [cv.entity_id]),
    }
)

SCENE_SCHEMA = vol.Schema(
    {
        vol.Required("id"): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("group_exclude", default=[]): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("cycle"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("entities"): dict,  # Per-light state data for the scene
        vol.Optional("icon"): cv.icon,
    }
)


def _validate_motion_light_condition(value: dict) -> dict:
    """Enforce mutual-exclusion rules not expressible via the base schema."""
    has_single = "entity_id" in value
    has_multi = "entity_ids" in value
    if has_single and has_multi:
        raise vol.Invalid(
            "motion_light_condition: specify either 'entity_id' or 'entity_ids', not both"
        )
    if not has_single and not has_multi:
        raise vol.Invalid("motion_light_condition: one of 'entity_id' or 'entity_ids' is required")
    if has_multi and "aggregate" not in value:
        raise vol.Invalid(
            "motion_light_condition: 'aggregate' is required when 'entity_ids' is set"
        )
    if has_multi and "state" in value:
        raise vol.Invalid(
            "motion_light_condition: 'state' is not supported with 'entity_ids' "
            "(string-state matching does not aggregate)"
        )
    return value


MOTION_LIGHT_CONDITION_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional("entity_id"): cv.entity_id,
            vol.Optional("entity_ids"): vol.All(
                cv.ensure_list,
                vol.Length(min=1),
                [cv.entity_id],
            ),
            vol.Optional("aggregate"): vol.In(["average", "min", "max"]),
            vol.Optional("state"): cv.string,
            vol.Optional("attribute"): cv.string,
            vol.Optional("above"): vol.Coerce(float),
            vol.Optional("below"): vol.Coerce(float),
        }
    ),
    _validate_motion_light_condition,
)

LUTRON_REMOTE_SCHEMA = vol.Schema(
    {
        vol.Required("id"): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("additional_actions", default={}): dict,
        vol.Optional("buttons", default={}): dict,
    }
)

TIMER_DURATION_SCHEMA = vol.Schema(
    {
        vol.Optional("off"): cv.string,
        vol.Optional("night_off"): cv.string,
    }
)

LINKED_MOTION_MAPPING_SCHEMA = vol.Schema(
    {
        vol.Required("local_scene"): cv.string,
        vol.Optional("remote_scene"): vol.Any(cv.string, None),
    }
)

LINKED_MOTION_ENTRY_SCHEMA = vol.Schema(
    {
        vol.Required("remote_area"): cv.string,
        vol.Required("default"): LINKED_MOTION_MAPPING_SCHEMA,
        vol.Optional("when_remote_scene", default={}): {cv.string: LINKED_MOTION_MAPPING_SCHEMA},
    }
)

AREA_SCHEMA = vol.Schema(
    {
        vol.Required("id"): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("enabled", default=True): cv.boolean,
        vol.Optional("event_handlers", default=False): cv.boolean,
        vol.Optional("icon"): cv.string,
        vol.Optional("special"): cv.string,
        vol.Optional("ambient_lighting_zone"): cv.string,
        # D3: per-area brightness step override (integer percentage)
        vol.Optional("brightness_step_pct"): vol.All(int, vol.Range(min=1, max=100)),
        # D6: per-area night-mode fade duration override
        vol.Optional("night_fadeout_seconds"): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional("circadian_switches", default=[]): vol.All(
            cv.ensure_list, [CIRCADIAN_SWITCH_SCHEMA]
        ),
        vol.Optional("lights", default=[]): vol.All(cv.ensure_list, [LIGHT_SCHEMA]),
        vol.Optional("light_clusters", default=[]): vol.All(cv.ensure_list, [LIGHT_SCHEMA]),
        vol.Optional("scenes", default=[]): vol.All(cv.ensure_list, [SCENE_SCHEMA]),
        vol.Optional("lutron_remotes", default=[]): vol.All(cv.ensure_list, [LUTRON_REMOTE_SCHEMA]),
        vol.Optional("motion_light_motion_sensor_ids"): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("motion_light_conditions", default=[]): vol.All(
            cv.ensure_list, [MOTION_LIGHT_CONDITION_SCHEMA]
        ),
        vol.Optional("motion_light_timer_durations", default={}): TIMER_DURATION_SCHEMA,
        vol.Optional("occupancy_light_sensor_ids"): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("occupancy_light_timer_durations", default={}): TIMER_DURATION_SCHEMA,
        vol.Optional("linked_motion", default=[]): vol.All(
            cv.ensure_list, [LINKED_MOTION_ENTRY_SCHEMA]
        ),
        # Dashboard fields (ignored by component, kept for config compatibility)
        vol.Optional("overview"): dict,
    }
)


def parse_config(raw: dict) -> AreaLightingConfig:
    """Parse validated config dict into typed dataclass models."""
    areas = []
    for area_raw in raw.get("areas", []):
        area_id = area_raw["id"]
        area_name = area_raw["name"]

        circadian_switches = [
            CircadianSwitchConfig(
                name=cs["name"],
                area_id=area_id,
                area_name=area_name,
                max_brightness=cs.get("max_brightness"),
                min_brightness=cs.get("min_brightness"),
            )
            for cs in area_raw.get("circadian_switches", [])
        ]

        lights = [
            LightConfig(
                id=light_raw["id"],
                circadian_switch=light_raw.get("circadian_switch"),
                circadian_type=light_raw.get("circadian_type"),
                roles=light_raw.get("roles", []),
                scenes=light_raw.get("scenes", []),
                members=light_raw.get("members", []),
            )
            for light_raw in area_raw.get("lights", [])
        ]

        light_clusters = [
            LightConfig(
                id=light_raw["id"],
                circadian_switch=light_raw.get("circadian_switch"),
                circadian_type=light_raw.get("circadian_type"),
                roles=light_raw.get("roles", []),
                scenes=light_raw.get("scenes", []),
                members=light_raw.get("members", []),
            )
            for light_raw in area_raw.get("light_clusters", [])
        ]

        scenes = [
            SceneConfig(
                slug=s["id"],
                name=s["name"],
                area_id=area_id,
                group_exclude=s.get("group_exclude", []),
                cycle=s.get("cycle"),
                entities=s.get("entities"),
                icon=s.get("icon"),
            )
            for s in area_raw.get("scenes", [])
        ]

        motion_conditions = [
            MotionLightCondition(
                entity_id=c.get("entity_id"),
                entity_ids=c.get("entity_ids"),
                aggregate=c.get("aggregate"),
                state=c.get("state"),
                attribute=c.get("attribute"),
                above=c.get("above"),
                below=c.get("below"),
            )
            for c in area_raw.get("motion_light_conditions", [])
        ]

        remotes = [
            LutronRemoteConfig(
                id=r["id"],
                name=r["name"],
                additional_actions=r.get("additional_actions", {}),
                buttons=r.get("buttons", {}),
            )
            for r in area_raw.get("lutron_remotes", [])
        ]

        linked_motion = []
        for lm_raw in area_raw.get("linked_motion", []):
            default_raw = lm_raw["default"]
            default = LinkedMotionMapping(
                local_scene=default_raw["local_scene"],
                remote_scene=default_raw.get("remote_scene"),
            )
            when_map = {}
            for scene_key, mapping_raw in lm_raw.get("when_remote_scene", {}).items():
                when_map[scene_key] = LinkedMotionMapping(
                    local_scene=mapping_raw["local_scene"],
                    remote_scene=mapping_raw.get("remote_scene"),
                )
            linked_motion.append(
                LinkedMotionConfig(
                    remote_area=lm_raw["remote_area"],
                    default=default,
                    when_remote_scene=when_map,
                )
            )

        areas.append(
            AreaConfig(
                id=area_id,
                name=area_name,
                enabled=area_raw.get("enabled", True),
                event_handlers=area_raw.get("event_handlers", False),
                icon=area_raw.get("icon"),
                special=area_raw.get("special"),
                ambient_lighting_zone=area_raw.get("ambient_lighting_zone"),
                brightness_step_pct=area_raw.get("brightness_step_pct"),
                night_fadeout_seconds=area_raw.get("night_fadeout_seconds"),
                circadian_switches=circadian_switches,
                lights=lights,
                light_clusters=light_clusters,
                scenes=scenes,
                lutron_remotes=remotes,
                motion_light_motion_sensor_ids=area_raw.get("motion_light_motion_sensor_ids"),
                motion_light_conditions=motion_conditions,
                motion_light_timer_durations=area_raw.get("motion_light_timer_durations", {}),
                occupancy_light_sensor_ids=area_raw.get("occupancy_light_sensor_ids"),
                occupancy_light_timer_durations=area_raw.get("occupancy_light_timer_durations", {}),
                linked_motion=linked_motion,
            )
        )

    return AreaLightingConfig(areas=areas)
