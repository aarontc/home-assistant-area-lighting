"""Constants for the Area Lighting integration."""

DOMAIN = "area_lighting"

# Scene slugs
SCENE_AMBIENT = "ambient"
SCENE_CHRISTMAS = "christmas"
SCENE_CIRCADIAN = "circadian"
SCENE_DAYLIGHT = "daylight"
SCENE_EVENING = "evening"
SCENE_HALLOWEEN = "halloween"
SCENE_NIGHT = "night"
SCENE_OFF = "off"
SCENE_OFF_INTERNAL = "off_internal"
SCENE_MANUAL = "manual"

HOLIDAY_SCENES = frozenset({SCENE_CHRISTMAS, SCENE_HALLOWEEN})
AMBIENT_SCENES = frozenset({SCENE_AMBIENT})
OFF_SCENES = frozenset({SCENE_OFF, SCENE_OFF_INTERNAL})

# Light roles
ROLE_COLOR = "color"
ROLE_DIMMING = "dimming"
ROLE_WHITE = "white"
ROLE_NIGHT = "night"
ROLE_MOVIE = "movie"
ROLE_CHRISTMAS = "christmas"
ROLE_PLANT = "plant"

ALL_ROLES = frozenset(
    {
        ROLE_COLOR,
        ROLE_DIMMING,
        ROLE_WHITE,
        ROLE_NIGHT,
        ROLE_MOVIE,
        ROLE_CHRISTMAS,
        ROLE_PLANT,
    }
)

# Circadian types
CIRCADIAN_CT = "ct"
CIRCADIAN_BRIGHTNESS = "brightness"
CIRCADIAN_RGB = "rgb"

# Remote button slugs
BUTTON_ON = "on"
BUTTON_OFF = "off"
BUTTON_RAISE = "raise"
BUTTON_LOWER = "lower"
BUTTON_FAVORITE = "favorite"

# Lutron subtype mapping (lutron uses "stop" for favorite)
LUTRON_SUBTYPE_MAP = {
    "on": BUTTON_ON,
    "off": BUTTON_OFF,
    "raise": BUTTON_RAISE,
    "lower": BUTTON_LOWER,
    "stop": BUTTON_FAVORITE,
}

# Default timer durations (seconds)
DEFAULT_MOTION_OFF_SECONDS = 480  # 8 minutes
DEFAULT_MOTION_NIGHT_OFF_SECONDS = 300  # 5 minutes
DEFAULT_OCCUPANCY_OFF_SECONDS = 1800  # 30 minutes

# Brightness adjustment step (D3).
# Integer-typed because HA's light.turn_on service schema for
# brightness_step_pct is int. 12 is rounded from 12.5%.
BRIGHTNESS_STEP_DEFAULT = 12
# Backwards-compat alias; new code should use BRIGHTNESS_STEP_DEFAULT.
BRIGHTNESS_STEP_PCT = BRIGHTNESS_STEP_DEFAULT

# Manual detection grace period (seconds after scene change)
MANUAL_DETECTION_GRACE_SECONDS = 4
# Manual detection brightness change threshold
MANUAL_DETECTION_BRIGHTNESS_THRESHOLD = 5

# Holiday mode entity
HOLIDAY_MODE_ENTITY = "input_select.holiday_mode"
HOLIDAY_MODE_NONE = "none"

# Ambient zone entities
AMBIENT_ZONE_ENTITY_PREFIX = "input_boolean.lighting_"
AMBIENT_ZONE_ENTITY_SUFFIX = "_ambient"

# Circadian daylight enabled entity
CIRCADIAN_DAYLIGHT_ENABLED_ENTITY = "input_boolean.lighting_circadian_daylight_lights_enabled"

# Global motion lighting enabled entity
GLOBAL_MOTION_LIGHT_ENABLED_ENTITY = "input_boolean.motion_light_enabled"

# Circadian kelvin routing
CIRCADIAN_KELVIN_HYSTERESIS = 25
DEFAULT_CIRCADIAN_KELVIN_CROSSFADE_SECONDS = 2.0

# Scene light attributes — the allowlist of per-light keys honored in a scene's
# `entities` block. Each of these is passed straight through to light.turn_on,
# so Home Assistant performs any needed color-mode conversion (e.g. rgbw_color
# on an rgbww-only bulb). This is the SINGLE source of truth: the config schema
# validates scene entity state against this same set, so an unsupported key
# (e.g. color_mode, or a typo) fails loudly at startup instead of being silently
# dropped at apply time. "state" is allowed in scene config too but handled
# separately (it selects turn_on vs turn_off rather than being a turn_on arg).
SCENE_LIGHT_ON_ATTRIBUTES = (
    "brightness",
    "color_temp_kelvin",
    "color_temp",
    "hs_color",
    "rgb_color",
    "rgbw_color",
    "rgbww_color",
    "xy_color",
    "effect",
)
