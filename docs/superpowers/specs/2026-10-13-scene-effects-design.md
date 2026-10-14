# Scene effects: design spec

Date: 2026-10-13
Status: Draft, revised with the user's answers, awaiting review
Scope: one minor release of `area_lighting` (3.2.0, from one `(Minor)`
commit), then a haconf change that must land after it

Line numbers below are from `main` at `24661c8` (release 3.1.2) unless a path
starts with `haconf:`, which means `/home/aaron/git/aaron/haconf` at
`origin/main` (`49308364`) on 2026-10-13. Release 3.1.2 only bumped the
version for this spec's commit, so every code line matches 3.1.1 (`3acb713`).
Bare paths are under `custom_components/area_lighting/`.

Two proposals and an independent review preceded this spec. It takes the
dynamic scene proposal, narrowed by the review's corrections.

## 1. Goal and context

An area gets scenes that keep changing their lights' colours on a timer.
"Party" is the first: each colour bulb gets a new random colour every few
seconds. The effect is a named preset in a global `effects:` block, and a scene
uses one by name with `effect: party`. Every way into the scene starts the
effect, and every way out stops it.

Today haconf runs party mode outside `area_lighting`:

- `haconf:scripts/theater/party_mode.yaml:1-32` loops over eight theater
  lights while `input_boolean.theater_party_mode` is on, sending each a random
  `rgb_color` at brightness 220 every 5 seconds.
  `haconf:automations.yaml:383-393` starts it.
- `haconf:scripts/den/party_mode_a.yaml` does the same for four den lights.
  No automation in the repo starts it.
- `haconf:automations.yaml:2227-2254` runs the third-party Party Lights
  blueprint on 13 media room lights while `input_boolean.media_room_party_mode`
  is on: random colour, random brightness from 1 to 100 percent, every 5
  seconds.

None of them tells `area_lighting`. The select keeps showing the old scene,
motion and occupancy timers fade the room mid-party, and manual detection sees
each random colour as a divergence from the scene's targets.

The component already runs one scene that keeps sending commands: circadian.
It has a start and stop pair with a generation counter
(`controller.py:1569-1608`), a guard so ticks never overlap
(`controller.py:1722-1725`), a check before every command
(`controller.py:1838-1843`), and an area-wide manual detection skip
(`event_handlers.py:518-526`). Effects copy that pattern. They do not fold
circadian in.

## 2. Decisions

The user made decisions 1 to 11. They are final.

1. **Party is a scene with an effect generator.** It is not a light role and
   not a per-area switch. Roles stay as they are.
2. **Presets are global.** A top-level `effects:` block defines named presets.
   A scene names one with `effect: <name>`.
3. **Holiday colours are ordinary presets.** `christmas_party` is a preset
   with a red, green and white palette. Nothing selects it from
   `input_select.holiday_mode`, even if the helper gains an option with that
   name (6.7). An area that wants both defines a `party` and a
   `christmas_party` scene and places either in its remote cycles.
4. **Preset parameters:** a generator (`random` gives each light a random RGB
   colour, `palette` gives each light a random pick from a colour list), an
   interval in seconds with an enforced minimum, a transition in seconds,
   brightness (fixed or a range), and `sync` (all lights share one colour per
   tick). Speed and transition are fixed per preset. Per-area number entities
   for them are a non-goal.
5. **No overlay or mode engine.** Alert and demand response keep their own
   designs.
6. **Holiday slug consolidation is separate and optional.** The duplicated
   holiday sets sit at `const.py:17`, `scene_machine.py:51`,
   `area_state.py:63` (inside `AMBIENT_LIKE_SCENES`), `models.py:244`,
   `event_handlers.py:641` and `controller.py:981`. Merging them can be a
   behaviour-neutral commit before this feature, and the feature does not
   depend on it. Merging would force a decision on an existing mismatch: the
   motion gate lets motion through in a holiday scene
   (`event_handlers.py:636-642`), but `determine_on_action` overrides only
   `ambient` (`scene_machine.py:114-119`), so that motion does nothing. This
   spec changes neither.
7. **Reserved slugs.** `off`, `off_internal`, `manual`, `circadian`,
   `ambient`, `christmas`, `halloween`, `night`, `daylight` and `evening` may
   not carry `effect:`. Each already has fixed behaviour. An animated holiday
   scene would also count as ambient-like (`area_state.py:63`) and
   holiday-tracked (`controller.py:2830-2842`). Motion's default picks
   `night` in night mode and `daylight` or `evening` by sun position
   (`scene_machine.py:62-72`), and `_resolve_and_activate` activates the sun
   position result directly (`controller.py:1559-1565`).
8. **A motion timer never fades an effect scene.** Starting an effect leaves
   a running motion timer alone, because that timer also cleans up the linked
   areas the motion lit (`controller.py:2914-2917`). When it fires,
   `_on_motion_timer` cleans them up and then returns without fading if the
   area is in an effect scene. The occupancy timer applies as in any lit
   scene. Section 6.3 has the details.
9. **Demand response and effect scenes are mutually exclusive.** While demand
   response is active, no path enters an effect scene. The scene entity and
   the select raise `HomeAssistantError`, remote cycles skip effect scenes,
   and internal paths take the area to its demand response default instead.
   When demand response turns on, an area in an undimmed effect scene leaves
   it for that default, which sheds like any other scene. An alert that holds
   the area keeps that exit owed until it ends. A dimmed party, whose effect
   already stopped, keeps its frozen frame, as the existing re-drive leaves
   every dimmed area alone. When demand response turns off, nothing restarts
   the party. Section 6.2 has each path.
10. **The den's party uses every colour light, the stoplight included.** The
    den's effect scenes carry no `entities:`. haconf adds
    `light.den_stoplight_red`, `light.den_stoplight_yellow`,
    `light.den_stoplight_green` and `light.den_desk_clock_display` to the
    den's `lights:`, scoped with `scenes:` to the den's effect scenes and with
    no circadian group. They take part in a party, and nothing else the den
    does touches them. A dashboard change to the stoplight never latches the
    den to manual. `light.den_stoplight_main`, WLED's master brightness,
    stays out. Section 4.6 has the rules that make this hold.
11. **Changes made during an alert are lost on restore, as today.** The alert
    restores its saved state and colours, so a dim or a static scene chosen
    mid-alert is undone. Entering or leaving an effect scene is the one
    exception (decision 20).

This spec makes decisions 12 to 25, which the brief left open. Review should
start with them.

12. **Participants.** Only the area's declared individual lights can take
    part: `lights:` entries without `members`. The manual detection listener
    watches every one of them (`event_handlers.py:300-307`). With
    `entities:`, the participants are the listed lights with `state: "on"`
    that are available and colour capable. Without `entities:`, they are every
    available, colour capable declared individual light whose `scenes:` list
    allows the scene (`models.py:47-49`). `group_exclude` removes lights
    first, either way. An effect scene's `entities:` may name only declared
    individual lights, so it can never name a cluster or a light outside the
    area. Section 4.2 has the rules.
13. **Lights outside the participant set.** With `entities:`, every `off`
    entry and every `on` entry for an available light without colour applies
    once at activation, as a static scene would apply it. Excluded and
    unavailable entries get no command. Without `entities:`, the effect leaves
    every other light alone. To turn a white lamp off during a party, list it
    with `state: "off"`.
14. **Ticks command only lit participants.** Activation turns every kept
    participant on. Later ticks skip a participant that is off or
    unavailable, and the check runs again as each command is dispatched
    (4.5). So switching one bulb off takes it out of the party until the next
    activation, and a tick never relights a bulb the user just turned off.
    Circadian follows the same rule (`controller.py:1774-1778`).
15. **Motion never picks an effect scene.** Reserved slugs keep effects off
    every scene an automatic path picks by name (decision 7). The generic
    default skips effect scenes, a follower ignores a leader's effect scene
    when motion turned it on, and `linked_motion` may not name one. Section
    6.4 has the details.
16. **The scene entity drives activation.** `AreaLightingScene.async_activate`
    for an effect scene calls the controller, which runs the full activation.
    The `scene.turn_on` tracker ignores effect slugs, so the scene is not
    handled twice. A static scene entity leaves the effect scene, and waits
    for a tick still in flight, before it applies its lights.
17. **The select turns nothing on.** Selecting an effect scene sets the
    state, arms the occupancy timer and starts the effect on the participants
    that are on. Selecting circadian also turns nothing on
    (`controller.py:543-561`). The select entity awaits a new controller
    method for this (5.1). Under demand response the select refuses an effect
    scene (decision 9).
18. **Raise and lower freeze the effect.** Dimming stops the effect and keeps
    the scene, as it suspends circadian (`controller.py:2287-2288`). The next
    `on` restores the scene and restarts the effect. Raise and lower also
    take the activation claim, so a party still starting yields to them
    instead of starting its effect over the dim (4.5).
19. **Manual detection skips the whole area while it is in an effect
    scene.** That matches circadian, without circadian's exception for a shed
    bulb, since an effect scene never runs under demand response (decision
    9). A manual change during a party is therefore not detected.
20. **Alert supersession is scoped to effect scenes.** If the area entered or
    left an effect scene while any alert held it, the last alert to finish
    keeps the newer state and re-applies it instead of restoring its
    snapshot. Every other change during an alert is lost (decision 11).
    Section 6.1 has the rule.
21. **Snapshots do not apply.** An effect scene ignores any stored snapshot,
    and `area_lighting.snapshot_scene` refuses an effect scene.
22. **Tick timing.** The next tick is scheduled with Home Assistant's
    `async_call_later` once the previous tick's commands have all returned. The
    interval floor is 2 seconds and the default is 5. Ticks cannot overlap.
23. **haconf adds no remote buttons in the first cut.** The scenes are
    reachable from the select, the scene entities and dashboards. Where they go
    on remotes is a follow-up, because adding a slug to a favorite cycle
    changes what every press does from the scenes that are not in it (haconf
    CLAUDE.md, "A favorite cycle is one shared position").
24. **Effect-only lights.** A light whose `scenes:` list names only effect
    scenes is out of play while the area is not in one of them. The component
    then neither commands it nor reads it: off, static scenes and their
    stored snapshots, alerts, effect ticks, raise and lower, the startup
    reconcile, manual detection and the all-off and occupancy edges skip it.
    Validation keeps it out of clusters and kelvin routes. Section 4.6 lists
    each path.
25. **Leaving a party turns its effect-only lights off.** An activation that
    takes the area from an effect scene to a scene the light does not list
    turns the light off, so the stoplight does not keep a random colour after
    the party. The select sends no commands, and neither does an older
    static activation that commits over a newer party (4.3), so after either
    the light keeps its last frame and is out of play (4.6).

## 3. Config schema

### 3.1 The `effects:` block

A new optional top-level key in `CONFIG_SCHEMA` (`__init__.py:64-81`), beside
`alert_patterns`. It maps a preset name to its parameters.

| key | type | default | rule |
| --- | --- | --- | --- |
| `generator` | `random` or `palette` | required | |
| `colors` | list of `[r, g, b]`, each 0 to 255 | none | required for `palette`, rejected for `random`, at least one entry, no repeated colour |
| `interval` | seconds, float | 5 | at least 2 (`EFFECT_MIN_INTERVAL_SECONDS`) |
| `transition` | seconds, float | 0 | at least 0, at most `interval` |
| `brightness` | int 1 to 255, or `[min, max]` | 255 | a range needs `min <= max` |
| `sync` | bool | `false` | |

Preset names follow the scene id rule (`config_schema.py:154-164`), so they
read the same in logs and diagnostics.

`random` draws each RGB channel uniformly from 0 to 255, as the theater script
does. `palette` picks uniformly from `colors`, except that a light never gets
the colour it got on the previous tick when the palette has two or more
colours. Without that rule a three-colour palette would leave each bulb
unchanged on a third of the ticks. Validation rejects a palette that lists
the same colour twice, so every entry is distinct and the rule can always be
met. A brightness range draws per light per tick. With `sync: true`, each
tick draws one colour and one brightness and sends them to every participant.

### 3.2 The scene's `effect` key

`SCENE_SCHEMA` (`config_schema.py:167-176`) gains `vol.Optional("effect")`, a
preset name. `SceneConfig` (`models.py:56-66`) gains `effect: str | None`.
`parse_config` copies it (`config_schema.py:447-457`), and
`AreaLightingConfig` (`models.py:293-300`) gains
`effects: dict[str, EffectPreset]`.

This key is not the per-light `effect` attribute. That one sits inside an
`entities:` entry, comes from `SCENE_LIGHT_ON_ATTRIBUTES` (`const.py:125-135`)
and is passed to `light.turn_on` as the bulb's own effect. An effect scene
ignores attributes on participant entries, so a per-light `effect` there does
nothing.

### 3.3 Validation

A new `validate_scene_effects(config)` in `config_schema.py` runs from
`async_setup` beside the other validators (`__init__.py:91-105`) and from
`_async_read_config` (`__init__.py:266-279`). A failure at startup logs and
returns `False`. A failure on reload raises, and the reload changes nothing.
It rejects:

- a scene whose `effect` names no preset in `effects:`
- `effect` on a reserved slug (decision 7)
- an effect scene whose `entities:` names anything but a declared individual
  light of its area. That rejects a cluster from `light_clusters`, a cluster
  declared under `lights:` with `members` (`config_schema.py:125-129`,
  `models.py:51-53`), a cluster member the area does not declare under
  `lights:`, and a light from another area.
  The check runs over every entry in `area.all_lights` that carries
  `members`, as `_individual_light_ids` does (`controller.py:1868`).
- an effect scene whose `entities:` names a light whose `scenes:` list omits
  that scene
- a static scene whose `entities:` names an effect-only light (4.6)
- a light whose `scenes:` names an effect scene and also names a slug its
  area does not define. Without this, a typo would make the light an ordinary
  scoped light, and every off would turn it off.
- a `lights:` or `light_clusters` entry with `members` whose `scenes:` names
  an effect scene
- a `lights:` or `light_clusters` entry with `members` that lists an
  effect-only light. Off (`controller.py:1537-1545`) and the skeleton
  (`controller.py:1246-1258`) command every cluster, and the all-off and
  occupancy edges read its aggregate state (`event_handlers.py:444-447`,
  `event_handlers.py:742-747`), so a cluster over the light would reach it
  in every one of those paths.
- an effect-only light in `circadian_kelvin_routes`, named directly or as a
  cluster member. The router switches on a route light that has no
  circadian group (`controller.py:1808-1820`), and the on-set takes the
  active route's lights whatever their group (`controller.py:1025-1031`).
  Today the route check requires only that each light is declared
  (`config_schema.py:714-729`).
- a `linked_motion` `local_scene` that is an effect scene in its own area, or a
  `remote_scene` that is an effect scene in the remote area. Today
  `LINKED_MOTION_MAPPING_SCHEMA` checks only that each is a string
  (`config_schema.py:291-296`).

Today nothing checks the slugs in a light's `scenes:` list. The schema takes
any quoted string (`config_schema.py:124`). The unknown slug rule checks only
lights that name an effect scene, and the cluster and route rules only
effect-only lights, so existing configs keep loading.

An effect scene may appear in `buttons.favorite` and `buttons.on`.
`_ON_SEQUENCE_RESERVED` (`config_schema.py:251-254`) does not change.

### 3.4 Full example

haconf includes `area_lighting.yaml` under the `area_lighting:` key
(`haconf:configuration.yaml:353`), so these keys sit at the top of that file.

```yaml
effects:
  party:
    generator: random
    interval: 5
    transition: 0
    brightness: 220
  christmas_party:
    generator: palette
    colors:
      - [255, 0, 0]
      - [0, 255, 0]
      - [255, 255, 255]
    interval: 5
    transition: 1
    brightness: [150, 255]
  media_room_party:
    generator: random
    interval: 5
    brightness: [3, 255]

areas:
  # Every colour capable light in the area takes part.
  - id: theater
    name: Theater
    scenes:
      - id: party
        name: Party
        effect: party
      - id: christmas_party
        name: Christmas Party
        effect: christmas_party

  # Every colour light takes part, including four effect-only lights. The
  # stoplight segments and the desk clock list only the effect scenes, so
  # no other den scene, no off and no alert touches them while the den is
  # outside those scenes (4.6).
  - id: den
    name: Den
    lights:
      # The nine existing lights stay as they are.
      - id: light.den_stoplight_red
        scenes:
          - party
          - christmas_party
      - id: light.den_stoplight_yellow
        scenes:
          - party
          - christmas_party
      - id: light.den_stoplight_green
        scenes:
          - party
          - christmas_party
      - id: light.den_desk_clock_display
        scenes:
          - party
          - christmas_party
    scenes:
      - id: party
        name: Party
        effect: party
      - id: christmas_party
        name: Christmas Party
        effect: christmas_party

  # Only the listed "on" lights take part. 10.1 lists all 13.
  - id: media_room
    name: Media Room
    scenes:
      - id: party
        name: Party
        effect: media_room_party
        entities:
          light.media_room_front_left_c1:
            state: "on"
          light.media_room_tv_backlight:
            state: "on"
```

No area gets a remote button. haconf adds none in the first cut
(decision 23).

## 4. Architecture

### 4.1 Modules

| file | change |
| --- | --- |
| `effects.py` (new) | `next_frame()`, a pure function, and `EffectRunner`, which owns the tick timer |
| `models.py` | `EffectPreset`, `SceneConfig.effect`, `AreaLightingConfig.effects`, `AreaConfig.effect_scene_slugs`, `AreaConfig.effect_only_light_ids` |
| `config_schema.py` | `EFFECT_PRESET_SCHEMA`, the scene key, `parse_config`, `validate_scene_effects` |
| `const.py` | `EFFECT_MIN_INTERVAL_SECONDS = 2`, `DEFAULT_EFFECT_INTERVAL_SECONDS = 5`, `EFFECT_HANDOFF_SECONDS = 2` |
| `light_targets.py` | `is_color_capable`, moved from `alert.py:48-50`, which imports it back |
| `controller.py` | an effect branch in `_activate_scene` with the demand response guard, start, stop, leave and resume, the effect leave before every non-effect commit, `async_select_scene`, `_demand_response_default`, `_effect_exit_pending`, `light_in_play`, the dispatch check and completion barrier, every exit path, the claim taken by raise and lower, the effect-only light filters, effect-aware `_scene_bulb_ids` and `_area_holiday`, the motion timer guard, diagnostics |
| `scene.py` | effect routing and the demand response refusal in `AreaLightingScene.async_activate`, the skeleton and stored snapshot filters, `scene_source: effect` |
| `select.py` | `async_select_option` awaits `async_select_scene` |
| `event_handlers.py` | the manual detection skips, the tracker skip, the effect-only filters in the all-off and occupancy edges |
| `scene_machine.py` | `effect_slugs` keeps effect scenes out of the generic default |
| `alert.py` | the alert hold count, the supersession check, the effect-only filter at start, before each step and before the restore |
| `services.py` | `snapshot_scene` refuses effect scenes and leaves out effect-only lights |
| `__init__.py` | schema key, validator, resume at startup and on reload |
| `README.md`, `CONFIGURATION.md` | the new block, the scene key, effect-only lights, the lifecycle and demand response rules |

```python
# models.py
@dataclass(frozen=True)
class EffectPreset:
    name: str
    generator: str  # "random" or "palette"
    colors: tuple[tuple[int, int, int], ...] = ()
    interval: float = DEFAULT_EFFECT_INTERVAL_SECONDS
    transition: float = 0.0
    brightness: tuple[int, int] = (255, 255)  # a fixed value parses to (n, n)
    sync: bool = False


# effects.py
def next_frame(
    preset: EffectPreset,
    light_ids: list[str],
    rng: random.Random,
    previous: dict[str, tuple[int, int, int]],
) -> dict[str, dict[str, Any]]:
    """light.turn_on data per light for one tick. Pure: no Home Assistant."""


class EffectRunner:
    """One area's effect: the preset, the participants and the tick timer.

    The controller owns the generation counter and every check before a
    command is sent. The runner only schedules ticks and builds frames.
    """
```

`next_frame` takes its random source as an argument, so unit tests pass a
seeded `random.Random`.

### 4.2 Participants

The controller splits an effect scene's lights into three sets when an effect
starts, and keeps them for that run. Each start recomputes them: activation,
resume and a reload resume.

The candidates are the area's declared individual lights, the `lights:`
entries without `members`, in config order. Validation keeps every
`entities:` key inside that list (3.3).

1. Remove the scene's `group_exclude` from the candidates and from the
   `entities:` keys. An excluded light lands in no set and gets no command,
   as in the static skeleton path (`controller.py:1307-1319`). Without
   `entities:`, also remove every candidate whose `scenes:` list omits the
   slug (`LightConfig.in_scene`, `models.py:47-49`), as the skeleton does
   (`controller.py:1254`). With `entities:`, validation already rejects such
   a key (3.3).
2. Remove every light whose live state is missing or `unavailable`. It also
   gets no command. An unavailable bulb sits out the run and joins at the
   next activation.
3. Participants. With `entities:`, the remaining keys whose entry has
   `state: "on"` and whose light is colour capable by `is_color_capable`
   (today `alert.py:48-50`, which reads `supported_color_modes`). Without
   `entities:`, every remaining colour capable candidate.
4. Static entries. With `entities:`, every remaining entry that is not a
   participant: the `off` entries and the `on` entries for lights without
   colour. Without `entities:`, there are none.

Capability comes from live state, not from roles. Roles stay inert: nothing
calls `lights_with_role` (`models.py:271-272`).

### 4.3 Activation

A new branch in `_activate_scene`, after the circadian branch
(`controller.py:942-950`) and before the visual path (`controller.py:952`).
Every branch of `_activate_scene` first takes the activation claim
(`controller.py:924-925`), calls `_leave_effect_scene()`, waits for a tick
still in flight (4.5), and turns off the effect-only lights the next scene
does not list (4.6). The effect branch then runs:

1. If demand response is active, activate the demand response default
   instead, with the same source, transition and flags, and return (6.2).
2. `_stop_circadian()`.
3. Clear `_active_scene_targets`, as circadian does (`controller.py:943`).
   With no targets, `_schedule_post_settle_selfcheck` returns early
   (`controller.py:2438`), and the branch does not call it anyway. Set
   `_dr_shed_ids` to empty, since an effect scene sheds nothing.
4. Compute the three sets (4.2).
5. Apply each static entry once, through `_apply_light_state`
   (`controller.py:1261-1276`) with the caller's transition.
6. Return if the controller closed or the activation lost its claim (4.5),
   whatever `yield_if_superseded` says. The visual path checks the claim only
   for that flag (`controller.py:961-972`). If demand response turned on
   during step 5, activate the default instead, as in step 1, and return.
7. `_clear_scene_drift_issue()`, `transition_to_scene(slug, source)`, bump
   `_effect_scene_epoch` (4.5), clear `_effect_exit_pending` (6.2),
   `_enforce_occupancy_timer()`, `_notify_state_change()`, then await
   `_sync_kelvin_router()`.
8. Return if the activation lost its claim during that await. Otherwise call
   `_start_effect(turn_on=True)`. It sends the first frame to every kept
   participant and awaits it, then schedules the next tick.
9. Return if the activation lost its claim during the first frame. Otherwise
   propagate to followers with `LeaderReason.SCENE_ACTIVATED`, unless
   `propagate_to_followers` is false or the source is `LEADER`. The existing
   tuple at `controller.py:980-987` already gives an effect slug that reason.

Once step 7 commits, the state names the effect scene, so a demand response
flip after that point reaches the area through the re-drive (6.2).

The other branches can also commit over a newer party. An ordinary
activation without `yield_if_superseded` ignores a lost claim
(`controller.py:961`), so one suspended in `_apply_scene_data`
(`controller.py:958`) can resume after a party has committed and started,
and then commit its own scene (`controller.py:975`). The leave at the top of
the method ran before that party existed. So each non-effect commit calls
`_leave_effect_scene()` again, synchronously, just before it changes the
state: the off branch before `transition_to_off` (`controller.py:934`), the
visual path before `transition_to_scene` (`controller.py:975`), and the
circadian branch before `_activate_circadian` commits
(`controller.py:1053`). The call does nothing unless the state names an
effect scene. It sends no commands, so the party's effect-only lights keep
their last frame, as after the select. The runner is also bound to its scene
(4.4, 4.5), which catches any commit this rule misses.

### 4.4 The runner and the tick

`_start_effect(turn_on)` bumps `_effect_generation`, builds the three sets, and
starts the runner. `turn_on=True`, used by activation, sends the first frame
to every kept participant. `turn_on=False`, used by resume and the select,
sends the first frame only to participants that are on. It turns no bulb on.
Each later tick:

1. Returns at once if the generation moved, the controller is closed, or the
   runner stopped. If the state no longer names the runner's scene, or names
   it dimmed, the tick calls `_stop_effect("scene changed")` and returns.
2. Skips the frame while an alert holds the area (6.1), and reschedules.
3. Builds a frame for the participants that are on, available and in play
   (4.6).
4. With `sync: true`, coalesces the frame through `select_dispatch_commands`
   (`cluster_dispatch.py`), so a cluster whose members all take part gets one
   command. Demand response never needs the cluster exclusion here
   (`controller.py:1069-1074`), because no effect runs under it.
5. Sends every command through `async_effect_call`, which checks each light
   again (4.5), and awaits all of them with `return_exceptions=True`. It logs
   each failure as a warning, as `_send_circadian_logged` does
   (`controller.py:1746-1769`).
6. Resolves `_effect_tick_done` (4.5) and schedules the next tick with
   `async_call_later(interval)`.

The interval runs from the end of one tick to the start of the next, so a slow
bridge stretches the period instead of stacking ticks. Each area has one
runner, so ticks in an area are serial.

### 4.5 Generations, claims and completion

Five mechanisms keep a stale effect from commanding lights. The first three
change synchronously, before the caller's first await.

**Effect generation.** `_effect_generation` moves on every `_start_effect`
and every `_stop_effect(reason)` call. `_stop_effect` cancels the scheduled
tick, marks the runner inactive and bumps the generation, whether or not a
runner was active. Raise and lower, `apply_config` and `shutdown` call it
directly.

**Effect scene epoch.** `_effect_scene_epoch` counts moves into and out of
effect scenes, whether or not a runner was ticking.
`_leave_effect_scene(reason)` bumps it when `effect_scene_active` (6.5) is
true, dimmed or not, then calls `_stop_effect`. Every exit in 5.2 that
changes the scene calls it. The effect branch (4.3, step 7) and the select's
effect entry (5.1) bump it when they commit. Raise and lower, alert pauses,
reload and shutdown leave it alone. So does an activation with
`yield_if_superseded`, which marks a background replay of the current state:
the demand response re-drive and the alert re-apply. Section 6.1 reads the
epoch.

**Activation claim.** The effect branch acts on a lost claim at steps 6, 8 and
9 of 4.3, whatever `yield_if_superseded` says. Every `_activate_scene` call
takes a new claim. `async_select_scene` (5.1), `apply_config`, and raise and
lower where they call `_stop_effect` bump `_activation_seq` too, as
`_leave_removed_scene` already does (`controller.py:428`), and `shutdown`
sets `_closed`. So a remote off, a select, a scene entity, a fade, a dim, a
reload or an unload during the static commands keeps the older party from
committing, starting or propagating.

Yielding here drops no cascade. The handlers the static commands can
trigger, all-off and the manual latch, change state without activating a
scene (`controller.py:2376-2395`, `controller.py:2397-2425`). If one of them
fires mid-activation, the activation commits over it, as the visual path does
today.

Raise and lower need the claim because `_stop_effect` alone cannot stop a
party that has not started its runner. A raise between step 7's commit and
step 8 marks the party dimmed (`controller.py:2296-2298`), and without the
claim step 8 would then start the effect in a dimmed scene and send the
first frame over the dim. A raise before the commit dims the previous scene,
and the party no longer commits over it.

The select's bump is still needed with demand response out of the effect:
without it, a selection made during a party's static commands would be
overwritten when the party commits. It also makes an in-flight demand
response re-drive of a static scene yield to a selection
(`controller.py:1446-1450`). Today that re-drive commits over it. Section 12
lists that side effect.

**Dispatch check.** `async_effect_call(generation, entity_id, **data)` is the
only way an effect command reaches Home Assistant. Every effect command is a
`light.turn_on`. As each queued command starts, the call drops it unless all
of these hold:

- `generation` equals `_effect_generation`, the runner is active, and the
  controller is not closed.
- The state names the runner's scene and is not dimmed. The runner records
  the slug it started for.
- The target light is in play (4.6). Validation keeps effect-only lights out
  of clusters (3.3), so a coalesced cluster command never carries one.
- No alert holds the area (6.1).
- Demand response is not active. The setter flips the flag, then only
  schedules each controller's re-drive as a task (`global_state.py:101-112`),
  so a tick already running when the flag flips would otherwise send its
  remaining commands under demand response.
- The target light, or every member of a coalesced cluster, has a live state
  that is not `unavailable`, and is on. The first frame of `turn_on=True`
  skips the "is on" test, because it turns the participants on. If a cluster
  command fails the member test, the call sends the frame to the passing
  members one by one instead.

Circadian checks live state the same way as each send starts
(`controller.py:1774-1781`), though it reads a cluster's own state only.

**Completion barrier.** A command that passed the check is inside Home
Assistant's service call and cannot be recalled. Each tick creates
`_effect_tick_done`, a future it resolves in its `finally`, as circadian does
(`controller.py:1726`, `controller.py:1742`). Two places wait on it:

- `async_end_alert` waits on it beside the circadian future
  (`controller.py:1620-1622`), with the same `CIRCADIAN_SETTLE_SECONDS`
  bound, before it releases the area.
- `_activate_scene` waits on it after `_leave_effect_scene()` and before its
  first command, for at most `EFFECT_HANDOFF_SECONDS = 2`. The new scene's
  commands then follow the last tick's instead of racing them.

On timeout the caller logs a WARNING and goes on. A tick command that
completes later can land on top of the new scene. A static scene's post-settle
self-check heals that bulb when self-heal is on (`controller.py:2426-2443`).
Off and circadian have no such check, and section 12 lists the risk. The
select and the manual latch send no commands, so they do not wait.

`async_effect_call` does not go through `_call_service`
(`controller.py:809-820`), which logs every command at DEBUG. haconf runs
`custom_components.area_lighting` at debug (`haconf:configuration.yaml:16`).
A 13-bulb party at 5 seconds would add about 2.6 log lines a second for one
area. The effect logs one DEBUG line per tick instead (section 8). It keeps
the closed check from `controller.py:810-812`.

### 4.6 Effect-only lights

An effect-only light is a `lights:` entry without `members` whose `scenes:`
list is not empty and names only effect scenes of its area.
`AreaConfig.effect_only_light_ids` computes the set at load. Such a light is
in play while the area's state is a scene its `scenes:` list names, dimmed or
not, and out of play otherwise. `ctrl.light_in_play(entity_id)` answers
that, and is true for every other light.

The existing scoping (`LightConfig.in_scene`, `models.py:47-49`) keeps a
scoped light out of the skeleton's on-set, but it does not make the light
invisible. Read against the code, this is what the stoplight would get
today if it were declared with `scenes: [party, christmas_party]`:

| path | where | today | change |
| --- | --- | --- | --- |
| Off | `_turn_off_all_lights` turns off every `all_lights` entry (`controller.py:1537-1545`) | every off turns the stoplight off, whatever scene the den was in | skip the light unless in play |
| Static skeleton | `scene.py:223-240`, `controller.py:1246-1258` | `in_scene` is false, so every skeleton scene turns it off | skip it, as `group_exclude` does |
| Skeleton targets | `controller.py:1314-1318` | it gets an off target, which manual detection and event-path heal compare against (`event_handlers.py:583-596`) | omit it |
| Stored snapshots | `_apply_scene_data` (`controller.py:1184-1190`), `_resolve_raw_scene_targets` (`controller.py:1295-1304`) and `AreaLightingScene.async_activate` (`scene.py:122-124`) prefer a stored snapshot | a snapshot taken while the light was unscoped, before a reload made it effect-only, still commands it, counts it in the shed (`controller.py:1212`, `scene.py:156`), and gives it an on target that post-settle heal retries until it latches manual (`controller.py:2482-2483`, `controller.py:2531`) | drop it from the stored payload before shedding, dispatch, targets and bulb selection |
| Manual detection | the listener covers every `lights:` entry (`event_handlers.py:300-307`) | in a scene with `entities:` it has no target, so `state_matches_scene_target` returns false (`controller.py:1461-1463`) and a dashboard change latches the den to manual after the grace window | skip it beside the `group_exclude` skip (`event_handlers.py:535-537`) |
| All-off detection | `event_handlers.py:444-447` | a lit stoplight keeps the den from reading all off when every bulb goes off outside `area_lighting` | ignore the light, and ignore events from it |
| Occupancy light edges | `event_handlers.py:742-747` | the same, so `handle_occupancy_lights_off` (`controller.py:2762-2764`) never cancels the occupancy timer | ignore it |
| Startup reconcile | `controller.py:401-412` | persisted off plus a lit stoplight switches the den to manual | ignore it |
| Raise and lower | `_on_light_entity_ids` (`controller.py:1881-1895`), used for stepping and for the dark test (`controller.py:2283`), and the `_individual_light_ids()` fallback (`controller.py:2333-2334`) | a raise in `study` steps the stoplight | leave it out |
| Alerts | `alert.py:255` takes every `lights:` entry, and each step and the restore reuse that list after the sleeps (`alert.py:153`, `alert.py:167`, `alert.py:299`) | every alert flashes, captures and restores it | leave it out of the list at start, and check `light_in_play` again before each step's commands and before the restore (6.1) |
| `snapshot_scene` | `services.py:132` takes every `all_lights` entry | the snapshot records it, and the snapshotted scene then commands it | always leave it out, since only static scenes take snapshots (decision 21) |

The skeleton, skeleton target and stored snapshot rows drop the light
whenever the scene being resolved is static, whatever the current state,
since an effect-only light never belongs to a static scene. The snapshot
service always leaves it out. Every other row applies only while the light
is out of play. In a party the light is a participant, and every path treats
it as any other light. Off from a party turns it off, because the off branch
runs `_turn_off_all_lights` before `transition_to_off`
(`controller.py:927-934`), while the state still names the party.

Three paths already leave it alone, and need no change:

- Circadian, given the kelvin route rule in 3.3. Outside the routes it
  drives only lights with a circadian group. `_circadian_payload` returns
  `None` without one (`controller.py:1101-1103`), `_circadian_adjustable`
  refuses the light (`controller.py:1792-1793`), and the light listener and
  the on-set take grouped lights only (`controller.py:1580`,
  `controller.py:1025`). A route light is different: the on-set takes the
  active route's lights whatever their group (`controller.py:1025-1031`),
  and the router switches on one without a group
  (`controller.py:1808-1820`). Omitting `circadian_group` alone would not
  keep the light out, so validation rejects it in a route. Circadian manual
  detection skips the whole area (`event_handlers.py:524-526`).
- Demand response sheds the tail of an activation's on-set
  (`demand_response.py:68-78`). Static on-sets come from targets
  (`controller.py:1380-1395`), circadian on-sets from grouped lights
  (`controller.py:1034-1038`), and an effect scene never runs under demand
  response (6.2). With the skeleton change and the validation in 3.3, no
  on-set can hold an effect-only light. It still sits in the ordered list
  each shed reads, but order alone sheds nothing.
- Self-heal, given the target rows above. The post-settle check reads on
  targets only (`controller.py:2482-2483`), and event-path heal needs a
  target (`event_handlers.py:583-586`).

**Leaving a party (decision 25).** `_activate_scene`, `lighting_circadian`
and `async_leave_effect_scene()` (5.2) note the effect scene they leave.
After the completion barrier they send `light.turn_off`, through
`_call_service`, to each effect-only light of that scene that is on and whose
`scenes:` list does not name the next scene. Party to `christmas_party` keeps
the stoplight as a participant. The off branch needs nothing more, as above.
The select, the all-off handler, a reload and the leave before a late
non-effect commit (4.3) send no commands, so after them the light keeps its
last frame and is out of play.

Lights out (`haconf:scripts/_global/lights_out.yaml`) calls
`lighting_force_off`, which runs the off branch (`controller.py:2153-2169`).
It leaves a stoplight lit from the dashboard alone, as today, when no area
declares the stoplight.

## 5. Lifecycle

### 5.1 Entry paths

Every path below ends in the activation of 4.3, or in `_start_effect` with
`turn_on=False`. While demand response is active, none of them starts an
effect. Section 6.2 gives each path's behaviour then.

| path | today | change |
| --- | --- | --- |
| Scene entity, `scene.turn_on` | `AreaLightingScene.async_activate` applies a snapshot, entities or skeleton itself (`scene.py:107-133`). The tracker then calls `handle_scene_activated`, which only updates tracking (`event_handlers.py:376-420`, `controller.py:2344-2374`). | For an effect scene, `async_activate` calls `ctrl.activate_scene(slug)`, a public wrapper over `_activate_scene(slug, USER)`. It raises first under demand response (6.2). `handle_scene_activated` returns early for an effect slug. |
| Select, `select_option` | The `current_scene` setter changes state only and starts circadian for `circadian` (`select.py:71-78`, `controller.py:543-561`). | `async_select_option` awaits a new `ctrl.async_select_scene(value)` instead of assigning the setter (`select.py:78`). For an effect slug under demand response, it raises before it changes anything (6.2). Otherwise, for any option, it first bumps `_activation_seq` and calls `_leave_effect_scene()`, then makes the setter's state change, all before its first await. For an effect slug it then bumps `_effect_scene_epoch`, clears `_effect_exit_pending` (6.2), calls `_enforce_occupancy_timer()` (`controller.py:2697-2717`) and `_notify_state_change()`, and awaits `_start_effect(turn_on=False)`. A second selection during that first frame bumps the generation, so the first frame's remaining commands drop. The synchronous setter stays for existing tests and raises for an effect slug, which needs the awaited method. |
| Remote favorite cycle | `lighting_favorite` cycles the remote's list and calls `_activate_scene` (`controller.py:2191-2214`). | Skips effect slugs under demand response (6.2). |
| Remote `buttons.on` | `determine_on_sequence_action` walks the list (`scene_machine.py:154-186`). | Skips effect slugs under demand response (6.2). |
| Remote `on`, default cycle | `determine_on_action`. Its generic fallback could pick an effect scene (`scene_machine.py:73-76`). | The fallback skips effect slugs (6.4). |
| `on` while dimmed | `lighting_on` restores the previous scene (`controller.py:2063-2069`). | None. The restore runs 4.3 and restarts the effect. |
| Follower | `handle_leader_change` activates the leader's slug if the follower defines it (`controller.py:2883-2894`). | None, except under demand response (6.2). |
| Follower turned on, Scenario A | `lighting_on` mirrors the leader's on-scene (`controller.py:2022-2032`). | Ignore an effect hint when the source is `MOTION` (6.4) or demand response is active (6.2). |
| Motion | `handle_motion_on` calls `lighting_on(MOTION)` or the linked mapping (`controller.py:2580-2588`). | Never reaches an effect scene (6.4). |
| Demand response edge | `reactivate_for_demand_response` re-drives a scene area through `_activate_scene` (`controller.py:1397-1451`). | Never enters an effect scene. Turning on moves an area out of one, and turning off skips an area in one (6.2). |
| Alert end | `async_end_alert` re-drives for demand response, or resumes circadian (`controller.py:1610-1643`). | The completion that releases the last alert hold resumes the effect, or re-applies the newer state when the alert was superseded (6.1). |
| Startup | `_on_started` restores timers, reconciles, then resumes circadian (`__init__.py:213-228`). | Then `async_resume_effect()` (section 7). |
| Reload, kept and added areas | `async_resume_circadian` per area (`__init__.py:331-337`). | Then `async_resume_effect()` (section 7). |

### 5.2 Exit paths

Every exit calls `_leave_effect_scene()`, or `_stop_effect()` where the scene
stays, before it sends a command or changes state. Commands already queued
then fail the dispatch check (4.5). The exits that send commands also turn
off the party's effect-only lights the next scene does not list (4.6).

| path | where | change |
| --- | --- | --- |
| Another scene, off or circadian | `_activate_scene`, all three branches (`controller.py:927-978`) | `_leave_effect_scene()`, the completion barrier and the effect-only turn-off at the top of the method, before the branches (4.3, 4.5, 4.6). Each non-effect commit calls `_leave_effect_scene()` again, in case a newer party committed during its awaits (4.3). |
| `area_lighting.lighting_circadian` service | `lighting_circadian` calls `_activate_circadian` directly, with no claim (`controller.py:2228-2238`) | It bumps `_activation_seq`, calls `_leave_effect_scene()`, waits at the completion barrier and turns off the effect-only lights first. |
| Static scene entity | `AreaLightingScene.async_activate` (`scene.py:107-133`) | Awaits `ctrl.async_leave_effect_scene(slug)` before applying. It bumps `_activation_seq`, so an in-flight party activation yields, calls `_leave_effect_scene()`, waits at the completion barrier, and turns off the effect-only lights the scene does not list. No tick then lands between the entity's commands and the tracker task. |
| External scene, tracker | `handle_scene_activated` (`controller.py:2355-2372`) | `_leave_effect_scene()` in the off, circadian and static branches. |
| Select to another option | `current_scene` setter (`controller.py:553-560`) | `async_select_scene` calls `_leave_effect_scene()` first (5.1). |
| Manual latch | `handle_manual_light_change` (`controller.py:2417-2425`) | `_leave_effect_scene()` beside `_stop_circadian()`. Manual detection skips an area in an effect scene, dimmed or not (6.5), so this call is a safeguard for a future caller. |
| All lights off | `handle_lights_all_off` (`controller.py:2376-2395`) | `_leave_effect_scene()` beside `_stop_circadian()`. |
| Raise or lower | `_adjust_brightness` (`controller.py:2287-2288`), `_bring_dark_area_to_min` (`controller.py:2326-2327`) | `_stop_effect()` and a bump of `_activation_seq` where circadian stops. The scene stays, dimmed, and the epoch does not move (4.5, 6.6). |
| Motion or occupancy fade | `lighting_off_fade` (`controller.py:2114-2151`) | `_leave_effect_scene()` beside the `_stop_circadian()` at `controller.py:2126`. The motion timer never gets here from an effect scene (6.3). |
| Demand response turns on | `reactivate_for_demand_response` (`controller.py:1397-1451`) | Activates the demand response default, which leaves the effect scene through `_activate_scene` (6.2). |
| Scene removed by reload | `_leave_removed_scene` (`controller.py:413-432`) | Covered: `apply_config` stops the effect first. |
| Preset changed, or `effect:` removed, on reload | `apply_config` (`controller.py:213-245`) | `_stop_effect()` beside `_stop_circadian()` at `controller.py:224`, and a bump of `_activation_seq`. The epoch does not move. The resume restarts a still-valid effect (section 7). |
| Area removed on reload | `shutdown(persist=True)` (`__init__.py:307-308`) | `shutdown` calls `_stop_effect()` (`controller.py:2938-2952`). |
| Unload | `async_teardown` calls `ctrl.shutdown()` (`__init__.py:401-422`) | The same call. |
| Alert start | `execute_alert` sets `_alert_active` (`alert.py:273`) | A pause, not an exit. Ticks skip while an alert holds the area (6.1). |

## 6. Interactions

### 6.1 Alerts

An alert pauses the effect. Alerts on one area can overlap. The service
gathers one `execute_alert` per controller (`services.py:160-167`), two
service calls can target the same area, and `_alert_active` is one shared
boolean (`alert.py:273`) that the first completion clears
(`controller.py:1623`). So the effect counts alerts itself:

- `execute_alert` increments `controller._alert_holds` where it sets the flag,
  and decrements it in its `finally` before it calls `async_end_alert`. When
  the count rises from zero, it records `_alert_epoch_base`, the current
  `_effect_scene_epoch`.
- An alert holds the area while `_alert_active` is set or `_alert_holds` is
  above zero. Ticks keep their schedule and skip their frames,
  `async_effect_call` drops queued commands, and `async_resume_effect()`
  returns.
- The generation does not change when an alert starts.
- The alert's light list (`alert.py:255`) leaves out effect-only lights that
  are out of play (4.6). In a party they are flashed, captured and restored
  like the other participants.
- The list is captured once, and each step reuses it after the previous
  step's sleep (`alert.py:153`, `alert.py:167`). A user can leave the party
  in that sleep, and the leave turns the stoplight off (4.6). So
  `_execute_steps` drops lights that are no longer in play from each step's
  targets before it builds the batch, and the restore drops them from the
  capture. Validation keeps effect-only lights out of clusters (3.3), so
  coalescing (`alert.py:213`) cannot bring one back through an aggregate.

The alert captures the party colours (`alert.py:277`), runs its steps, and
restores them (`alert.py:294-299`). The completion that brings `_alert_holds`
to zero then resumes the effect through `async_resume_effect()`, called
beside `async_resume_circadian()` (`controller.py:1643`). If demand response
is still on, the re-drive at `controller.py:1626-1639` runs instead, and it
moves the area to its demand response default (6.2). If it turned on and off
again during the alert, the exit it owed still applies (6.2). An earlier
completion behaves as today and leaves the effect paused. Each alert
still restores its own capture, so a later alert can restore colours captured
during an earlier one's flashes. The resumed effect replaces them at its first
frame, except on a participant captured off, which stays off until the next
activation.

**Supersession.** The alert restores its saved `AreaState` and targets
unconditionally (`alert.py:308-309`). Remotes, the select and scene entities
do not check `_alert_active`, so a user can enter or leave a party during an
alert, and the restore would undo that choice. The fix:

1. Before the light restore at `alert.py:294`, and again in the `finally`
   block, `execute_alert` compares `_effect_scene_epoch` with
   `_alert_epoch_base`. A move means the area entered or left an effect scene
   while some alert held it (4.5). The epoch reads the state, not the runner,
   so leaving a party that was dimmed and frozen before the alert counts too.
2. If it moved, the alert skips its light restore, leaves the controller's
   current state and targets in place (skipping `alert.py:308-309`), and
   passes `superseded=True` to `async_end_alert`.
3. Only the completion that brings `_alert_holds` to zero acts on
   `superseded`, and an owed demand response exit takes precedence over it
   (6.2). Otherwise, instead of the demand response and circadian branches, it
   clears the flag, waits at the completion barrier, and re-applies the
   current state with `propagate_to_followers=False` and
   `yield_if_superseded=True`, as the demand response re-drive does
   (`controller.py:1446-1450`). An effect or static scene goes through
   `_activate_scene`, off through `_activate_scene(SCENE_OFF_INTERNAL)`, and
   circadian through `_activate_circadian`. A manual area is left alone.
   Under demand response, the effect branch's guard turns an effect re-apply
   into the default (4.3, step 1), and a static re-apply sheds as usual.

Each re-apply yields to a newer user action. The effect branch checks its
claim after every await (4.3), and the visual path honours the flag
(`controller.py:961-972`). The off branch gains the same claim check after
`_turn_off_all_lights` when the flag is set. No caller passes the flag for
off today, so nothing else changes. Circadian needs no check, because each of
its commands passes its own generation check (`controller.py:1830-1846`) and
a later action stops it. A replay with the flag does not move the epoch
(4.5), so it cannot mark a later alert superseded.

Raise, lower and reload stop the effect but leave the epoch alone (4.5), so
they are not supersession:

- Raise or lower during an alert. The alert restores its saved state, which
  predates the dim, and its captured colours. The effect then resumes. The
  dim is lost, as any dim during an alert is today (decision 11), because the
  saved `AreaState` carries the old `dimmed` flag (`alert.py:274`,
  `area_state.py:187-194`).
- Reload during an alert. `apply_config` stops the runner. The alert restores
  its snapshot, and its `_leave_removed_scene` call handles a removed scene
  (`alert.py:310-312`). The resume then follows section 7. A changed preset
  restarts on the lit participants, and a scene that lost `effect:` keeps the
  restored frame.

The cases:

- Party, then off during the alert. The alert skips its restore and applies
  the off again.
- A party dimmed before the alert, then off during it. No runner was active,
  but the state named an effect scene, so the off moved the epoch. The alert
  applies the off again.
- Evening, then party during the alert. The effect started with ticks paused.
  The alert keeps `party` and re-applies it.
- Two overlapping alerts, and off during the first. Neither restores, and the
  second completion applies the off.
- No move into or out of an effect scene. The alert behaves as it does today,
  and the effect resumes when the last hold is released.
- Party, then demand response on and off during the alert. The alert
  restores the party's colours, and the completion moves the area to its
  default instead of resuming the effect.
- Party, then a switch to `study` during the alert's sleep. Later steps skip
  the stoplight, which the switch turned off, and the alert re-applies
  `study`.

Static scenes chosen during an alert, with no effect scene involved, still
lose to the alert's restore, as they do today (decision 11). Serialising
overlapping alerts beyond the effect's pause and resume is a non-goal.

### 6.2 Demand response

Demand response and effect scenes are mutually exclusive (decision 9). While
the global `demand_response_active` flag is on, no path starts an effect, and
an area already in an undimmed effect scene leaves it. An effect scene
therefore never sheds: `_dr_shed_ids` is empty in one, and the participants,
frames and dispatch check ignore shedding.

**The default.** `_demand_response_default()` picks the scene an area moves
to. It runs `_default_sequence` (`scene_machine.py:80-89`) over the area's
scene slugs less its effect slugs, with the current holiday mode and night
mode, and resolves the action as `_resolve_and_activate` does
(`controller.py:1547-1565`): a holiday action through `_area_holiday`
(`controller.py:791-793`) and a sun position through `resolve_sun_position`
(`scene_machine.py:280`). A no-op becomes `off_internal`. A holiday mode that
names an effect scene already reads as `none` (6.7). The default never names
an effect scene, and it sheds as any static or circadian activation does.

**One guard.** The effect branch of `_activate_scene` checks the flag before
its first command and again before it commits (4.3, steps 1 and 6). With the
flag on, it activates the default instead, with the same source, transition
and flags. Paths that should take the area to its default rely on this guard.
Paths that should refuse, skip or keep check the flag first, so the guard
never sees them. The guard also covers a flip that lands mid-activation, such
as an off area entering a party when the re-drive skips it as off
(`controller.py:1441`).

**Turning on.** The setter flips the flag and schedules
`reactivate_for_demand_response` for every controller as a task
(`global_state.py:101-112`). For an area in an undimmed effect scene, the
re-drive calls `_activate_scene` with the scene's slug, the area's source,
`propagate_to_followers=False` and `yield_if_superseded=True`, as today
(`controller.py:1446-1451`). The guard turns that into the default. The
activation leaves the effect scene, turns off the party's effect-only lights
(4.6), and sheds. A tick running when the flag flips drops its remaining
commands at the dispatch check (4.5).

A dimmed party is skipped, as every dimmed area is (`controller.py:1441`).
Its effect already stopped when it was dimmed, so nothing animates under
demand response, and its frozen frame stays unshed. That is the existing
rule for every dimmed area. A re-drive would clear `dimmed` and relight the
room at scene brightness as the shed starts (`controller.py:1408-1417`). The
state still names the party, so its effect-only lights stay in play, and
manual detection keeps skipping the area (6.5). The next `on` or dark raise
goes through the guard to the default. A startup resume skips a dimmed party
too (section 7).

**An alert defers the exit.** The re-drive returns for an area an alert
holds (`controller.py:1441`), and the setter keeps no record of the edge
(`global_state.py:101-112`). An alert end checks only the current flag and
shed set (`controller.py:1626`), and an effect scene sheds nothing. So
without a record, party, alert, demand response on, demand response off and
the alert's end would resume the party, though the edge should have ended
it. The re-drive therefore sets `_effect_exit_pending` when it skips an
alert-held area that is in an undimmed effect scene with the flag on. The
completion that brings `_alert_holds` to zero reads it first. If it is set
and the area is still in an undimmed effect scene, the completion activates
`_demand_response_default()` directly, with `propagate_to_followers=False`
and `yield_if_superseded=True`, whatever the flag reads now, and skips the
superseded re-apply and the resume. It clears the record either way. An
effect entry that commits clears it too (4.3, step 7, and the select in
5.1), so a party the user starts after demand response turned off, while the
alert still runs, survives the alert's end. The record is not persisted. A
restart mid-alert loses the alert's restore as well.

**Turning off.** Nothing restarts the party. The re-drive returns early for
an area in an effect scene while the flag is off, because that area has
nothing shed to relight and a re-activation would only restart the effect
with every participant turned on. That case arises when a party starts after
the flag turns off and before the area's re-drive task runs. An area that
demand response moved to its default stays there.

**Per path.**

| path | under demand response |
| --- | --- |
| Scene entity | `async_activate` raises `HomeAssistantError("Den Party is unavailable while demand response is active")`, naming the entity. Nothing changes, and the `scene.turn_on` call fails with that message. |
| Select | `async_select_scene` raises the same error before it changes anything. The select keeps showing the current scene, because its state reads the controller. |
| Favorite cycle | `lighting_favorite` cycles a copy of the remote's list without effect slugs. If none is left, the press does nothing and logs INFO. A single `scene.` entry that names an effect scene entity of any area also does nothing and logs INFO, since `_call_service` is blocking (`controller.py:820`) and the entity's error would otherwise escape into the remote handler. |
| `buttons.on` | `lighting_on` passes `determine_on_sequence_action` the sequence without effect slugs. If none is left, the press runs `determine_on_action`, as for a remote without `buttons.on`. Otherwise `on_sequence[0]` (`scene_machine.py:186`) would raise on an empty list. |
| Default `on` cycle | No change. It never picks an effect scene (6.4). |
| `on` while dimmed in a party | `lighting_on` restores the previous scene (`controller.py:2063-2069`). The guard turns that into the default. |
| Raise or lower from dark | `_bring_dark_area_to_min` restores a remembered party (`controller.py:2311-2316`). The guard turns that into the default, which it then dims. |
| Follower | `handle_leader_change` ignores a leader's effect slug, keeps its own scene and logs DEBUG. Scenario A ignores an effect hint and runs the follower's own cycle. |
| Startup and reload | `async_resume_effect()` re-activates an undimmed effect scene with `yield_if_superseded=True`, and the guard turns that into the default, as the edge would have (section 7). |
| Alert end | The demand response re-drive, or the superseded re-apply, goes to the default through the guard (6.1). An exit owed from an edge during the alert goes to the default even after the flag turned off. |

The parts of the earlier design that shed participants are gone: the effect
shed set, the shed bulbs switched off by the select and every resume, the
shed checks in the dispatch check and in each tick, and the manual detection
exception for a relit shed bulb. What stays is the one flag read in the
dispatch check, for the gap between the flip and the re-drive task.

### 6.3 Timers

The occupancy timer applies as in any lit scene. An effect scene is not
ambient-like, so `_enforce_occupancy_timer` arms it when every occupancy
sensor is clear (`controller.py:2697-2717`). On expiry, `lighting_off_fade`
leaves the effect scene and fades the room, or falls back to ambient
(`controller.py:2919-2930`).

Without a guard, a motion timer already running when the party starts would
fade it. `_on_motion_timer` serves both motion timers. It cleans up linked
areas and then fades whatever the area shows (`controller.py:2914-2917`), and
a static scene activation does not cancel it.

Cancelling the timer at party entry would strand the linked areas. Their only
cleanup is that callback: `_activate_linked_areas` records them
(`controller.py:2678-2679`), and the occupancy timer never cleans them up. So
an effect entry leaves the timer running, and the callback changes instead:

```python
async def _on_motion_timer(self) -> None:
    await self._cleanup_linked_areas()
    if self.effect_scene_active:
        return  # a party never fades on a motion timer
    await self.lighting_off_fade(source=ActivationSource.MOTION)
```

The check runs after the cleanup's awaits, against the current state. That
covers both late paths. `TimerHandle._fire` can run after a cancel, because a
cancel cannot retract a task the loop already queued
(`timer_manager.py:118-119`). A callback suspended in the linked cleanup can
also resume after a party started. Neither fades the party, and both still
clean up on time.

No later motion timer starts, because `handle_motion_off` starts one only for
a motion-owned state (`controller.py:2614`), and motion never owns an effect
scene. Static scenes keep today's behaviour. A party that ends in a static
scene before the inherited timer fires is faded by it, as that static scene
would be today.

### 6.4 Followers, leaders and motion

A leader in `party` propagates the slug as `SCENE_ACTIVATED`
(`controller.py:984-987`). A follower that defines `party` and is lit, and not
ambient-like or manual, activates it with source `LEADER`
(`controller.py:2876-2893`). One without the slug logs a warning and keeps
its scene (`controller.py:2884-2892`). In haconf the pantry follows the kitchen
(`haconf:area_lighting.yaml:4887-4889`) and defines no party, so each kitchen
party logs that warning once. Under demand response a follower ignores an
effect slug (6.2).

Motion never picks an effect scene:

- Every slug an automatic path picks by name is reserved (decision 7):
  `night` for night mode, `daylight` and `evening` for the sun position, and
  the holiday and ambient slugs. Holiday resolution ignores effect scenes
  (6.7).
- `_non_night_default` (`scene_machine.py:67-77`) falls back to the
  alphabetically first generic scene. An area with no circadian scene and no
  daylight and evening pair could get `christmas_party`. `determine_on_action`
  and `determine_on_sequence_action` gain an `effect_slugs` keyword, passed
  down to that fallback, which removes them. The controller passes
  `self.area.effect_scene_slugs`. Every haconf area in section 10 has a
  circadian scene, so this changes nothing there today.
- Scenario A (`controller.py:2022-2032`) mirrors the leader's slug for any
  source. With source `MOTION`, an effect hint is ignored and the area falls
  through to its own default.
- `linked_motion` may not name an effect scene (3.3), so
  `handle_motion_on` (`controller.py:2583-2586`) and `_activate_linked_areas`
  (`controller.py:2660-2679`) cannot reach one.
- The motion gate rejects an effect scene, because its source is never
  `MOTION` and its slug is not in the ambient tuple
  (`event_handlers.py:635-646`).

### 6.5 Manual detection and self-heal

The handler at `event_handlers.py:456-608` gets two skips. The first sits
beside the circadian one (`event_handlers.py:524-526`):

```python
if ctrl.effect_scene_active:
    _skip("area is in an effect scene", entity_id)
    return
```

`effect_scene_active` reads the state: the area is in `SCENE` and its scene
has an effect. It does not read whether the runner is ticking, so the skip
also covers the gap between a restart and the resume. Circadian lets a shed
bulb through its skip. The effect needs no such exception, because no bulb is
shed in an effect scene (6.2).

The second sits beside the `group_exclude` skip (`event_handlers.py:535-537`)
and covers effect-only lights outside their scenes (4.6):

```python
if not ctrl.light_in_play(entity_id):
    _skip("effect-only light outside its scenes", entity_id)
    return
```

`_heal_suppressed` (`controller.py:2445-2467`) returns `"effect scene"` for an
effect scene. With no targets nothing would heal anyway. The reason makes the
skip show in the log.

The effect needs no command stamps or echo window. Circadian stamps commands
(`controller.py:1845`) only because its light listener must ignore its own
echoes (`controller.py:1677`). The effect has no light listener.

### 6.6 Raise and lower

`_adjust_brightness` stops the effect and bumps `_activation_seq` where it
stops circadian, marks the area dimmed, and steps the lit bulbs
(`controller.py:2287-2300`). The colours freeze at the last frame. The bump
makes a party activation still in flight yield (4.5).
`_bring_dark_area_to_min` does both after restoring the scene, as it stops
circadian there (`controller.py:2326-2327`). Its own restore has finished
by then, so the bump only stops older activations. The next `on`
clears `dimmed` and re-activates the scene (`controller.py:2063-2069`), which
restarts the effect. A dimmed party is persisted dimmed and does not resume
after a restart.

A dark area raised back into a party needs effect-aware membership.
`_bring_dark_area_to_min` lights `_scene_bulb_ids(slug)`
(`controller.py:2332-2339`), which resolves through
`_resolve_raw_scene_targets` (`controller.py:1922`). That resolver prefers a
stored snapshot, then `entities:`, then role membership
(`controller.py:1295-1318`). For a party without `entities:`, the skeleton
turns on every light whose `scenes:` list is empty or names the slug
(`models.py:47-49`), white lamps included. `_scene_bulb_ids` therefore gets
an effect branch. For an effect scene it returns the participants plus the
static entries with `state: "on"`, computed as in 4.2 and ordered as
declared, and ignores any snapshot. If that set is empty, it falls back to
`_individual_light_ids()`, as today (`controller.py:2333-2334`), less the
effect-only lights out of play (4.6), so dim-up still lights the room.

### 6.7 Holiday and ambience

An effect scene is a lit, user-owned scene. It is not ambient-like
(`area_state.py:63`), not a holiday scene for `handle_holiday_changed`
(`controller.py:2830-2842`), and not excluded from `is_occupied`
(`controller.py:529-532`). Changing `input_select.holiday_mode` mid-party does
nothing, because that handler acts only on an off area or a holiday-owned
holiday scene. `christmas_party` is a slug like any other.

`_area_holiday` (`controller.py:791-793`) accepts any configured slug, so a
`holiday_mode` option named `christmas_party` would activate that party from
off through `handle_holiday_changed` (`controller.py:2823-2835`), and through
every default and ambience path that reads `_get_holiday_mode()`
(`controller.py:789`). `_area_holiday` therefore treats a mode that names an
effect scene as `none`. Every holiday path goes through it, so this one check
covers `_default_sequence`, `determine_on_sequence_action`, the ambient
fallback in `determine_off_action`, `handle_ambient_enabled` and the demand
response default (6.2). Areas without effect scenes see no change.

`lighting_off` from a party falls back to ambient when ambience is on
(`scene_machine.py:225-233`), as from any lit scene.

### 6.8 Radio load

Each tick sends one `light.turn_on` per lit participant, or fewer with `sync`
coalescing. Colour capable lights per area, read from the live Home Assistant
on 2026-10-13 (capability from `supported_color_modes`, bridge from the device
registry's `via_device`):

| area | colour lights | Downstairs Hue | Upstairs Hue | other |
| --- | --- | --- | --- | --- |
| theater | 8 | | 4 | 4 |
| den | 10 | 6 | | 4 |
| media_room | 14 | 9 | | 5 |
| living_room | 8 | 5 | | 3 |
| kitchen | 10 | 10 | | |
| office | 10 | | 9 | 1 |
| main_bedroom | 13 | | 13 | |
| craft_room | 5 | | 5 | |
| total | 78 | 30 | 31 | 17 |

The den's "other" four are the three WLED stoplight segments and the desk
clock, which haconf adds as effect-only lights (10.2). The clock read
`unavailable` on 2026-10-13, so it sits out until it returns (4.2).

Philips's guidance for the Hue bridge is about 10 light commands a second.
One main bedroom party at the 2-second floor sends 6.5 a second to the
Upstairs bridge. Office and main bedroom together at 2 seconds send 11. At the
5-second default, every party on one bridge at once sends about 6. haconf's
presets use 5 seconds.

There is no cross-area limiter (section 11). The per-area guarantees are the
floor, no overlapping ticks, one runner per area, and an interval measured
from the end of the previous tick.

## 7. Startup and reload

**Startup.** `_on_started` runs `restore_timers`, `reconcile_startup_state`
and `async_resume_circadian` for each controller (`__init__.py:213-228`). A new
loop after them calls `async_resume_effect()`.

- `_rebuild_scene_tracking` (`controller.py:434-438`) gets an effect branch:
  empty targets and an empty shed set. `reconcile_startup_state` calls it for
  a persisted scene (`controller.py:395-396`). Its check for lit bulbs in an
  off area skips effect-only lights (4.6).
- `async_resume_effect()` returns unless the controller is open, no alert holds
  the area (6.1), the area is in an effect scene and not dimmed, and the
  runner is not already active. Those are the conditions of
  `async_resume_circadian` (`controller.py:1655`), plus the alert count.
  Under demand response it then calls `_activate_scene` for the scene with
  `propagate_to_followers=False` and `yield_if_superseded=True`, and the
  guard moves the area to its default (6.2). Otherwise it calls
  `_start_effect(turn_on=False)`, which updates the participants that are on
  and turns on nothing.
- A past-due timer restored at startup fires in its own task
  (`timer_manager.py:74-107`) and may run after the resume. The occupancy
  timer fades the room and stops the effect, which is correct. The motion
  timer only cleans up linked areas (6.3).

**Reload.** `apply_config` stops the effect beside circadian
(`controller.py:224`) and bumps `_activation_seq`, so an activation still in
flight under the old config yields (4.5). Then `_leave_removed_scene` switches
an area whose scene is gone to manual, and `_rebuild_scene_tracking` rebuilds
the rest (`controller.py:241-243`). `_async_reload` resumes circadian for
kept areas (`__init__.py:331-332`) and for added ones after their timers and
reconcile (`__init__.py:333-337`). `async_resume_effect()` follows each of
those calls.

| change | result |
| --- | --- |
| Preset parameters changed | The effect restarts with the new preset at once, on the lit participants. |
| Scene's `effect:` removed | The scene is static now. The lights keep the last frame until the next activation, and manual detection compares against the static targets again. |
| Scene's `effect:` added | The area, if in that scene, starts the effect on its lit participants. |
| Effect scene removed | The area switches to manual (`controller.py:413-432`). |
| Preset removed while a scene still names it | Validation fails, and the reload changes nothing (3.3). |
| Area removed | `shutdown(persist=True)` stops the effect. |
| Participants changed (`entities:`, `group_exclude`, lights, `scenes:` lists) | The resume recomputes them, and the effect-only set is rebuilt from the new config. A light that becomes effect-only drops out of every stored snapshot of a static scene at its next use (4.6). Validation rejects a scope change that leaves it in a cluster or kelvin route (3.3). |
| Any of the above during demand response | The resume moves an area still in an undimmed effect scene to its default (6.2). |
| Any of the above during an alert | The runner stops, the epoch stays, and the alert restores its snapshot. The resume at the last alert completion applies the row above (6.1). |

Effect scenes keep the `AreaLightingScene` class, so a static scene that gains
`effect:` keeps its entity, and `_refresh_entities` hands it the new config
(`__init__.py:376-392`). No entity is added or removed.

Persistence does not change. The scene slug and `dimmed` are already saved
(`area_state.py:187-194`).

## 8. Observability

`diagnostic_snapshot` (`controller.py:469-506`) gains:

- `effect_preset`: the running preset's name, or `None`
- `effect_running`: whether the runner is active
- `effect_participants`: the participant list for this run, in order
- `effect_only_lights`: the area's effect-only lights, each with whether it
  is in play
- `effect_tick_count`: ticks sent since the effect started
- `effect_generation` and `effect_scene_epoch`
- `alert_holds` and `effect_exit_pending`

The diagnostic sensor already writes its state every second
(`diagnostics.py:28`, `diagnostics.py:121-122`), so a tick counter adds no
writes.

An effect scene's entity reports `scene_source: effect`, beside `snapshot`,
`config` and `skeleton` (`scene.py:96-105`). Its default icon is
`mdi:party-popper`. A configured `icon:` still wins (`scene.py:92`).

Logging:

- INFO when an effect starts: area, preset, participant count, and whether
  it turned lights on.
- INFO when it stops, with the reason: scene change, select, manual, all off,
  dim, fade, demand response, reload, shutdown.
- INFO when demand response refuses or skips an effect scene, naming the path
  (6.2), and when it moves an area to its default.
- DEBUG once per tick: area, generation, lights sent, lights skipped as off
  or unavailable.
- DEBUG when a tick is skipped for an alert, or a command is dropped by the
  dispatch check.
- WARNING per failed command, as circadian logs them
  (`controller.py:1762-1769`).
- WARNING when the completion barrier times out (4.5).
- DEBUG when an effect activation yields to a newer claim, and when an alert
  ends superseded.

## 9. Test strategy

**Unit tests, `tests/test_effects.py`.** `next_frame` with a seeded
`random.Random`:

- `test_random_frame_gives_each_light_its_own_color`
- `test_random_frame_channels_in_range`
- `test_palette_frame_uses_only_palette_colors`
- `test_palette_frame_never_repeats_previous_color`
- `test_single_color_palette_repeats`
- `test_sync_frame_shares_color_and_brightness`
- `test_brightness_range_is_inclusive`
- `test_fixed_brightness_is_sent_every_tick`

**Schema, `tests/test_effect_schema.py`.**

- `test_interval_below_minimum_rejected`
- `test_transition_longer_than_interval_rejected`
- `test_palette_requires_colors` and `test_random_rejects_colors`
- `test_palette_repeated_color_rejected`
- `test_brightness_range_order_enforced`
- `test_preset_name_must_match_id_pattern`
- `test_scene_effect_unknown_preset_rejected`
- `test_effect_on_reserved_slug_rejected`, one case per slug, `night`,
  `daylight` and `evening` included
- `test_effect_scene_entities_cannot_name_cluster`, one case for
  `light_clusters` and one for a `lights:` entry with `members`
- `test_effect_scene_entities_must_be_declared_individual_lights`, covering an
  undeclared cluster member and a light from another area
- `test_effect_scene_entities_cannot_name_light_scoped_elsewhere`
- `test_static_scene_entities_cannot_name_effect_only_light`
- `test_effect_scoped_light_cannot_name_unknown_slug`
- `test_cluster_cannot_be_scoped_to_effect_scene`
- `test_cluster_cannot_contain_effect_only_light`, one case for
  `light_clusters` and one for a `lights:` entry with `members`
- `test_kelvin_route_cannot_name_effect_only_light`, directly and through a
  cluster
- `test_linked_motion_cannot_name_effect_scene`, local and remote
- `test_effect_scene_allowed_in_favorite_and_on_lists`
- `test_no_effects_key_parses_to_empty`
- `test_effect_only_light_ids`, covering a light scoped to effect scenes only,
  one that mixes an effect and a static scene, and an unscoped light

**Scene machine, `tests/test_scene_machine.py`.**
`test_generic_default_skips_effect_scenes`,
`test_on_from_effect_scene_goes_to_default`. In
`tests/integration/test_holiday_membership.py`:
`test_holiday_mode_matching_effect_scene_is_ignored`, which sets the helper
to `christmas_party` with the area off and checks no effect starts.

**Integration, `tests/integration/test_scene_effects.py`.** Time moves with
`async_fire_time_changed`, since ticks use `async_call_later`. Entry:

- `test_scene_entity_starts_effect_and_turns_participants_on`
- `test_select_option_starts_effect_without_turning_lights_on`
- `test_consecutive_selections_leave_only_last_effect_running`
- `test_select_party_arms_occupancy_timer_without_sensor_change`
- `test_favorite_cycle_enters_effect_scene`
- `test_on_sequence_enters_effect_scene`
- `test_dimmed_party_restored_by_on_restarts_effect`
- `test_follower_with_party_scene_joins_leader`
- `test_follower_without_party_scene_keeps_its_scene`
- `test_motion_never_starts_effect`, covering the generic fallback and a
  leader in party
- `test_participants_skip_light_scoped_to_other_scenes`

Exit and generation:

- `test_other_scene_stops_effect_and_no_tick_follows`
- `test_scene_change_during_awaited_tick_drops_remaining_commands`
- `test_off_during_static_part_keeps_party_from_starting`, for a remote off,
  a select and a reload
- `test_held_tick_command_completing_during_handoff`: a stubbed service
  holds one tick command, a static scene waits at the barrier, and its
  commands land after the held one
- `test_handoff_barrier_times_out_and_logs_warning`
- `test_bulb_switched_off_after_frame_built_is_not_relit`
- `test_coalesced_cluster_falls_back_when_member_turns_off`
- `test_static_scene_entity_leaves_effect_before_applying`
- `test_select_manual_stops_effect`
- `test_all_lights_off_stops_effect`
- `test_raise_freezes_colors_and_marks_dimmed`
- `test_occupancy_timeout_fades_party`
- `test_motion_timer_during_party_cleans_linked_areas_without_fading`
- `test_queued_motion_timer_fire_does_not_fade_party`
- `test_motion_timer_suspended_in_linked_cleanup_does_not_fade_party`
- `test_tick_skips_off_and_unavailable_participants`
- `test_ticks_do_not_overlap_when_sends_are_slow`
- `test_participants_without_entities_are_color_lights_only`
- `test_non_participant_entities_apply_once`
- `test_excluded_on_entry_gets_no_command`
- `test_unavailable_entry_gets_no_command`
- `test_dim_up_dark_party_lights_participants_and_static_on_entries_only`
- `test_dim_up_ignores_snapshot_for_effect_scene`
- `test_sync_frame_coalesces_into_cluster`
- `test_late_static_commit_over_party_stops_effect`: a static activation
  held in `_apply_scene_data` resumes after a party started, commits, and no
  tick follows
- `test_tick_stops_runner_when_state_names_other_scene`
- `test_raise_after_party_commit_before_start_keeps_effect_stopped`
- `test_raise_during_party_static_entries_keeps_party_from_committing`

Effect-only lights, one test per row of 4.6, in an area with a static scene
that has `entities:`, a skeleton scene and a party:

- `test_effect_only_light_joins_party_without_entities`
- `test_off_from_static_scene_leaves_effect_only_light_alone`
- `test_off_from_party_turns_effect_only_light_off`
- `test_skeleton_scene_neither_commands_nor_targets_effect_only_light`
- `test_effect_only_light_change_does_not_latch_manual`, for the static scene
  and the skeleton scene
- `test_effect_only_light_on_does_not_block_all_off`
- `test_effect_only_light_does_not_hold_occupancy_lights_on`
- `test_startup_off_with_effect_only_light_on_stays_off`
- `test_raise_outside_party_does_not_step_effect_only_light`
- `test_dim_up_fallback_skips_effect_only_light`
- `test_alert_outside_party_skips_effect_only_light`
- `test_alert_in_party_restores_effect_only_light`
- `test_alert_step_after_leaving_party_skips_effect_only_light`
- `test_snapshot_scene_leaves_out_effect_only_lights`
- `test_stored_snapshot_naming_effect_only_light_is_filtered`, covering the
  scene entity, `_apply_scene_data`, the targets, the shed size and
  post-settle heal
- `test_circadian_leaves_effect_only_light_alone`
- `test_leaving_party_for_static_scene_turns_effect_only_light_off`
- `test_leaving_party_for_circadian_service_turns_effect_only_light_off`
- `test_party_to_christmas_party_keeps_effect_only_light`
- `test_select_from_party_leaves_effect_only_light_at_last_frame`
- `test_lights_out_from_static_scene_leaves_effect_only_light_alone`

Alert, beside `tests/integration/test_alert.py`:

- `test_alert_pauses_ticks_and_resumes_after_restore`
- `test_off_during_alert_is_not_undone_by_restore`
- `test_party_started_during_alert_survives_restore`
- `test_alert_without_effect_change_restores_as_before`
- `test_dimmed_party_then_off_during_alert_stays_off`
- `test_raise_during_alert_restores_pre_alert_party_and_resumes`
- `test_overlapping_alerts_resume_effect_only_after_last`
- `test_overlapping_alerts_off_during_first_is_kept`
- `test_alert_replay_yields_to_user_action`
- `test_reload_during_alert_restarts_on_lit_participants_only`
- `test_reload_removing_effect_during_alert_keeps_restored_frame`

Demand response:

- `test_scene_entity_refuses_effect_during_demand_response`
- `test_select_refuses_effect_during_demand_response`
- `test_favorite_cycle_skips_effect_scenes_during_demand_response`
- `test_favorite_cycle_of_only_effect_scenes_does_nothing_during_demand_response`,
  for a slug list and a single `scene.` entry
- `test_on_sequence_skips_effect_scenes_during_demand_response`
- `test_on_sequence_of_only_effect_scenes_uses_default_cycle`
- `test_demand_response_on_mid_party_moves_area_to_default_and_sheds`
- `test_demand_response_on_turns_effect_only_lights_off`
- `test_demand_response_off_does_not_restart_party`
- `test_party_started_before_redrive_task_after_flag_off_keeps_running`
- `test_dimmed_party_skipped_by_demand_response_and_on_goes_to_default`
- `test_demand_response_on_and_off_during_alert_moves_party_to_default`
- `test_party_entered_after_demand_response_off_during_alert_survives`
- `test_dark_raise_into_party_during_demand_response_goes_to_default`
- `test_follower_ignores_leader_party_during_demand_response`
- `test_scenario_a_ignores_effect_hint_during_demand_response`
- `test_alert_end_with_demand_response_moves_party_to_default`
- `test_off_area_entering_party_when_demand_response_turns_on_goes_to_default`
- `test_tick_in_flight_when_demand_response_turns_on_drops_commands`
- `test_dr_redrive_in_flight_yields_to_select`
- `test_dr_redrive_in_flight_yields_to_raise`

Manual detection and heal:

- `test_effect_commands_do_not_latch_manual`
- `test_no_post_settle_heal_in_effect_scene`

Startup and reload, in `tests/integration/test_reload.py` and
`tests/integration/test_persistence.py`:

- `test_restart_in_party_resumes_effect_on_lit_participants`
- `test_restart_in_dimmed_party_does_not_resume`
- `test_restart_in_party_during_demand_response_goes_to_default`
- `test_reload_changed_preset_applies_at_once`
- `test_reload_removing_effect_key_stops_effect`
- `test_reload_removing_effect_scene_switches_to_manual`
- `test_reload_removing_area_stops_effect`
- `test_reload_with_unknown_preset_raises_and_changes_nothing`

Other: `test_snapshot_scene_refuses_effect_scene`, and teardown during a
party leaves no loop timer. The harness already fails any test that leaves
one.

The gate for every commit is the repository's: from
`custom_components/area_lighting/`, `uv run ruff check .`,
`uv run ruff format --check .` and `uv run pytest -n auto -rs` with 0
skipped, then `dagger call all`.

## 10. haconf migration

The component release lands first. `CONFIG_SCHEMA` uses `PREVENT_EXTRA`
(`__init__.py:77`), so 3.1.x rejects an `effects:` key and the integration
would not load. haconf runs 3.1.1 through HACS
(`haconf:custom_components/area_lighting/manifest.json`). `tag:auto` tagged
3.1.2 for this spec's commit, a version bump with no code change, so the
feature ships as 3.2.0 from one `(Minor)` commit. Install 3.2.0 through HACS,
then deploy the config.

### 10.1 Changes

`haconf:area_lighting.yaml`:

- Add the top-level `effects:` block with `party`, `christmas_party` and
  `media_room_party` from 3.4.
- Add a `party` and a `christmas_party` scene to theater, den, media_room,
  living_room, kitchen, office, main_bedroom and craft_room.
  - theater: no `entities:`. Its 8 colour lights are the 8 the script drives.
    `light.theater_work_light` has no colour and stays out.
  - den: no `entities:`, and the four effect-only lights of 10.2. Every den
    colour light takes part: the bookcase uplight, desk lamp, both study
    lights and both overheads, plus the stoplight segments and the desk
    clock. The moon, panda and rainbow lights have no colour and stay out.
  - media_room: `party` uses `media_room_party` and lists the 13 lights the
    blueprint drives (`haconf:automations.yaml:2234-2247`). That leaves out
    `light.yellow_ikea_lamp`, the area's 14th colour light, and the 15 white
    and decorative lights. `christmas_party` lists the same 13.
  - living_room, kitchen, office, main_bedroom and craft_room: no
    `entities:`, so every colour light takes part.
- No remote buttons (decision 23).

Retire the old party mode:

- Delete `haconf:scripts/theater/party_mode.yaml` and
  `haconf:scripts/den/party_mode_a.yaml`. After `script.reload`, remove the
  orphaned `script.theater_party_mode` and `script.den_party_mode_a` registry
  entries with `config/entity_registry/remove`.
- Delete "Theater Party Mode - Start" (id `1634951580172`) and "Media Room
  Party Lights" (id `1708211647128`) in the automation editor on the host.
  `automations.yaml` is UI-owned, so replay only those two deletions onto the
  committed file, as haconf CLAUDE.md describes.
- Delete `haconf:blueprints/automation/Twanne/party_lights.yaml`. The media
  room automation is its only user.
- Delete `input_boolean.theater_party_mode`, `input_boolean.den_party_mode`
  and `input_boolean.media_room_party_mode` under Settings, Helpers. They live
  in the host's `.storage`, not in the repo. First search the host's
  `/config/.storage` for UI dashboards that still reference them.

`haconf:CLAUDE.md`: drop the `party_mode.yaml` bullet from "Three scripts that
name lights by hand", and add a short "Party scenes" section: the presets, the
participant rule, that motion never starts a party, and that demand response
refuses one. In "Den stoplight", say that the three segments are effect-only
den lights: only the den's party scenes command them, and nothing in
`area_lighting` reads them otherwise.

### 10.2 The den's effect-only lights

Append these to the den's `lights:`, after `light.den_overhead_2`:

```yaml
      - id: light.den_stoplight_red
        scenes:
          - party
          - christmas_party
      - id: light.den_stoplight_yellow
        scenes:
          - party
          - christmas_party
      - id: light.den_stoplight_green
        scenes:
          - party
          - christmas_party
      - id: light.den_desk_clock_display
        scenes:
          - party
          - christmas_party
```

None gets a `circadian_group`, `circadian_type` or roles. Every other den
scene (`ambient`, `christmas`, `daylight`, `evening`, `night`, `study`) has
an `entities:` map that does not name them, and `circadian` and `off` have
none, so no scene stanza changes (haconf CLAUDE.md, "Adding a New Light",
step 4, does not apply to an effect-only light).

Live state on 2026-10-13: the segments support `rgb` and `color_temp`, the
desk clock supports `rgb` and read `unavailable`, and
`light.den_stoplight_main` supports `brightness` only. Nothing automates the
stoplight. It is on dashboards only.

`light.den_stoplight_main` stays out, and the party shows on the stoplight
only while it is on. Home Assistant's WLED segment light sends `on` for its
own segment and turns on the master only when the device has no main light
(`homeassistant/components/wled/light.py:349-394` in the running container,
read 2026-10-13). This controller has one, so a segment can report `on` with
the master off and show nothing. Boot preset 1, All On, turns the master on.

### 10.3 Order and rollback

1. Release `area_lighting` 3.2.0 and install it through HACS. HACS's own
   commit job records it.
2. Delete the two automations and three helpers on the host. Reconcile
   `automations.yaml`.
3. Commit the config, script and blueprint changes. Deploy with the fetch and
   fast-forward from haconf CLAUDE.md. Run `ha core check`, then
   `script.reload`, `automation.reload` and `area_lighting.reload`.
4. Check each area: activate `scene.<area>_party`, then read
   `effect_participants` and `effect_running` under `per_area.<area>` of
   `sensor.area_lighting_diagnostics`. Press off and confirm
   `effect_running` goes false.
5. Check the den's effect-only lights. With the den in `study`, turn
   `light.den_stoplight_red` on from the dashboard, wait past the grace
   window, and confirm `select.den_last_scene` still reads `study`. Turn the
   den off with its remote and confirm the stoplight stays on. Start the
   party, confirm the segments animate and `effect_only_lights` reports them
   in play, then press the remote's `on` and confirm they turn off.

Rollback: remove the effect scenes, the four effect-only lights and the
`effects:` block, then `area_lighting.reload`, and only then downgrade the
component. The old scripts and blueprint come back from git if wanted.

## 11. Non-goals

- Per-area number entities for interval, transition or brightness.
- Selecting a preset from `input_select.holiday_mode`.
- Generators beyond `random` and `palette`, such as chases, fades or the
  bulbs' own effects.
- An effect on a holiday or ambient scene, such as an animated driveway.
- Any effect during demand response, shed or reduced (decision 9).
- Commanding a WLED master such as `light.den_stoplight_main`.
- A cross-area or per-bridge rate limiter.
- Folding circadian into the effect runner. Circadian has kelvin routes,
  demand response recompute and sleep listeners (`controller.py:1118-1166`,
  `controller.py:1585-1593`).
- An overlay or owner abstraction over alert, demand response and effects.
- Fixing alert restore for static scenes chosen during an alert, or
  serialising overlapping alerts beyond the effect's own hold count
  (`services.py:160-167`).
- Consolidating the holiday slug sets, and the motion gate mismatch
  (decision 6).
- Waking roles, or fixing the docs that claim roles pick scene lights
  (`scene.py:5`, `scene.py:132`, `scene.py:206`).

## 12. Risks

- **Bridge saturation.** Several parties on one bridge at a short interval can
  exceed the Hue guidance (6.8). The floor and haconf's 5-second presets keep
  normal use under it. Nothing stops a user setting every preset to 2 seconds.
- **Manual changes are invisible during a party.** With detection skipped, a
  bulb changed in the Hue app gets overwritten at the next tick, and a changed
  non-participant is not noticed. A remote press, the select or a scene ends
  the party, and the README must say so.
- **Commands in flight cannot be recalled.** A frame command already inside
  its service call can land after a wall switch turns every light off, since
  the all-off handler sends nothing and does not wait. The completion barrier
  covers scene changes and alert ends only up to its bound (4.5). After a
  timeout, off and circadian have no self-check to undo a late command.
  Static scene activation has the same race today.
- **Effect-only lights reach into eleven existing paths.** Section 4.6 lists
  them, and validation keeps them out of clusters and kelvin routes (3.3). A
  path missed now, or one added later that walks `area.lights` or
  `area.all_lights`, would command the stoplight or latch the den. Each row
  has its own test, and the README must tell contributors to call
  `light_in_play` in any new path that walks those lists.
- **Some exits leave effect-only lights lit.** The select sends no commands,
  and neither does the leave before a late non-effect commit (4.3). After
  either, the stoplight stays at its last frame, out of play, until the
  dashboard or the next party changes it.
- **A dimmed party stays unshed under demand response.** The re-drive skips
  every dimmed area (6.2), so a frozen party keeps its frame, and its
  effect-only lights, until the next `on`. Nothing animates.
- **Demand response at startup can turn lights on.** A party persisted
  across a restart while demand response is active resumes as the default,
  which turns on that scene's lights even if a power cut left the bulbs off.
- **The stoplight needs its master on.** `area_lighting` never commands
  `light.den_stoplight_main`, so with the master off the segments report on
  and show nothing (10.2).
- **Alert supersession is a new rule.** It changes alert ends only when the
  area entered or left an effect scene during an alert, but it is a second
  restore path in `execute_alert`, and it needs the tests in section 9.
- **The select, raise and lower now take an activation claim.** They must,
  so a selection or a dim supersedes an in-flight party activation (4.5). As
  a side effect, a demand response re-drive or an alert re-apply of a static
  scene in flight yields to them instead of committing over them. After a
  dim, that leaves the area unshed, as the re-drive already leaves every
  dimmed area (`controller.py:1408-1417`).
  `test_dr_redrive_in_flight_yields_to_select` and
  `test_dr_redrive_in_flight_yields_to_raise` cover it.
- **Follower warnings.** A follower without the slug logs a warning on every
  leader party. That is the pantry today.
- **A static scene gaining `effect:` keeps its stored snapshot.** The effect
  ignores it (decision 21), but it comes back if `effect:` is removed later.
- **Reload stops and restarts the effect on every kept party area,** which
  sends one extra frame each.

## 13. Key file references

| file | lines | what |
| --- | --- | --- |
| `controller.py` | 213-245 | `apply_config` |
| `controller.py` | 369-438 | startup reconcile, removed scene, tracking rebuild |
| `controller.py` | 469-506 | `diagnostic_snapshot` |
| `controller.py` | 529-532 | `is_occupied` |
| `controller.py` | 543-561 | `current_scene` setter |
| `controller.py` | 580-598 | `_resolve_leader_on_slug` |
| `controller.py` | 788-793 | `_get_holiday_mode`, `_area_holiday` |
| `controller.py` | 809-820 | `_call_service` |
| `controller.py` | 881-987 | `_activate_scene` and follower propagation |
| `controller.py` | 1004-1038 | circadian on-set and shed set |
| `controller.py` | 1096-1103 | `_circadian_payload` |
| `controller.py` | 1168-1258 | `_apply_scene_data`, stored and skeleton apply |
| `controller.py` | 1261-1276 | `_apply_light_state` |
| `controller.py` | 1295-1318 | `_resolve_raw_scene_targets` |
| `controller.py` | 1380-1395 | `_compute_scene_shed_ids` |
| `controller.py` | 1397-1451 | `reactivate_for_demand_response` |
| `controller.py` | 1453-1463 | `state_matches_scene_target` |
| `controller.py` | 1537-1545 | `_turn_off_all_lights` |
| `controller.py` | 1547-1565 | `_resolve_and_activate` |
| `controller.py` | 1569-1608 | circadian start and stop |
| `controller.py` | 1610-1666 | `async_end_alert`, `async_resume_circadian` |
| `controller.py` | 1717-1795 | circadian tick, sends, `_circadian_adjustable` |
| `controller.py` | 1830-1846 | `async_circadian_call` |
| `controller.py` | 1858-1910 | `_individual_light_ids`, `_on_light_entity_ids`, stepping |
| `controller.py` | 1912-1928 | `_scene_bulb_ids` |
| `controller.py` | 1998-2071 | `lighting_on` |
| `controller.py` | 2114-2169 | `lighting_off_fade`, `lighting_force_off` |
| `controller.py` | 2171-2227 | `lighting_favorite` |
| `controller.py` | 2228-2238 | `lighting_circadian` |
| `controller.py` | 2271-2340 | raise and lower |
| `controller.py` | 2344-2425 | tracker, all off, manual latch |
| `controller.py` | 2426-2490 | post-settle self-check, `_heal_suppressed` |
| `controller.py` | 2572-2626 | motion on and off |
| `controller.py` | 2660-2695 | linked areas and their cleanup |
| `controller.py` | 2697-2717 | `_enforce_occupancy_timer` |
| `controller.py` | 2758-2764 | occupancy light edges |
| `controller.py` | 2844-2910 | `handle_leader_change` |
| `controller.py` | 2914-2930 | timer callbacks |
| `controller.py` | 2938-2952 | `shutdown` |
| `global_state.py` | 101-112 | demand response setter |
| `timer_manager.py` | 118-126 | `_fire` after a cancel |
| `area_state.py` | 138-148, 187-194 | `transition_to_scene`, persisted fields |
| `event_handlers.py` | 278-307 | all-off and manual detection subscriptions |
| `event_handlers.py` | 376-420 | `scene.turn_on` tracker |
| `event_handlers.py` | 426-448 | all-off detection |
| `event_handlers.py` | 456-608 | manual detection |
| `event_handlers.py` | 614-695 | motion gate |
| `event_handlers.py` | 733-760 | occupancy light edges |
| `event_handlers.py` | 844-917 | remote dispatch |
| `scene.py` | 36-42, 69-133 | behavioral slugs, `AreaLightingScene` |
| `scene.py` | 205-240 | `_apply_skeleton` |
| `select.py` | 71-78 | `async_select_option` |
| `alert.py` | 48-50, 112-170, 234-316 | colour test, steps, `execute_alert` |
| `scene_machine.py` | 51-89, 92-186, 280 | defaults, on decisions, sun position |
| `config_schema.py` | 115-131, 167-176, 251-296, 415-640, 714-729 | light schema, scene schema, remotes and linked motion, `parse_config`, kelvin route lights |
| `models.py` | 30-53, 56-66, 197-280, 293-300 | `LightConfig`, `SceneConfig`, `AreaConfig`, `AreaLightingConfig` |
| `const.py` | 17, 125-135 | holiday set, scene light attributes |
| `demand_response.py` | 46-105 | shed policy |
| `services.py` | 89-135, 150-170 | snapshot, alert |
| `__init__.py` | 64-105, 174-228, 266-343, 401-422 | schema and validators, startup, reload, teardown |
| `haconf:area_lighting.yaml` | 377-711 | den area |
| `haconf:area_lighting.yaml` | 4887-4889 | pantry follows kitchen |
| `haconf:scripts/theater/party_mode.yaml` | 1-32 | theater party script |
| `haconf:scripts/den/party_mode_a.yaml` | 1-51 | den party script |
| `haconf:scripts/_global/lights_out.yaml` | whole file | lights out |
| `haconf:automations.yaml` | 383-393, 2227-2254 | party automations |
