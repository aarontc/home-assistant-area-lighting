# Scene effects: design spec

Date: 2026-10-13
Status: Draft, awaiting review
Scope: one minor release of `area_lighting` (3.2.0, from one `(Minor)`
commit), then a haconf change that must land after it

Line numbers below are from `main` at `3acb713` (release 3.1.1) unless a path
starts with `haconf:`, which means `/home/aaron/git/aaron/haconf` at
`origin/main` (`49308364`) on 2026-10-13. Bare paths are under
`custom_components/area_lighting/`.

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

The user made decisions 1 to 6. They are final.

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

This spec makes decisions 7 to 20, which the brief left open. Review should
start with them.

7. **Participants.** Only the area's declared individual lights can take
   part: `lights:` entries without `members`. The manual detection listener
   watches every one of them (`event_handlers.py:300-307`). With
   `entities:`, the participants are the listed lights with `state: "on"`
   that are available and colour capable. Without `entities:`, they are every
   available, colour capable declared individual light. `group_exclude`
   removes lights first, either way. An effect scene's `entities:` may name
   only declared individual lights, so it can never name a cluster or a light
   outside the area. Section 4.2 has the rules.
8. **Lights outside the participant set.** With `entities:`, every `off`
   entry and every `on` entry for an available light without colour applies
   once at activation, as a static scene would apply it. Excluded and
   unavailable entries get no command. Without `entities:`, the effect leaves
   every other light alone. To turn a white lamp off during a party, list it
   with `state: "off"`.
9. **Ticks command only lit participants.** Activation turns every kept
   participant on. Later ticks skip a participant that is off, unavailable or
   shed, and the check runs again as each command is dispatched (4.5). So
   switching one bulb off takes it out of the party until the next
   activation, and a tick never relights a bulb the user just turned off.
   Circadian follows the same rule (`controller.py:1774-1778`).
10. **Motion never picks an effect scene.** Reserved slugs keep effects off
    every scene an automatic path picks by name (decision 11). The generic
    default skips effect scenes, a follower ignores a leader's effect scene
    when motion turned it on, and `linked_motion` may not name one. Section
    6.4 has the details.
11. **Reserved slugs.** `off`, `off_internal`, `manual`, `circadian`,
    `ambient`, `christmas`, `halloween`, `night`, `daylight` and `evening` may
    not carry `effect:`. Each already has fixed behaviour. An animated holiday
    scene would also count as ambient-like (`area_state.py:63`) and
    holiday-tracked (`controller.py:2830-2842`). Motion's default picks
    `night` in night mode and `daylight` or `evening` by sun position
    (`scene_machine.py:62-72`), and `_resolve_and_activate` activates the sun
    position result directly (`controller.py:1559-1565`).
12. **The scene entity drives activation.** `AreaLightingScene.async_activate`
    for an effect scene calls the controller, which runs the full activation.
    The `scene.turn_on` tracker ignores effect slugs, so the scene is not
    handled twice. A static scene entity leaves the effect scene, and waits
    for a tick still in flight, before it applies its lights.
13. **The select turns nothing on.** Selecting an effect scene sets the
    state, arms the occupancy timer, switches off any lit bulb that demand
    response sheds, and starts the effect on the participants that are on.
    Selecting circadian also turns nothing on (`controller.py:543-561`). The
    select entity awaits a new controller method for this (5.1).
14. **Raise and lower freeze the effect.** Dimming stops the effect and keeps
    the scene, as it suspends circadian (`controller.py:2287-2288`). The next
    `on` restores the scene and restarts the effect.
15. **Manual detection skips the whole area while it is in an effect scene,**
    except for a manually relit shed bulb. That matches circadian. A manual
    change to a non-participant during a party is therefore not detected.
16. **Alert supersession is scoped to effect scenes.** If the area entered or
    left an effect scene while any alert held it, the last alert to finish
    keeps the newer state and re-applies it instead of restoring its
    snapshot. Raise and lower during an alert do not count, so the restore
    undoes them, as it undoes any dim during an alert today. Other alerts
    behave as today. Section 6.1 has the rule.
17. **Snapshots do not apply.** An effect scene ignores any stored snapshot,
    and `area_lighting.snapshot_scene` refuses an effect scene.
18. **Tick timing.** The next tick is scheduled with Home Assistant's
    `async_call_later` once the previous tick's commands have all returned. The
    interval floor is 2 seconds and the default is 5. Ticks cannot overlap.
19. **A motion timer never fades an effect scene.** Starting an effect leaves
    a running motion timer alone, because that timer also cleans up the linked
    areas the motion lit (`controller.py:2914-2917`). When it fires,
    `_on_motion_timer` cleans them up and then returns without fading if the
    area is in an effect scene. The occupancy timer applies as in any lit
    scene. Section 6.3 has the details.
20. **haconf adds no remote buttons in the first cut.** The scenes are
    reachable from the select, the scene entities and dashboards. Where they go
    on remotes is a follow-up, because adding a slug to a favorite cycle
    changes what every press does from the scenes that are not in it (haconf
    CLAUDE.md, "A favorite cycle is one shared position").

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
- `effect` on a reserved slug (decision 11)
- an effect scene whose `entities:` names anything but a declared individual
  light of its area. That rejects a cluster from `light_clusters`, a cluster
  declared under `lights:` with `members` (`config_schema.py:125-129`,
  `models.py:51-53`), a cluster member the area does not declare under
  `lights:`, and a light from another area.
  The check runs over every entry in `area.all_lights` that carries
  `members`, as `_individual_light_ids` does (`controller.py:1868`).
- a `linked_motion` `local_scene` that is an effect scene in its own area, or a
  `remote_scene` that is an effect scene in the remote area. Today
  `LINKED_MOTION_MAPPING_SCHEMA` checks only that each is a string
  (`config_schema.py:291-296`).

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

  # Only the listed "on" lights take part. The bookcase uplight and desk
  # lamp are colour capable but stay as they were. The moon light turns off.
  - id: den
    name: Den
    scenes:
      - id: party
        name: Party
        effect: party
        entities:
          light.den_study_light_up:
            state: "on"
          light.den_study_light_down:
            state: "on"
          light.den_overhead_1:
            state: "on"
          light.den_overhead_2:
            state: "on"
          light.den_moon_light:
            state: "off"
```

Neither area gets a remote button. haconf adds none in the first cut
(decision 20).

## 4. Architecture

### 4.1 Modules

| file | change |
| --- | --- |
| `effects.py` (new) | `next_frame()`, a pure function, and `EffectRunner`, which owns the tick timer |
| `models.py` | `EffectPreset`, `SceneConfig.effect`, `AreaLightingConfig.effects`, `AreaConfig.effect_scene_slugs` |
| `config_schema.py` | `EFFECT_PRESET_SCHEMA`, the scene key, `parse_config`, `validate_scene_effects` |
| `const.py` | `EFFECT_MIN_INTERVAL_SECONDS = 2`, `DEFAULT_EFFECT_INTERVAL_SECONDS = 5`, `EFFECT_HANDOFF_SECONDS = 2` |
| `light_targets.py` | `is_color_capable`, moved from `alert.py:48-50`, which imports it back |
| `controller.py` | an effect branch in `_activate_scene`, start, stop, leave and resume, `async_select_scene`, the dispatch check and completion barrier, every exit path, effect-aware `_scene_bulb_ids` and `_area_holiday`, the motion timer guard, diagnostics |
| `scene.py` | effect routing in `AreaLightingScene.async_activate`, `scene_source: effect` |
| `select.py` | `async_select_option` awaits `async_select_scene` |
| `event_handlers.py` | the manual detection skip, the tracker skip |
| `scene_machine.py` | `effect_slugs` keeps effect scenes out of the generic default |
| `alert.py` | the alert hold count and the supersession check |
| `services.py` | `snapshot_scene` refuses effect scenes |
| `__init__.py` | schema key, validator, resume at startup and on reload |
| `README.md`, `CONFIGURATION.md` | the new block, the scene key, the lifecycle rules |

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
resume, a demand response re-drive and a reload resume.

The candidates are the area's declared individual lights, the `lights:`
entries without `members`, in config order. Validation keeps every
`entities:` key inside that list (3.3).

1. Remove the scene's `group_exclude` from the candidates and from the
   `entities:` keys. An excluded light lands in no set and gets no command,
   as in the static skeleton path (`controller.py:1307-1319`).
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
(`controller.py:924-925`), calls `_leave_effect_scene()`, and waits for a tick
still in flight (4.5). The effect branch then runs:

1. `_stop_circadian()`.
2. Clear `_active_scene_targets`, as circadian does (`controller.py:943`).
   With no targets, `_schedule_post_settle_selfcheck` returns early
   (`controller.py:2438`), and the branch does not call it anyway.
3. Compute the three sets (4.2). Under demand response, compute
   `_dr_shed_ids` (6.2).
4. Apply each static entry once, through `_apply_light_state`
   (`controller.py:1261-1276`) with the caller's transition, and send
   `light.turn_off` to every shed light.
5. Return if the controller closed or the activation lost its claim (4.5),
   whatever `yield_if_superseded` says. The visual path checks the claim only
   for that flag (`controller.py:961-972`).
6. `_clear_scene_drift_issue()`, `transition_to_scene(slug, source)`, bump
   `_effect_scene_epoch` (4.5), `_enforce_occupancy_timer()`,
   `_notify_state_change()`, then await `_sync_kelvin_router()`.
7. Return if the activation lost its claim during that await. Otherwise call
   `_start_effect(turn_on=True)`. It sends the first frame to every kept
   participant and awaits it, then schedules the next tick.
8. Return if the activation lost its claim during the first frame. Otherwise
   propagate to followers with `LeaderReason.SCENE_ACTIVATED`, unless
   `propagate_to_followers` is false or the source is `LEADER`. The existing
   tuple at `controller.py:980-987` already gives an effect slug that reason.

### 4.4 The runner and the tick

`_start_effect(turn_on)` bumps `_effect_generation`, builds the three sets, and
starts the runner. `turn_on=True`, used by activation, sends the first frame
to every kept participant. `turn_on=False`, used by resume and the select,
first switches off each shed bulb that is on, then sends the first frame only
to participants that are on. It turns no bulb on (6.2). Each later tick:

1. Returns at once if the generation moved, the controller is closed, or the
   runner stopped.
2. Skips the frame while an alert holds the area (6.1), and reschedules.
3. Builds a frame for the participants that are on, available and not in
   `_dr_shed_ids`.
4. With `sync: true` and demand response inactive, coalesces the frame through
   `select_dispatch_commands` (`cluster_dispatch.py`), so a cluster whose
   members all take part gets one command.
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
changes the scene calls it. The effect branch (4.3, step 6) and the select's
effect entry (5.1) bump it when they commit. Raise and lower, alert pauses,
reload and shutdown leave it alone. So does an activation with
`yield_if_superseded`, which marks a background replay of the current state:
the demand response re-drive and the alert re-apply. Section 6.1 reads the
epoch.

**Activation claim.** The effect branch acts on a lost claim at steps 5, 7 and
8 of 4.3, whatever `yield_if_superseded` says. Every `_activate_scene` call
takes a new claim. `async_select_scene` (5.1) and `apply_config` bump
`_activation_seq` too, as `_leave_removed_scene` already does
(`controller.py:428`), and `shutdown` sets `_closed`. So a remote off, a
select, a scene entity, a fade, a reload or an unload during the static
commands keeps the older party from committing, starting or propagating.
Yielding here drops no cascade. The handlers the static commands can
trigger, all-off and the manual latch, change state without activating a
scene (`controller.py:2376-2395`, `controller.py:2397-2425`). If one of them
fires mid-activation, the activation commits over it, as the visual path does
today. The select's bump also makes an in-flight demand response re-drive
yield to a selection (`controller.py:1446-1450`). Today that re-drive commits
over it.

**Dispatch check.** `async_effect_call(generation, service, entity_id, **data)`
is the only way an effect command reaches Home Assistant. As each queued
command starts, it drops the command unless all of these hold:

- `generation` equals `_effect_generation`, the runner is active, and the
  controller is not closed.
- No alert holds the area (6.1).
- For `light.turn_on`, the target light, or every member of a coalesced
  cluster, is not in `_dr_shed_ids`, has a live state that is not
  `unavailable`, and is on. The first frame of `turn_on=True` skips the "is
  on" test, because it turns the participants on. If a cluster command fails
  the member test, the call sends the frame to the passing members one by
  one instead.
- For `light.turn_off`, the target is in `_dr_shed_ids` and is on. The effect
  switches off only shed bulbs.

Circadian checks live state and shedding the same way as each send starts
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

## 5. Lifecycle

### 5.1 Entry paths

Every path below ends in the activation of 4.3, or in `_start_effect` with
`turn_on=False`.

| path | today | change |
| --- | --- | --- |
| Scene entity, `scene.turn_on` | `AreaLightingScene.async_activate` applies a snapshot, entities or skeleton itself (`scene.py:107-133`). The tracker then calls `handle_scene_activated`, which only updates tracking (`event_handlers.py:376-420`, `controller.py:2344-2374`). | For an effect scene, `async_activate` calls `ctrl.activate_scene(slug)`, a public wrapper over `_activate_scene(slug, USER)`. `handle_scene_activated` returns early for an effect slug. |
| Select, `select_option` | The `current_scene` setter changes state only and starts circadian for `circadian` (`select.py:71-78`, `controller.py:543-561`). | `async_select_option` awaits a new `ctrl.async_select_scene(value)` instead of assigning the setter (`select.py:78`). For any option, it first bumps `_activation_seq` and calls `_leave_effect_scene()`, then makes the setter's state change, all before its first await. For an effect slug it then bumps `_effect_scene_epoch`, calls `_enforce_occupancy_timer()` (`controller.py:2697-2717`) and `_notify_state_change()`, and awaits `_start_effect(turn_on=False)`. A second selection during that first frame bumps the generation, so the first frame's remaining commands drop. The synchronous setter stays for existing tests and raises for an effect slug, which needs the awaited method. |
| Remote favorite cycle | `lighting_favorite` cycles the remote's list and calls `_activate_scene` (`controller.py:2191-2214`). | None. |
| Remote `buttons.on` | `determine_on_sequence_action` walks the list (`scene_machine.py:154-186`). | None. |
| Remote `on`, default cycle | `determine_on_action`. Its generic fallback could pick an effect scene (`scene_machine.py:73-76`). | The fallback skips effect slugs (6.4). |
| `on` while dimmed | `lighting_on` restores the previous scene (`controller.py:2063-2069`). | None. The restore runs 4.3 and restarts the effect. |
| Follower | `handle_leader_change` activates the leader's slug if the follower defines it (`controller.py:2883-2894`). | None. |
| Follower turned on, Scenario A | `lighting_on` mirrors the leader's on-scene (`controller.py:2022-2032`). | Ignore an effect hint when the source is `MOTION` (6.4). |
| Motion | `handle_motion_on` calls `lighting_on(MOTION)` or the linked mapping (`controller.py:2580-2588`). | Never reaches an effect scene (6.4). |
| Demand response edge | `reactivate_for_demand_response` re-drives a scene area through `_activate_scene` (`controller.py:1397-1451`). | None. The re-drive runs 4.3, which recomputes the shed set. |
| Alert end | `async_end_alert` re-drives for demand response, or resumes circadian (`controller.py:1610-1643`). | The completion that releases the last alert hold resumes the effect, or re-applies the newer state when the alert was superseded (6.1). |
| Startup | `_on_started` restores timers, reconciles, then resumes circadian (`__init__.py:213-228`). | Then `async_resume_effect()` (section 7). |
| Reload, kept and added areas | `async_resume_circadian` per area (`__init__.py:331-337`). | Then `async_resume_effect()` (section 7). |

### 5.2 Exit paths

Every exit calls `_leave_effect_scene()`, or `_stop_effect()` where the scene
stays, before it sends a command or changes state. Commands already queued
then fail the dispatch check (4.5).

| path | where | change |
| --- | --- | --- |
| Another scene, off or circadian | `_activate_scene`, all three branches (`controller.py:927-978`) | `_leave_effect_scene()` and the completion barrier at the top of the method, before the branches (4.3, 4.5). |
| `area_lighting.lighting_circadian` service | `lighting_circadian` calls `_activate_circadian` directly, with no claim (`controller.py:2228-2238`) | It bumps `_activation_seq`, calls `_leave_effect_scene()` and waits at the completion barrier first. |
| Static scene entity | `AreaLightingScene.async_activate` (`scene.py:107-133`) | Awaits `ctrl.async_leave_effect_scene()` before applying. It bumps `_activation_seq`, so an in-flight party activation yields, calls `_leave_effect_scene()`, and waits at the completion barrier. No tick then lands between the entity's commands and the tracker task. |
| External scene, tracker | `handle_scene_activated` (`controller.py:2355-2372`) | `_leave_effect_scene()` in the off, circadian and static branches. |
| Select to another option | `current_scene` setter (`controller.py:553-560`) | `async_select_scene` calls `_leave_effect_scene()` first (5.1). |
| Manual latch | `handle_manual_light_change` (`controller.py:2417-2425`) | `_leave_effect_scene()` beside `_stop_circadian()`. |
| All lights off | `handle_lights_all_off` (`controller.py:2376-2395`) | `_leave_effect_scene()` beside `_stop_circadian()`. |
| Raise or lower | `_adjust_brightness` (`controller.py:2287-2288`), `_bring_dark_area_to_min` (`controller.py:2326-2327`) | `_stop_effect()` where circadian stops. The scene stays, dimmed, and the epoch does not move (6.6). |
| Motion or occupancy fade | `lighting_off_fade` (`controller.py:2114-2151`) | `_leave_effect_scene()` beside the `_stop_circadian()` at `controller.py:2126`. The motion timer never gets here from an effect scene (6.3). |
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

The alert captures the party colours (`alert.py:277`), runs its steps, and
restores them (`alert.py:294-299`). The completion that brings `_alert_holds`
to zero then resumes the effect: in the demand response branch through the
re-drive, otherwise through `async_resume_effect()`, called beside
`async_resume_circadian()` (`controller.py:1643`). An earlier completion
behaves as today and leaves the effect paused. Each alert still restores its
own capture, so a later alert can restore colours captured during an earlier
one's flashes. The resumed effect replaces them at its first frame, except on
a participant captured off, which stays off until the next activation.

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
   `superseded`. Instead of the demand response and circadian branches, it
   clears the flag, waits at the completion barrier, and re-applies the
   current state with `propagate_to_followers=False` and
   `yield_if_superseded=True`, as the demand response re-drive does
   (`controller.py:1446-1450`). An effect or static scene goes through
   `_activate_scene`, off through `_activate_scene(SCENE_OFF_INTERNAL)`, and
   circadian through `_activate_circadian`. A manual area is left alone. The
   re-apply covers demand response too, since activation recomputes the shed
   set.

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
  dim is lost, as any dim during an alert is today, because the saved
  `AreaState` carries the old `dimmed` flag (`alert.py:274`,
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

Static scenes chosen during an alert, with no effect scene involved, still
lose to the alert's restore, as they do today. Fixing that, and serialising
overlapping alerts beyond the effect's pause and resume, is a non-goal.

### 6.2 Demand response

The effect sheds against its own lights. The on-set is the participants plus
the static entries with `state: "on"` (4.2).
`demand_response_shed_ids` (`demand_response.py:68-78`) takes the area's
declared light order and that on-set. Activation turns the shed lights off,
the frames leave them out, and `_dr_shed_ids` holds them.

The select and every resume compute the same shed set. They do not only
record it: `_start_effect(turn_on=False)` switches off each shed bulb that is
on before its first frame (4.4), and turns no bulb on. So selecting `party`
in an area where every light is on, manual or not, during demand response
leaves the shed bulbs off and animates the rest. Recording alone would leave
them lit, because `_rebuild_scene_tracking` sends no command
(`controller.py:434-438`). Circadian switches off bulbs that become shed in
the same way (`controller.py:1152-1155`).

An edge mid-party re-drives the area (`controller.py:1441-1451`). The re-drive
runs 4.3, which recomputes the participants and the shed set. When demand
response turns off, the re-drive relights the shed participants. A dimmed
party is skipped, as every dimmed area is (`controller.py:1408-1417`). Its
effect is already stopped.

Under demand response the effect never commands a cluster. Validation keeps
clusters out of an effect scene's `entities:`, and participants are
individual lights. Coalescing a sync frame into a cluster command happens only
while demand response is inactive, as everywhere else
(`controller.py:1069-1074`).

`reconcile_startup_state` recomputes the shed set for a persisted effect
scene, through the effect branch of `_rebuild_scene_tracking`, and the resume
then switches the lit shed bulbs off (section 7).

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
party logs that warning once.

Motion never picks an effect scene:

- Every slug an automatic path picks by name is reserved (decision 11):
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

The handler at `event_handlers.py:456-608` gets a skip beside the circadian
one (`event_handlers.py:524-526`):

```python
if ctrl.effect_scene_active and entity_id not in ctrl.dr_shed_ids:
    _skip("area is in an effect scene", entity_id)
    return
```

`effect_scene_active` reads the state: the area is in `SCENE` and its scene
has an effect. It does not read whether the runner is ticking, so the skip
also covers the gap between a restart and the resume. The shed exception
keeps the manual relight rule: a bulb shed for demand response that reports
on is a person's doing, and the area latches manual. The latch stops the
effect (5.2).

`_heal_suppressed` (`controller.py:2445-2467`) returns `"effect scene"` for the
same state. With no targets nothing would heal anyway. The reason makes the
skip show in the log.

The effect needs no command stamps or echo window. Circadian stamps commands
(`controller.py:1845`) only because its light listener must ignore its own
echoes (`controller.py:1677`). The effect has no light listener.

### 6.6 Raise and lower

`_adjust_brightness` stops the effect where it stops circadian, marks the area
dimmed, and steps the lit bulbs (`controller.py:2287-2300`). The colours
freeze at the last frame. `_bring_dark_area_to_min` stops it after restoring
the scene, as it does for circadian (`controller.py:2326-2327`). The next `on`
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
`_individual_light_ids()`, as today (`controller.py:2333-2334`), so dim-up
still lights the room.

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
fallback in `determine_off_action` and `handle_ambient_enabled`. Areas without
effect scenes see no change.

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
| den | 6 | 6 | | |
| media_room | 14 | 9 | | 5 |
| living_room | 8 | 5 | | 3 |
| kitchen | 10 | 10 | | |
| office | 10 | | 9 | 1 |
| main_bedroom | 13 | | 13 | |
| craft_room | 5 | | 5 | |
| total | 74 | 30 | 31 | 13 |

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
  empty targets and the effect shed set. `reconcile_startup_state` calls it
  for a persisted scene (`controller.py:395-396`).
- `async_resume_effect()` returns unless the controller is open, no alert holds
  the area (6.1), the area is in an effect scene and not dimmed, and the
  runner is not already active. Those are the conditions of
  `async_resume_circadian` (`controller.py:1655`), plus the alert count. It
  then calls `_start_effect(turn_on=False)`, which switches off the lit shed
  bulbs, updates the participants that are on, and turns on nothing.
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
| Participants changed (`entities:`, `group_exclude`, lights) | The resume recomputes them. |
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
- `effect_tick_count`: ticks sent since the effect started
- `effect_generation` and `effect_scene_epoch`
- `alert_holds`

The diagnostic sensor already writes its state every second
(`diagnostics.py:28`, `diagnostics.py:121-122`), so a tick counter adds no
writes.

An effect scene's entity reports `scene_source: effect`, beside `snapshot`,
`config` and `skeleton` (`scene.py:96-105`). Its default icon is
`mdi:party-popper`. A configured `icon:` still wins (`scene.py:92`).

Logging:

- INFO when an effect starts: area, preset, participant count, shed count,
  and whether it turned lights on.
- INFO when it stops, with the reason: scene change, select, manual, all off,
  dim, fade, reload, shutdown.
- DEBUG once per tick: area, generation, lights sent, lights skipped as off,
  unavailable or shed.
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
- `test_linked_motion_cannot_name_effect_scene`, local and remote
- `test_effect_scene_allowed_in_favorite_and_on_lists`
- `test_no_effects_key_parses_to_empty`

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
- `test_shed_bulb_relight_latches_manual_and_stops_effect`
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

Alert, beside `tests/integration/test_alert.py`:

- `test_alert_pauses_ticks_and_resumes_after_restore`
- `test_off_during_alert_is_not_undone_by_restore`
- `test_party_started_during_alert_survives_restore`
- `test_alert_without_effect_change_restores_as_before`
- `test_alert_end_with_demand_response_redrives_party`
- `test_dimmed_party_then_off_during_alert_stays_off`
- `test_raise_during_alert_restores_pre_alert_party_and_resumes`
- `test_overlapping_alerts_resume_effect_only_after_last`
- `test_overlapping_alerts_off_during_first_is_kept`
- `test_alert_replay_yields_to_user_action`
- `test_reload_during_alert_restarts_on_lit_participants_only`
- `test_reload_removing_effect_during_alert_keeps_restored_frame`

Demand response:

- `test_party_sheds_against_participants`
- `test_demand_response_on_mid_party_sheds`
- `test_demand_response_off_mid_party_relights_shed_participants`
- `test_sync_frame_coalesces_into_cluster_without_demand_response`
- `test_sync_frame_never_drives_cluster_under_demand_response`
- `test_select_party_during_demand_response_switches_off_lit_shed_bulbs`
- `test_dr_redrive_in_flight_yields_to_select`

Manual detection and heal:

- `test_effect_commands_do_not_latch_manual`
- `test_no_post_settle_heal_in_effect_scene`

Startup and reload, in `tests/integration/test_reload.py` and
`tests/integration/test_persistence.py`:

- `test_restart_in_party_resumes_effect_on_lit_participants`
- `test_restart_in_dimmed_party_does_not_resume`
- `test_restart_in_party_during_demand_response_switches_off_lit_shed_bulbs`
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
(`__init__.py:77`), so 3.1.1 rejects an `effects:` key and the integration
would not load. Install 3.2.0 through HACS, then deploy the config.

### 10.1 Changes

`haconf:area_lighting.yaml`:

- Add the top-level `effects:` block with `party`, `christmas_party` and
  `media_room_party` from 3.4.
- Add a `party` and a `christmas_party` scene to theater, den, media_room,
  living_room, kitchen, office, main_bedroom and craft_room.
  - theater: no `entities:`. Its 8 colour lights are the 8 the script drives.
    `light.theater_work_light` has no colour and stays out.
  - den: `entities:` with the four lights the den script drives.
    Without it, the bookcase uplight and desk lamp would join.
  - media_room: `party` uses `media_room_party` and lists the 13 lights the
    blueprint drives (`haconf:automations.yaml:2234-2247`). That leaves out
    `light.yellow_ikea_lamp`, the area's 14th colour light, and the 15 white
    and decorative lights. `christmas_party` lists the same 13.
  - living_room, kitchen, office, main_bedroom and craft_room: no
    `entities:`, so every colour light takes part.
- No remote buttons (decision 20).

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
participant rule, and that motion never starts a party.

### 10.2 Order and rollback

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

Rollback: remove the effect scenes and the `effects:` block, then
`area_lighting.reload`, and only then downgrade the component. The old scripts
and blueprint come back from git if wanted.

## 11. Non-goals

- Per-area number entities for interval, transition or brightness.
- Selecting a preset from `input_select.holiday_mode`.
- Generators beyond `random` and `palette`, such as chases, fades or the
  bulbs' own effects.
- An effect on a holiday or ambient scene, such as an animated driveway.
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
- **Alert supersession is a new rule.** It changes alert ends only when the
  area entered or left an effect scene during an alert, but it is a second
  restore path in `execute_alert`, and it needs the tests in section 9.
- **The select now takes an activation claim.** A demand response re-drive in
  flight yields to a selection instead of committing over it (4.5). That
  touches static scenes too, and `test_dr_redrive_in_flight_yields_to_select`
  covers it.
- **Follower warnings.** A follower without the slug logs a warning on every
  leader party. That is the pantry today.
- **A static scene gaining `effect:` keeps its stored snapshot.** The effect
  ignores it (decision 17), but it comes back if `effect:` is removed later.
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
| `controller.py` | 1261-1276 | `_apply_light_state` |
| `controller.py` | 1295-1318 | `_resolve_raw_scene_targets` |
| `controller.py` | 1397-1451 | `reactivate_for_demand_response` |
| `controller.py` | 1569-1608 | circadian start and stop |
| `controller.py` | 1610-1666 | `async_end_alert`, `async_resume_circadian` |
| `controller.py` | 1717-1781 | circadian tick and sends |
| `controller.py` | 1830-1846 | `async_circadian_call` |
| `controller.py` | 1858-1879 | `_individual_light_ids` |
| `controller.py` | 1912-1928 | `_scene_bulb_ids` |
| `controller.py` | 1998-2071 | `lighting_on` |
| `controller.py` | 2114-2151 | `lighting_off_fade` |
| `controller.py` | 2171-2227 | `lighting_favorite` |
| `controller.py` | 2228-2238 | `lighting_circadian` |
| `controller.py` | 2271-2340 | raise and lower |
| `controller.py` | 2344-2425 | tracker, all off, manual latch |
| `controller.py` | 2426-2467 | post-settle self-check, `_heal_suppressed` |
| `controller.py` | 2572-2626 | motion on and off |
| `controller.py` | 2660-2695 | linked areas and their cleanup |
| `controller.py` | 2697-2717 | `_enforce_occupancy_timer` |
| `controller.py` | 2844-2910 | `handle_leader_change` |
| `controller.py` | 2914-2930 | timer callbacks |
| `timer_manager.py` | 118-126 | `_fire` after a cancel |
| `area_state.py` | 138-148, 187-194 | `transition_to_scene`, persisted fields |
| `controller.py` | 2938-2952 | `shutdown` |
| `event_handlers.py` | 376-420 | `scene.turn_on` tracker |
| `event_handlers.py` | 426-448 | all-off detection |
| `event_handlers.py` | 456-608 | manual detection |
| `event_handlers.py` | 614-695 | motion gate |
| `event_handlers.py` | 844-917 | remote dispatch |
| `scene.py` | 36-42, 69-133 | behavioral slugs, `AreaLightingScene` |
| `select.py` | 71-78 | `async_select_option` |
| `alert.py` | 48-50, 234-316 | colour test, `execute_alert` |
| `scene_machine.py` | 51-89, 92-186 | defaults, on decisions |
| `config_schema.py` | 167-176, 251-296, 415-640 | scene schema, remotes and linked motion, `parse_config` |
| `models.py` | 56-66, 197-280, 293-300 | `SceneConfig`, `AreaConfig`, `AreaLightingConfig` |
| `const.py` | 17, 125-135 | holiday set, scene light attributes |
| `demand_response.py` | 68-105 | shed policy |
| `services.py` | 89-135, 150-170 | snapshot, alert |
| `__init__.py` | 64-105, 174-228, 266-343, 401-422 | schema and validators, startup, reload, teardown |
| `haconf:scripts/theater/party_mode.yaml` | 1-32 | theater party script |
| `haconf:scripts/den/party_mode_a.yaml` | 1-51 | den party script |
| `haconf:automations.yaml` | 383-393, 2227-2254 | party automations |
| `haconf:area_lighting.yaml` | 4887-4889 | pantry follows kitchen |
