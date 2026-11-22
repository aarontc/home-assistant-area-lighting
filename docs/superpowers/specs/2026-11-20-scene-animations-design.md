# Scene animations

An animated scene changes its lights' colours on a fixed period, from a
palette, in step across every area. It is for holiday scenes (a slow fade
between red, green and white for christmas) and for a fast party scene. The
house this was built for has 101 colour bulbs in 21 christmas scenes: 85 Hue
bulbs on two bridges, 14 WiZ bulbs, one Z-Wave RGBW controller and one MQTT
light.

Why inside area_lighting: an automation that recolours an area's lights while
the area is in a scene looks like a person changing them. Self-heal changes
them back for about a minute, then the area latches `manual`, cancels its
motion timers and raises a Repairs issue. Hue dynamic scenes reach only Hue
bulbs, run separately on each bridge, and drop a bulb from the effect on any
command area_lighting sends.

## Configuration

A top-level `animations:` block holds named animations, the way
`alert_patterns:` holds alerts. A scene lists the animations it may run.

```yaml
animations:
  christmas_step:          # new colours every 30 s, each fading over 20 s
    period: 30
    transition: 20
    palette:
      - hs_color: [0, 100]
      - hs_color: [128, 100]
  christmas_drift:         # the fade lasts the whole period, so the lights never stop moving
    period: 60
    transition: 60
    palette:
      - hs_color: [0, 100]
      - hs_color: [128, 100]
      - color_temp_kelvin: 5000
        brightness: 120
        weight: 1
  party:
    period: 2
    transition: 0.5
    palette:
      - hs_color: [0, 100]
      - hs_color: [40, 100]
      - hs_color: [120, 100]
      - hs_color: [180, 100]
      - hs_color: [240, 100]
      - hs_color: [300, 100]

areas:
  - id: kitchen
    scenes:
      - id: christmas
        name: Christmas
        animations: [christmas_step, christmas_drift]
        entities:
          light.kitchen_overhead_up: {state: "on", brightness: 255, hs_color: [0, 100]}
          light.kitchen_sink:        {state: "on", brightness: 200, color_temp_kelvin: 2700}
          light.kitchen_island:      {state: "on", brightness: 255, hs_color: [0, 100], animate: false}
```

### `animations.<name>`

| key | type | rule |
| --- | --- | --- |
| `period` | number, seconds | required, at least 1 |
| `transition` | number, seconds | optional, default `period`; from 0 to `period` |
| `palette` | list | required, at least one entry |

Animation names follow the scene id rule: lowercase letters and digits
separated by single underscores. `off` is reserved, because it is the
select option that turns animations off.

### Palette entries

Each entry has exactly one colour key: `hs_color`, `rgb_color`, `xy_color` or
`color_temp_kelvin`. It may add `brightness` (1 to 255) and `weight` (an
integer, at least 1, default 1). Any other key fails validation.

### Scenes

- `animations` is an optional list of animation names. Each must exist in
  the top-level block, and none may repeat.
- A scene with `animations` must have an `entities` map. The role-based
  skeleton has no colours to replace.
- An entity stanza may set `animate: false`. It is accepted only in a
  scene with `animations`.

## Which lights animate

A light in the scene's `entities` map animates when all of these hold:

- its stanza has `state: "on"`
- its stanza sets a colour key other than colour temperature: `hs_color`,
  `rgb_color`, `rgbw_color`, `rgbww_color` or `xy_color`
- it does not set `animate: false`
- it is not one of the area's `light_clusters`
- when the stanza carries `color_mode`, the mode is `hs`, `rgb`, `rgbw`,
  `rgbww` or `xy`. A snapshot records `hs_color` for a light in
  `color_temp` mode, and such a light stays white.

Every other light keeps its scene state. A light whose stanza sets
`color_temp_kelvin` or mireds `color_temp` therefore stays a useful white
light. So does any dimmer, switch or other light without colour. To keep a
coloured bulb at its own colour, set `animate: false`.

## Colours on each step

The step number is `floor(unix_time / period)`, from UTC epoch seconds. Every
area running the same animation is on the same step at the same moment, and
an area that starts mid-period joins the current step.

The palette expands into a slot list. Each entry fills `weight` slots, and
the slots are interleaved by smooth weighted round robin, so the weights
`red 2, green 2, white 1` give `red, green, red, green, white`. The expansion
is deterministic.

The animated lights are numbered from 0 in the order the `entities` map lists
them. Light `i` on step `s` takes slot `(i + s) mod len(slots)`. Each step
rotates the whole assignment by one slot. Over an area the colours stay in
proportion to the weights, and every light moves on to the next slot each
step. With equal weights, bulbs listed one after the other never share a
colour. Unequal weights put the heavier colour in neighbouring slots, so
neighbours can share it.

A light's target on a step is its scene stanza with every colour key
(`hs_color`, `rgb_color`, `rgbw_color`, `rgbww_color`, `xy_color`,
`color_temp_kelvin`, `color_temp`) and `color_mode` removed, then the slot's
colour key added. The slot's `brightness` replaces the stanza's when set,
and otherwise the stanza's brightness stays. Every other key of the stanza,
such as `effect`, stays as it is.

## Choosing the animation

Each scene id that any enabled area gives `animations` gets one global select
entity, `select.area_lighting_<scene id>_animation`, named
`<Scene name> Animation`. It uses the name of the first area's scene with
that id. Its options are `off` followed by every animation that any area
lists for that scene id, in the order the config first names them.

- The selected option applies to every area with that scene id. An area
  whose scene does not list the selected animation shows its static scene.
  `off` shows every area's static scene.
- With nothing persisted, the select starts on the first animation that the
  first area with that scene id lists.
- The choice persists in the global state store, next to the master
  toggles, and survives restarts.
- After a reload, a persisted option the new config no longer offers falls
  back to the default. A select whose scene id no longer has animations is
  removed.

When the option changes, every area currently in that scene, not dimmed and
not under an alert, whose effective animation changed, applies the new
choice at once. An area whose scene does not list either the old or the new
option is left alone. An animation applies its
current step with the animation's `transition` and starts its ticks. `off`
applies the static scene with the default scene transition.

## Running

An area runs its scene's selected animation while all of these hold:

- the area is in that scene, not `manual`, `off` or circadian
- the area is not dimmed
- no alert is running in the area
- the controller is open

The ticks are scheduled for each step boundary, `(s + 1) * period` in UTC.
On each tick the controller:

1. Resolves the scene's targets with the current step's colours through the
   usual demand-response filter (`_effective_scene_targets`) and replaces
   `_active_scene_targets` and the shed set.
2. Sends `light.turn_on` with the animation's `transition` to each animated
   light whose target is on and not shed. It sends nothing to the lights
   that keep their scene state, and nothing to cluster entities.
3. Marks the targets of the lights it commanded, before it assigns them to
   `_active_scene_targets` and before its first await, with `commanded_at`
   and `animation_from`. `animation_from` is the light's previous on-target:
   its colour key and brightness from the target it replaces. The other
   lights keep their targets and stamps unchanged.

A tick that comes due while the previous tick's commands are still being
sent is skipped, and logged at debug. A tick from an animation run that has
since stopped does nothing; use a generation counter as the circadian
adjuster does (`_circadian_generation`). A tick does not schedule the
post-settle self-check, and the post-settle self-check skips a target a
tick stamped while its `commanded_at` plus `transition` plus the grace is
still ahead. Targets stamped by an activation or a heal are checked as
before.

### Manual detection on animated lights

A person's change to an animated light latches the area `manual`, which
stops the animation, whether or not scene self-heal is on.

The usual per-light transition window cannot do this. Ticks restamp every
animated light each period, so with `transition` close to `period` the
window never closes, and with self-heal on, every divergence falls inside
the glitch window and is healed instead. For a target that carries
`animation_from`, manual detection therefore checks the fade's colour path,
with a timed exception for desaturated endpoints:

Each tick also keeps `animation_from_previous`, the `animation_from` of the
target it replaces, so a late echo of the command before last is still
recognised.

A report is the animation's own when the light is on, its brightness lies
between the lowest and highest brightness of `animation_from_previous`,
`animation_from` and the target, with the usual tolerance of 10 on each
side, and its reported `xy_color` lies within 0.03 of the segment from
`animation_from` to the target. Within `MANUAL_DETECTION_GRACE_SECONDS`
after `commanded_at`, when a late echo of the command before can still
arrive, the segment from `animation_from_previous` to `animation_from`
counts too, and so does its brightness.

When either endpoint carries `color_temp_kelvin` or `color_temp`, or has
saturation below 90, accept any colour during that segment's fade window,
provided the light is on and its brightness passes the same range check.
Convert `xy_color`, `rgb_color`, `rgbw_color` and `rgbww_color` to hs to
check saturation; use `hs_color` directly. A target without a colour key
does not qualify. The current segment's window runs from `commanded_at`
until `commanded_at + transition + MANUAL_DETECTION_GRACE_SECONDS`, using
the target's stored `transition`. The previous segment qualifies only
within `MANUAL_DETECTION_GRACE_SECONDS` after `commanded_at`.

Hue fades through white follow a curved path: live 15-second fades between
5000 K and red reported points 0.092 to 0.124 from the straight xy segment,
while saturated red-to-green fades stayed within 0.026. A white xy-to-red
fade deviated by 0.052 and green-to-5000 K by 0.057, so a straight colour
corridor cannot predict these reports.

After each window, the existing colour rule applies again, and a
segment between two saturated colours uses it throughout. A recovery from
`unavailable` or `unknown`, and a light whose target is off, never get the
relaxed rule. Nothing rechecks a light when a window closes, so a person's
colour-only change during a fade to or from white is caught only if the
bulb reports again after the window. Usually the next step overwrites it
instead of latching `manual`.

- Convert `hs_color` with `color_hs_to_xy`, `rgb_color` with
  `color_RGB_to_xy`, `rgbw_color` and `rgbww_color` through their rgb form,
  and `color_temp_kelvin` through `color_temperature_to_rgb`.
- A Hue bridge reports the point it clamped into the bulb's gamut, not the
  colour it was sent. Test each segment four times: with its endpoints as
  converted, and with both endpoints clamped to Hue gamut A, B and C
  (`get_closest_point_to_point` in `homeassistant.util.color`, with the
  published gamut triangles as constants in `animation.py`). A report near
  any of the four passes.
- When the report carries no `xy_color`, or a colour cannot be converted,
  on plus brightness in range is still enough inside a desaturated
  segment's window. Otherwise fall back to `state_matches_scene_target`
  against each endpoint.

Then:

- A report that is the animation's own is skipped.
- A report that turns the light off, or is not the animation's own, latches
  `manual`, at any time, including the first `MANUAL_DETECTION_GRACE_SECONDS`
  after a tick's command. The scene-change grace, measured from the
  activation, still applies to animated targets, because it covers the
  activation's own staged reports, such as a Hue bulb reporting on at its
  old level before the new colour. It never goes to the glitch-window heal.
- When that report leaves every light in the area off, the area goes
  through the all-off handling at once. The settle wait in
  `lights_settling_until` ignores tick stamps, so a room turned off by a
  person mid-fade reads `off`, not `manual` for the rest of the fade.
- A recovery from `unavailable` or `unknown` never latches. With self-heal
  on it heals; with self-heal off it is ignored, and the next tick recolours
  the bulb.

Targets without `animation_from`, including every light right after an
activation, use the existing rules.

### Leaving the scene

`_stop_animation` removes `animation_from` and `animation_from_previous`
from every target and keeps `commanded_at` and `transition`, so a late echo
of the last tick during the area's own turn-off still gets its transition
window. Tick stamps carry their own marker key, which stays, so
`lights_settling_until` and the post-settle self-check can still tell them
from activation and heal stamps. Every path that leaves the scene calls it before its
first await. The area's own turn-off (`off_internal`, a motion or occupancy
timer, ambience or holiday off, Lights Out, a follower deactivation) is
therefore judged by the existing rules, which skip off reports, and never
latches `manual`.

### Failures

A `light.turn_on` that raises must not leave an area without ticks. An
activation logs each failed light, as a tick does, and starts the ticks in
a `finally` when the area is still in the animated scene. The reload and
startup loops that re-apply animated areas wrap each controller in its own
`try`, so one area's failure neither aborts the reload nor skips the areas
after it, circadian areas included.

### Starting

Every path that puts an area into an animated scene shows the current step's
colours on its first command. There is no flash of the static colours first.
These paths are `_run_activation`, a `scene.turn_on` of the scene entity
(`scene.py`), `handle_scene_activated`, the demand-response re-drive, a
leader driving a follower, and a reload that leaves the area in the scene.
So does setting `select.<area>_last_scene` to the scene. Each one starts the
ticks. Resolve the animation overlay inside target resolution
(`_resolve_raw_scene_targets` and `_apply_scene_data`), and have the scene
entity apply the same resolved entities, so that every path agrees.

An activation keeps its own transition: the caller's, or the default scene
transition when there is none. Motion lighting an animated scene from off
therefore fades up in 0.5 seconds, not over the animation's 60. Only ticks
and the re-apply after a select change or a reload use the animation's
`transition`.

While an animation is selected, an activation sends nothing to cluster
entities, in `_apply_scene_data` and in the scene entity alike, even when a
stored snapshot or the `entities` map lists one. A cluster command carrying
static colours would race the members' step colours.

The first tick is scheduled from the step the activation applied, at
`(s + 1) * period`. If the activation's commands took so long that the
boundary has passed, the tick runs at once.

### Stopping

The ticks stop when the area leaves the scene for any reason, latches
`manual`, turns off, becomes dimmed, starts an alert, reloads or shuts down.
They restart when an alert ends with the area still in the scene, when
`lighting_on` clears dimmed, and when a reload keeps the area in the scene.

### Stored snapshots

`snapshot_scene` refuses a scene with `animations`, the way it refuses while
demand response is shedding, because a snapshot would hold one step's
colours. If a snapshot already exists for a scene that gains `animations`,
the overlay applies to the snapshot's entities by the same rules.

## Limits

- Lights that do not support transitions jump to each step's colour. Home
  Assistant drops the `transition` argument for them, so nothing breaks. In
  this house that is the 14 WiZ bulbs.
- A Hue bridge accepts roughly ten light commands a second. A 2 second party
  animation over 45 bulbs on one bridge outruns it, and steps arrive late.
  The skip rule keeps ticks from piling up behind it. Fast animations suit a
  room or two.
- Each area computes its own step from the clock, so areas agree on the step
  but their commands reach the bulbs at whatever latency each radio has.

## Diagnostics

`diagnostic_snapshot()` gains, per area, `animation`: the running animation's
name and current step, or `null`.

## Tests

Unit tests:

- slot expansion and interleaving for equal and unequal weights
- rotation: the colour counts over an area stay in proportion, and each light
  moves on one slot per step
- target building: colour keys and `color_mode` replaced, brightness from the
  palette or the stanza, other keys kept
- light selection: colour-temperature lights, `animate: false`, off lights and
  clusters stay out
- schema: every validation rule above, with a message that names the area,
  scene and key

Integration tests (pytest-homeassistant-custom-component, with time moved by
`async_fire_time_changed` or the freezer):

- activation sends the current step's colours, and each boundary sends the
  next step with the animation's transition
- two areas in the same animated scene send matching steps at the same
  boundary
- a person's change to an animated light latches `manual` and stops the ticks
- demand response: shed bulbs receive nothing on any tick and stay off
- the select: switching to `off` restores the static scene, switching to
  another animation applies its step, and the choice survives a restart
- a reload keeps a running animation going under the new config
- an alert pauses the ticks and they resume after it
- dimmed pauses the ticks and `lighting_on` resumes them
- activating `scene.<area>_<scene>` sends step colours, not static ones
- `snapshot_scene` refuses an animated scene
- a tick that comes due during a slow send is skipped
