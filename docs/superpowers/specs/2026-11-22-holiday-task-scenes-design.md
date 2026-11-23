# Holiday task scenes

A holiday scene such as `christmas` does two jobs today. It is the ambience an
area shows while the zone's ambient lighting is on, and it is what motion, the
on button and favorite light for someone who walked in to work. A scene of red
and green bulbs is right for the first and too dark and too coloured for the
second, such as cooking in a kitchen.

This change splits the two roles and lets a visual scene hold some of its
lights on the circadian curve:

- `<holiday>_ambient`, such as `christmas_ambient`, is the ambient role.
- `<holiday>` keeps the task role. It can mark lights `circadian: true`, so a
  room in `christmas` keeps red and green highlights with enough white light
  from bulbs that follow the curve: 2700 K at night, 5600 K at solar noon, and
  each group's brightness bounds.

An area without `<holiday>_ambient` uses `<holiday>` for both roles, exactly
as it does now. Areas adopt the split one at a time.

Why inside area_lighting: a light that follows the curve inside another scene
has to be driven by the circadian adjuster. Anything outside the integration
that sends it colour temperatures every minute looks like a person changing
it, so self-heal reverts it and the area latches `manual`.

## Configuration

```yaml
areas:
  - id: kitchen
    scenes:
      - id: christmas_ambient          # the old christmas scene, unchanged
        entities:
          light.kitchen_overhead_down: {state: "on", brightness: 255, hs_color: [128, 100]}
          ...
      - id: christmas                  # the task scene
        entities:
          light.kitchen_overhead_down: {circadian: true}
          light.kitchen_overhead_up: {circadian: true}
          light.kitchen_cabinet_fridge_up: {state: "on", brightness: 255, hs_color: [0, 100]}
          ...
```

### The `circadian` stanza key

- `circadian: true` means the light follows the curve while the scene is
  active, using its own `circadian_group` and `circadian_type`, the group's
  bounds and sleep options, and the global minimum brightness reduction. It is
  sent exactly what the `circadian` scene would send it.
- The schema fills in `state: "on"`. Every other reader of a stanza then sees
  an on target, and `scene.turn_on` on the scene entity cannot read a missing
  `state` as off. Filling it in is idempotent: running the schema twice gives
  the same result.
- `circadian: false` is rejected with "delete the key". It is the default.
- `circadian: true` cannot be combined with `state: "off"`, `brightness`,
  `effect`, `color_temp`, `color_temp_kelvin`, any colour key, or `animate`.
  Such a light never animates, so `animate: false` adds nothing.
- These checks run in the area pre-pass, so errors name the area and the
  scene, like the `animate` errors do.

After parsing, `validate_scene_circadian_lights` rejects a `circadian: true`
stanza whose entity:

1. is not a light declared in the area,
2. has no `circadian_group` (the existing rules already require the group and
   the type together and require the group to exist),
3. is a `light_clusters` id, because the cluster would drive its members twice
   and clusters already drop out of demand response, or
4. is a light in `circadian_kelvin_routes`, because the router runs only in
   the `circadian` scene.

The validator runs after `validate_circadian_groups`, at setup and at reload.
A reload that fails it raises and keeps the running configuration.

### `<holiday>_ambient` scenes

- `christmas_ambient` and `halloween_ambient` pass the scene id rule already.
  Each gets its scene entity, `scene.<area>_christmas_ambient`, and a
  `last_scene` option.
- An area that defines `<holiday>_ambient` must define `<holiday>`. Without
  that rule, an area with only the ambient scene would see the holiday as
  `none` and never light it.
- `buttons.on` sequences may not list `<holiday>_ambient`, as they may not
  list `<holiday>`. The on button never places an ambient role. Favorite
  cycles and `linked_motion` may name it.
- A `<holiday>_ambient` scene that lists `animations` gets its own animation
  select, `select.area_lighting_christmas_ambient_animation`, separate from the
  `christmas` one.
- `<holiday>_ambient` gets the same icon as `<holiday>`.

## Roles

"Split" below means the area defines `<holiday>_ambient` for the active
holiday. In an unsplit area both roles resolve to `<holiday>` and nothing
changes.

| Path | Role | Split area | Unsplit area |
| --- | --- | --- | --- |
| Motion from off, on button from off or `manual`, favorite | task | `christmas` | `christmas` |
| On button from `christmas` | task | circadian, as today | same |
| On button or favorite from `christmas_ambient` | task | `christmas` | n/a |
| `input_select.ambient_scene: holiday` when ambience turns on | ambient | `christmas_ambient` | `christmas` |
| Off or timer fade with ambience on and `ambient_scene: holiday` | ambient | `christmas_ambient` | `christmas` |
| `holiday_mode` changes while the area is off | ambient | `christmas_ambient` | `christmas` |

### Ambient-like

`ambient`, every `<holiday>_ambient`, and `<holiday>` in an unsplit area are
ambient-like. In a split area `christmas` is an ordinary lit scene. The set is
per area, computed from the area's scene ids, so a reload updates it.
`AreaConfig.ambient_like_slugs` holds it, and `AreaConfig.holiday_ambient_slug(h)`
returns `<h>_ambient` when the area has it and `h` otherwise.
`AreaState.is_ambient_like` moves to a controller method that reads it, so
restored state never carries a stale set.

Every site that uses the fixed set changes to the per-area one: the leader
reason in `_activate_scene`, Scenario A in `lighting_on`, the follower guard in
`handle_leader_change`, the three occupancy timer sites, and `motion_may_light`.

What changes for a split area:

- **Motion.** Motion in `christmas_ambient` steps up to `christmas` when
  `motion_light_override_ambient` is on, as it steps up from `ambient` today.
  Motion in `christmas` does nothing, like any other lit scene.
- **Occupancy.** `christmas` arms the occupancy timer, and its expiry fades
  the area through the off rules below. `christmas_ambient` cancels it.
- **Leaders.** A leader's `christmas` propagates to followers as a scene. A
  follower that lacks it logs at debug, not warning, for holiday slugs. A
  leader's `christmas_ambient` propagates as ambient, so a follower goes to
  its `ambient` as it does for a leader's `christmas` today.
- **Linked motion** skips the scene machine. Motion in an area in
  `christmas_ambient` jumps to the linked `local_scene`, as it does from
  `ambient`.

### Off rules

`determine_off_action` takes the area's ambient holiday slug as a new input.
In order:

1. An ambient-like scene, `<holiday>` or `<holiday>_ambient` owned by
   `ambience` turns off. Testing `<holiday>` here also turns off a split
   area's `christmas` restored with source `ambience` from before the upgrade.
2. With ambience on:
   1. An unsplit `christmas` falls back to `ambient`, or off without one, as
      today. This stops off from relighting the scene it is leaving.
   2. A person's `christmas_ambient` turns off, as a person's `ambient` does.
   3. Any other scene, split `christmas` included, goes to the holiday's
      ambient scene when `ambient_scene` is `holiday` and a holiday is active,
      and to `ambient` otherwise.
3. Otherwise the area turns off.

The off and fade paths record a fallback to the holiday's ambient scene with
source `ambience`. A new action type, `ACTIVATE_HOLIDAY_AMBIENT_SCENE`,
carries it. Without the source, `christmas_ambient` would count as occupied
and would not turn off when ambience does.

### Holiday changes

`handle_holiday_changed` targets `holiday_ambient_slug(mode)`:

- An off area lights it with source `holiday`.
- An area in any holiday role lit with source `holiday` switches to it when it
  differs, so `christmas` to `halloween` moves both roles.
- On `none`, an area in any holiday role lit with source `holiday` turns off.

`was_ambient_activated`, which `handle_ambient_disabled` uses, becomes "source
`ambience` and the slug is `ambient`, `<holiday>` or `<holiday>_ambient`".

`_non_night_default`'s alphabetical fallback skips holiday slugs of both
roles. `scene_machine.py` imports the holiday constants from `const.py`
rather than keeping its own copy.

## Circadian lights in a visual scene

### Which lights

`_circadian_scene_light_ids()` returns the active scene's `circadian: true`
ids, less the demand-response shed set and less clusters while demand
response is active. It returns an empty set unless the area is in a visual
scene and not dimmed. It is derived from the configuration and the current
slug, never stored, so it is right after a restart, a reload, or a change
through the `last_scene` select.

`_circadian_driven_lights()` returns every light in the `circadian` scene, the
subset's lights in a mixed scene, and no lights otherwise. The tick, the
light-on listener, the sleep listener and `refresh_circadian_brightness`
iterate it. Today they iterate `area.all_lights`, which in a mixed scene would
push circadian values onto the static lights.

### Targets

`_resolve_raw_scene_targets` replaces each `circadian: true` stanza with
`{"state": "on", "circadian": True, **payload}`, where the payload is
`_circadian_payload(light, values)` without `entity_id`. With no clock values
yet, the target is `{"state": "on", "circadian": True}`. Downstream code then
sees an ordinary on target: demand-response shed sizing, cluster dispatch,
`_scene_bulb_ids`, the settle window, and the alert's save and restore. The
`circadian` key never reaches a service call, because `light_turn_on_data`
reads only `SCENE_LIGHT_ON_ATTRIBUTES`.

`scene.turn_on` on a scene entity with circadian stanzas goes through the
controller's resolved targets, as an animated scene does now. Without a
controller it skips circadian entries rather than sending them anything.

### Adjuster lifecycle

The adjuster runs while the area is in `circadian`, or in a visual scene whose
subset is not empty.

- `_run_activation`'s visual branch stamps `_circadian_commanded_at` for the
  subset before it applies the scene, so the adjuster does not answer the
  activation's own on-echo with a 0.5 second transition that cuts a long fade
  short. In its `finally`, under the existing still-current guard, it starts
  the adjuster when the subset is not empty.
- `handle_scene_activated` and the `last_scene` select setter do the same.
- `async_resume_circadian`, called at startup, after reload and at the end of
  an alert, resumes a mixed scene too. There it skips the kelvin router and
  the shed-set recompute.
- `recompute_and_apply_circadian_dr` leaves the shed set alone outside the
  `circadian` scene. Today it clears it, which in a mixed scene would drop the
  shed bulbs' exemption from manual detection.
- Raise, lower and dim-from-dark stop the adjuster whenever it runs, not only
  in `circadian`, so the next tick does not undo the dim.
- `_leave_removed_scene` stops the adjuster. `_notify_state_change` stops it
  when the area is in neither `circadian` nor a mixed, undimmed scene, as it
  already does for a stale animation.
- The tick syncs the kelvin router only in `circadian`.
- In a mixed scene `_circadian_adjustable` does not consult kelvin routes,
  because the validator keeps route lights out of stanzas.

### Manual detection and self-heal

- Manual detection skips subset lights, as it skips every light in
  `circadian`, unless the light is in the shed set. A static light in the same
  scene is still checked.
- The post-settle self-check and drift reassert skip targets marked
  `circadian`. Their stored payload is stale after the first tick, and
  reasserting it would fight the curve.

### Demand response

A shed subset light gets `{"state": "off"}`, which drops the marker. It leaves
the subset, the adjuster skips it, and relighting it by hand latches `manual`,
as in `circadian`. Subset lights count toward the shed size in declaration
order like any scene light.

### Animation

`animated_light_ids` also skips targets marked `circadian`. Raw stanzas are
skipped already because they have no colour key, but an `rgb` light's
resolved payload carries `rgb_color`.

### Snapshots

`snapshot_scene` refuses a scene with circadian stanzas, as it refuses an
animated one. A snapshot would freeze one tick's colour temperature and
brightness. `_scene_entities` also lays configured circadian stanzas over a
stored snapshot, so a snapshot taken before this change cannot freeze them.

## Diagnostics

`sensor.area_lighting_diagnostics` gains `per_area.<area>.circadian_lights`,
the current subset, sorted.

## Limits

- Kelvin routes do not apply inside a visual scene.
- A follower mirrors a leader's `christmas_ambient` as its literal `ambient`,
  not as its own holiday ambient scene.
- An area in a holiday scene lit by ambience is not retargeted when
  `holiday_mode` changes and is not turned off when it becomes `none`. That
  was true before this change and stays true.
- An edit that removes `circadian: true` from the active scene stops the
  adjuster for that light at reload. The light keeps its last circadian values
  until the scene is next activated or self-heal applies the new static
  target.

## Tests

Unit:

- Stanza schema: `circadian: true` fills `state: on`; double run idempotent;
  `false` rejected; each forbidden companion key rejected; error names area
  and scene.
- Semantic validator: unknown light, light without a group, cluster, route
  light, valid case; reload keeps the running config on failure.
- `<holiday>_ambient` without `<holiday>` rejected; `buttons.on` rejects
  `christmas_ambient`; scene id accepted.
- Scene machine: every row of the roles table for split and unsplit areas;
  each off rule; motion step-up from `christmas_ambient`; motion no-op in
  split `christmas`; `_non_night_default` never picks a holiday slug.
- `animated_light_ids` skips a resolved circadian target.

Integration:

- Mixed scene activation sends circadian payloads to the subset and static
  targets to the rest; a tick moves only the subset.
- The activation's on-echo does not get a second command.
- A tick's echo does not latch `manual`; a person's change to a static light
  in the same scene still does.
- Self-heal does not reassert a circadian target.
- The light-on and sleep listeners and the minimum brightness reduction touch
  only the subset.
- Raise and lower stop the adjuster; dim-from-dark does too.
- Demand response: shed subset light stays off, the shed set survives resume
  and reload, a manual relight of a shed bulb latches.
- Startup, reload and alert end resume a mixed scene's adjuster.
- `last_scene` select into a mixed scene starts it; leaving stops it.
- `scene.turn_on` of a mixed scene entity never turns a circadian light off.
- Holiday mode change into a split area lights `christmas_ambient` with source
  `holiday`; `none` turns it off.
- Off from split `christmas` with ambience on and `ambient_scene: holiday`
  lands in `christmas_ambient` owned by `ambience`; ambience off then turns it
  off.
- Occupancy: split `christmas` arms the timer, `christmas_ambient` cancels it.
- A split area restored in `christmas` with source `ambience` turns off at
  the next off.
- `snapshot_scene` refuses a mixed scene.
