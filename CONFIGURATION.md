# Configuration Reference

This document is the complete specification of `area_lighting.yaml` — every
key, its type, validation rules, defaults, and runtime semantics. For the
rationale behind *why* a feature exists, see [`README.md`](README.md).

- Schema source: [`custom_components/area_lighting/config_schema.py`](custom_components/area_lighting/config_schema.py)
- Dataclass models (how keys normalize into Python state): [`custom_components/area_lighting/models.py`](custom_components/area_lighting/models.py)
- Role / scene / mode constants: [`custom_components/area_lighting/const.py`](custom_components/area_lighting/const.py)

---

## Top-level structure

`area_lighting` config lives under the `area_lighting:` key in
`configuration.yaml` (or a YAML package). It has exactly two things worth
configuring:

```yaml
area_lighting:
  areas: [...]            # required, list of areas
  alert_patterns: {...}   # optional, named flash/alert animations
```

| Key              | Type                         | Required | Description |
|------------------|------------------------------|----------|-------------|
| `areas`          | list of [area](#area)         | **yes**  | One entry per area/room managed by this integration. |
| `alert_patterns` | dict of [pattern](#alert-pattern) | no (default `{}`) | Named flash/alert animations invocable from remotes, automations, or the `area_lighting.alert` service. |

Any other top-level key is rejected (`vol.PREVENT_EXTRA`).

### Config structure at a glance

```
area_lighting:
  areas:
    - id: <slug>
      name: <display name>
      # flags, timings, integrations:
      enabled, event_handlers, icon, special, ambient_lighting_zone,
      brightness_step_pct, night_fadeout_seconds,
      leader_area_id, follow_leader_deactivation
      # sub-lists:
      circadian_switches: [...]
      lights:             [...]   # individual lights
      light_clusters:     [...]   # batch targets (e.g. Hue Zones)
      scenes:             [...]
      lutron_remotes:     [...]
      # motion / occupancy:
      motion_light_motion_sensor_ids:    [entity_id, ...]
      motion_light_conditions:           [...]
      motion_light_timer_durations:      { off, night_off }
      occupancy_light_sensor_ids:        [entity_id, ...]
      occupancy_light_timer_durations:   { off, night_off }
      # inter-area:
      linked_motion: [...]
  alert_patterns:
    <pattern_name>:
      steps: [...]
      delay, repeat, start_inverted, restore
```

---

## Area

One entry in the top-level `areas:` list. Every key below nests under an
entry in that list.

### Identity and flags

| Key                         | Type                  | Required | Default | Notes |
|-----------------------------|-----------------------|----------|---------|-------|
| `id`                        | string                | **yes**  | —       | Unique area slug. Used to form entity IDs (`scene.{id}_{slug}`, `switch.{id}_motion_light_enabled`, etc.) — keep it snake_case. |
| `name`                      | string                | **yes**  | —       | Human-readable label shown in the UI. |
| `enabled`                   | boolean               | no       | `true`  | `false` parses the area but creates no controller, entities, or handlers. |
| `event_handlers`            | boolean               | no       | `false` | Wires motion/occupancy/remote/external-change listeners. Typically `true` for live areas, `false` for test fixtures. |
| `icon`                      | string                | no       | —       | MDI icon name (e.g. `mdi:bedroom-outline`). |
| `special`                   | string                | no       | —       | Free-form marker used to exclude the area from bulk operations (e.g. `"global"`). |
| `ambient_lighting_zone`     | string                | no       | —       | Name of an `input_boolean.lighting_<zone>_ambient` helper that gates ambient activation for this area. |
| `brightness_step_pct`       | int, `1..100`         | no       | — (uses global default, 12) | Percentage per raise/lower button press. Override for areas that want finer or coarser steps. |
| `night_fadeout_seconds`     | float, `>= 0`         | no       | — (uses global default) | Per-area override for the night-mode fade transition duration. |
| `leader_area_id`            | string                | no       | —       | Slug of another area whose scene this area mirrors. Not chainable (see [leader/follower rules](#leaderfollower-rules)). |
| `follow_leader_deactivation`| boolean               | no       | `false` | When following a leader, also mirror its off/ambient transitions (default only mirrors active scenes). |

### `circadian_switches`

Declares the circadian-profile switches that the integration creates for
this area. Each entry becomes a `switch.circadian_lighting_{area_id}_{name}`
entity. Lights reference a profile by its `name`.

```yaml
circadian_switches:
  - name: Warm
    min_brightness: 20
    max_brightness: 100
```

| Key              | Type          | Required | Default | Notes |
|------------------|---------------|----------|---------|-------|
| `name`           | string        | **yes**  | —       | Short label (e.g. `Warm`, `Cool`). Referenced from `lights[].circadian_switch`. |
| `min_brightness` | int, `1..100` | no       | —       | Lower floor (percent) applied when the circadian profile drives brightness. |
| `max_brightness` | int, `1..100` | no       | —       | Upper cap (percent). |

`area_lighting` exposes the switches and the profile metadata; the actual
circadian updates come from an external integration (e.g. `circadian_lighting`)
that observes the switches.

### `lights` and `light_clusters`

Both lists use the same shape. `lights` is individual bulbs; `light_clusters`
is batch targets such as Hue Zones — listing a cluster alongside its members
lets the scene dispatcher coalesce per-light calls into one cluster call
when every member shares the same target state.

```yaml
lights:
  - id: light.bedroom_main
    roles: [dimming, color]
    circadian_switch: Warm
    circadian_type: ct

light_clusters:
  - id: light.hue_zone_bedroom
    members:
      - light.bedroom_main
      - light.bedroom_nightstand
```

| Key                | Type                                    | Required | Default | Notes |
|--------------------|-----------------------------------------|----------|---------|-------|
| `id`               | entity_id                               | **yes**  | —       | HA entity id of the light (or zone/group for clusters). |
| `roles`            | list of string                          | no       | `[]`    | Subset of `color`, `dimming`, `white`, `night`, `movie`, `christmas`, `plant`. Used for selective scene targeting. Unknown values rejected. |
| `scenes`           | list of string                          | no       | `[]`    | If non-empty, the light participates **only** in the listed scene slugs. Empty list = participates in all scenes. |
| `circadian_switch` | string                                  | no       | —       | Name of a circadian switch defined on this area. |
| `circadian_type`   | `ct` \| `brightness` \| `rgb`           | no       | —       | How circadian control is applied. Only meaningful together with `circadian_switch`. |
| `members`          | list of entity_id                       | no       | `[]`    | Populated only on `light_clusters` entries. Lists the physical lights inside the cluster. |

#### Light roles

Valid role names (from `const.py`):

| Role       | Typical use |
|------------|-------------|
| `color`    | Color-capable bulb. |
| `dimming`  | Brightness-capable bulb. |
| `white`    | White/CT-only. |
| `night`    | Low-key / dim-friendly fixture (used by `night` scene). |
| `movie`    | Participates in the `movie` scene. |
| `christmas`| Participates in the `christmas` scene. |
| `plant`    | Grow-light / plant fixture. |

A light can carry any combination of roles.

### `scenes`

Each scene declared here produces a `scene.{area_id}_{id}` entity.

```yaml
scenes:
  - id: reading
    name: Reading
    icon: mdi:book-open
    entities:
      light.bedroom_main:
        brightness: 255
        color_temp_kelvin: 4000
  - id: off
    name: Off
```

| Key             | Type                      | Required | Default | Notes |
|-----------------|---------------------------|----------|---------|-------|
| `id`            | string (slug)             | **yes**  | —       | Scene slug — unique within the area. Builds the entity id. |
| `name`          | string                    | **yes**  | —       | Display label. |
| `icon`          | MDI icon                  | no       | —       | Validated by `cv.icon`. |
| `group_exclude` | list of entity_id         | no       | `[]`    | Lights that should **not** be affected when this scene activates. |
| `cycle`         | list of scene slugs       | no       | —       | Defines a favorite-button cycle sequence. Parsed but not yet wired to the on-button cycler (tracked in `TODO.md`). |
| `entities`      | free-form dict            | no       | —       | Per-light target state (`brightness`, `rgb_color`, `color_temp_kelvin`, …). Same shape HA uses for its own scene entities. |

### `lutron_remotes`

Pico remotes associated with this area.

```yaml
lutron_remotes:
  - id: bedroom_bedside
    name: Bedside Pico
    buttons:
      favorite:
        - reading
        - night
```

| Key                  | Type                                         | Required | Default | Notes |
|----------------------|----------------------------------------------|----------|---------|-------|
| `id`                 | string                                       | **yes**  | —       | Matches the remote identifier used by `lutron_caseta` events. |
| `name`               | string                                       | **yes**  | —       | Human label for logs/UI. |
| `additional_actions` | dict                                         | no       | `{}`    | Free-form map of extra button→action hooks consumed by `event_handlers.py`. Not validated by this schema. |
| `buttons`            | dict                                         | no       | `{}`    | Per-button overrides (currently only `favorite`). |

#### `buttons.favorite` override

Three forms are accepted; one is chosen per remote:

1. **Single scene slug** — always activates that area scene on favorite.
   ```yaml
   buttons: { favorite: night }
   ```
2. **List of scene slugs** — cycles through them on repeated presses.
   ```yaml
   buttons:
     favorite: [reading, night]
   ```
3. **Home Assistant scene entity id** — fires `scene.turn_on` on an external scene. Must be a single value (cannot appear inside a cycle list).
   ```yaml
   buttons: { favorite: scene.my_custom_reading }
   ```

Bare slugs are validated at parse time against the owning area's `scenes` list; unknown slugs raise `vol.Invalid`. If `buttons.favorite` is unset, the default holiday/night cycling logic in `scene_machine.py` applies.

### Motion lighting

```yaml
motion_light_motion_sensor_ids:
  - binary_sensor.bedroom_motion
motion_light_conditions:
  - entity_id: input_boolean.motion_light_enabled
    state: "on"
  - entity_ids:
      - sensor.patio_illuminance
      - sensor.garden_illuminance
    aggregate: average
    below: 100
motion_light_timer_durations:
  off: "00:08:00"
  night_off: "00:05:00"
```

| Key                                | Type                                       | Required | Default | Notes |
|------------------------------------|--------------------------------------------|----------|---------|-------|
| `motion_light_motion_sensor_ids`   | list of entity_id                          | no       | —       | Absence disables motion lighting for the area. |
| `motion_light_conditions`          | list of [condition](#motion-light-condition) | no    | `[]`    | All conditions must hold for motion to activate lights. |
| `motion_light_timer_durations.off` | HA duration string                         | no       | 480 s   | Off delay after motion stops (day/normal). |
| `motion_light_timer_durations.night_off` | HA duration string                   | no       | 300 s   | Off delay during night mode. |

Duration strings accept any form `cv.string` allows HA to parse — typically
`HH:MM:SS` or plain seconds. Defaults live in `const.py`
(`DEFAULT_MOTION_OFF_SECONDS`, `DEFAULT_MOTION_NIGHT_OFF_SECONDS`).

#### Motion light condition

Each entry checks either a single entity or an aggregate over several.

| Key          | Type                              | Required | Default | Notes |
|--------------|-----------------------------------|----------|---------|-------|
| `entity_id`  | entity_id                         | either   | —       | Single-entity check. Mutually exclusive with `entity_ids`. |
| `entity_ids` | list of entity_id (≥ 1)           | either   | —       | Multi-entity aggregation. Requires `aggregate`. Cannot be used with `state`. |
| `aggregate`  | `average` \| `min` \| `max`       | required with `entity_ids` | — | Reduction over `entity_ids` before `above`/`below` comparison. |
| `state`      | string                            | no       | —       | Expected state (single-entity only). |
| `attribute`  | string                            | no       | —       | Attribute to read instead of state. |
| `above`      | float                             | no       | —       | Passes when value is strictly `>` this. |
| `below`      | float                             | no       | —       | Passes when value is strictly `<` this. |

Enforcement (from `_validate_motion_light_condition`):

- Exactly one of `entity_id` / `entity_ids` must be present.
- `entity_ids` implies `aggregate` (required) and disallows `state`.

### Occupancy lighting

Same shape as motion lighting but for the longer-window "someone is still
in the area" timer. No conditions list — occupancy is simpler.

| Key                                    | Type                    | Required | Default | Notes |
|----------------------------------------|-------------------------|----------|---------|-------|
| `occupancy_light_sensor_ids`           | list of entity_id       | no       | —       | Absence disables occupancy lighting. |
| `occupancy_light_timer_durations.off`  | HA duration string      | no       | 1800 s  | Default lives in `DEFAULT_OCCUPANCY_OFF_SECONDS`. |
| `occupancy_light_timer_durations.night_off` | HA duration string | no       | 1800 s  | Uses the same default. |

The `switch.{area_id}_occupancy_timeout_enabled` entity created by the
integration gates whether this timer starts at all.

### `linked_motion`

Couples motion in one area to scene activation in this area (used, for
example, to light the hallway when the bedroom wakes up).

```yaml
linked_motion:
  - remote_area: bedroom
    default:
      local_scene: circadian
      remote_scene: null
    when_remote_scene:
      night:
        local_scene: night
        remote_scene: night
```

| Key                 | Type                                        | Required | Default | Notes |
|---------------------|---------------------------------------------|----------|---------|-------|
| `remote_area`       | string                                      | **yes**  | —       | `id` of the area whose motion drives this link. |
| `default`           | [linked mapping](#linked-motion-mapping)    | **yes**  | —       | Applied when the remote area's scene doesn't match any `when_remote_scene` entry. |
| `when_remote_scene` | `{scene_slug: mapping}`                     | no       | `{}`    | Scene-specific override mappings. |

#### Linked motion mapping

| Key            | Type                | Required | Default | Notes |
|----------------|---------------------|----------|---------|-------|
| `local_scene`  | string              | **yes**  | —       | Scene slug to activate in this area. |
| `remote_scene` | string \| `null`    | no       | `null`  | Scene slug to force on the remote area. `null` leaves the remote alone. |

### Leader/follower rules

The leader/follower pair (`leader_area_id` + `follow_leader_deactivation`)
is validated after all areas are parsed. The rules (from
`validate_leader_follower_graph`):

1. An area cannot lead itself.
2. `leader_area_id` must reference an existing area.
3. **No chaining** — if area B follows area A, area A cannot also follow
   anyone. This keeps leader/follower relationships at exactly one level
   deep.

Violations raise `vol.Invalid` with a precise message at startup.

---

## Alert pattern

Top-level `alert_patterns:` is a dict keyed by pattern name. Each value is
an animation description. Patterns are invoked via the
`area_lighting.alert` service or from remote handlers.

```yaml
alert_patterns:
  alarm_flash:
    repeat: 10
    restore: true
    steps:
      - target: all
        state: on
        brightness: 255
        rgb_color: [255, 0, 0]
      - target: all
        state: off
        delay: 0.5
```

### Pattern-level

| Key              | Type                | Required | Default | Notes |
|------------------|---------------------|----------|---------|-------|
| `steps`          | list of [step](#alert-step) (≥ 1) | **yes** | — | The animation itself. |
| `delay`          | float ≥ 0           | no       | `0.0`   | Wait this long before the first step (seconds). |
| `repeat`         | int ≥ 1             | no       | `1`     | Number of times to cycle the step list. |
| `start_inverted` | boolean             | no       | `false` | Start the first cycle with state flipped — useful for alternating-off/on blinks. |
| `restore`        | boolean             | no       | `true`  | Return lights to their pre-alert state at the end. |

### Alert step

| Key                | Type                                  | Required | Default | Notes |
|--------------------|---------------------------------------|----------|---------|-------|
| `target`           | `all` \| `color` \| `white`           | **yes**  | —       | Which subset of lights this step touches. |
| `state`            | `on` \| `off`                         | **yes**  | —       | Turn lights on or off for this step. |
| `delay`            | float ≥ 0                             | no       | `0.0`   | Seconds between the previous step and this one. |
| `brightness`       | int, `0..255`                         | no       | —       | Only meaningful when `state: on`. |
| `rgb_color`        | `[R, G, B]` (three ints)              | no       | —       | Colour. One of `rgb_color` / `hs_color` / `xy_color` at most. |
| `hs_color`         | `[H, S]` (floats)                     | no       | —       | Alternative to `rgb_color`. |
| `xy_color`         | `[X, Y]` (floats)                     | no       | —       | Alternative to `rgb_color`. |
| `color_temp_kelvin`| int, `1000..10000`                    | no       | —       | For white/CT targets. |
| `transition`       | float ≥ 0                             | no       | —       | Fade duration (seconds). |

---

## Worked example

A small but realistic two-area setup showing the common shapes:

```yaml
area_lighting:
  areas:
    - id: bedroom
      name: Bedroom
      event_handlers: true
      icon: mdi:bed
      brightness_step_pct: 8

      circadian_switches:
        - name: Warm
          min_brightness: 20
          max_brightness: 100

      lights:
        - id: light.bedroom_main
          roles: [dimming, color]
          circadian_switch: Warm
          circadian_type: ct
        - id: light.bedroom_nightstand
          roles: [color, night]

      light_clusters:
        - id: light.hue_zone_bedroom
          members:
            - light.bedroom_main
            - light.bedroom_nightstand

      scenes:
        - id: circadian
          name: Circadian
          icon: mdi:white-balance-sunny
        - id: reading
          name: Reading
          icon: mdi:book-open
          entities:
            light.bedroom_main:
              brightness: 255
              color_temp_kelvin: 4000
        - id: night
          name: Night
          icon: mdi:weather-night
          entities:
            light.bedroom_nightstand:
              brightness: 40
              rgb_color: [255, 80, 0]
        - id: off
          name: Off

      lutron_remotes:
        - id: bedroom_bedside
          name: Bedside Pico
          buttons:
            favorite: [reading, night]

      motion_light_motion_sensor_ids:
        - binary_sensor.bedroom_motion
      motion_light_conditions:
        - entity_id: input_boolean.motion_light_enabled
          state: "on"
      motion_light_timer_durations:
        off: "00:08:00"
        night_off: "00:04:00"

      occupancy_light_sensor_ids:
        - binary_sensor.bedroom_occupancy
      occupancy_light_timer_durations:
        off: "00:30:00"

    - id: hallway
      name: Hallway
      event_handlers: true

      lights:
        - id: light.hallway
          roles: [dimming, white]

      scenes:
        - id: circadian
          name: Circadian
        - id: night
          name: Night
        - id: off
          name: Off

      # When the bedroom senses motion and goes to `night`, follow it.
      linked_motion:
        - remote_area: bedroom
          default:
            local_scene: circadian
          when_remote_scene:
            night:
              local_scene: night

  alert_patterns:
    doorbell_flash:
      repeat: 3
      restore: true
      steps:
        - target: all
          state: on
          brightness: 255
          rgb_color: [0, 128, 255]
        - target: all
          state: off
          delay: 0.4
```

---

## Validation and error reporting

- Schema errors surface at Home Assistant startup as `Invalid config for [area_lighting]` log entries with the specific field and rule that failed.
- Post-parse semantic errors (unknown favorite slugs, leader/follower graph issues) surface the same way, carrying the offending area id and the reason.
- `area_lighting.reload` reloads `configuration.yaml` without a full HA restart; the same validators run.

## What's *not* configured here

These are runtime-adjustable entities the integration creates — set them via
the UI, services, or standard HA helpers, not here:

- `switch.{area_id}_motion_light_enabled` — per-area motion lighting toggle.
- `switch.{area_id}_motion_override_ambient` — suppress ambient scenes during motion.
- `switch.{area_id}_occupancy_timeout_enabled` — per-area occupancy-timer gate.
- `number.{area_id}_manual_fadeout_seconds` / `number.{area_id}_motion_fadeout_seconds` / `number.{area_id}_motion_timeout_minutes` / `number.{area_id}_motion_night_timeout_minutes` / `number.{area_id}_occupancy_timeout_minutes` / `number.{area_id}_occupancy_night_timeout_minutes` — live-tunable timer overrides.

The YAML defaults (`brightness_step_pct`, `night_fadeout_seconds`, the
`*_timer_durations` maps) seed these entities; after startup the entities
themselves are the source of truth.
