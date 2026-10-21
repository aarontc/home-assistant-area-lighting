# Changelog

All notable changes to this project are recorded here. Versions and tags are
created automatically on `main` by the `tag:auto` CI job from commit subject
prefixes (`(Major)` / `(Minor)` / `(Patch)`); this file is a curated, human-
readable companion that highlights user-facing changes.

## Unreleased

### Changed

- **Raise and lower keep the lights in proportion.** A press used to move
  every lit bulb by 12 points, which took most of the light out of a dim
  bulb and barely touched a bright one. Three lower presses took a
  bathroom's vanities from 50% to 14% and its overhead only from 98% to
  62%. Now the brightest lit bulb moves 12 points and the others scale by
  the same factor, so the room dims as one and every light reaches off on
  the same press.

- **A quick second press is no longer lost.** Each press stepped from the
  bulb's reported level, and a Hue bulb can take over a second to report.
  A press inside that gap stepped from the old level and changed nothing.
  A press now steps from the level it last sent while that report is still
  outstanding, for up to 5 seconds.

- **Activations fade in over 0.5 seconds by default.** A scene activation
  that names no transition, from a remote, motion, the app or a service,
  used to send none, so each bulb used its own default. It now sends 0.5
  seconds. Lights coming into circadian (entering circadian, a light turned
  on mid-circadian, a sleep change) also fade over 0.5 seconds instead of 1.

- **An off that names no transition uses the area's off fade.** Force-off,
  ambience, holiday, leader and linked-motion offs now fade over
  `number.<area>_manual_fadeout_seconds` (default 1.5) instead of the bulb's
  default.

### Built-in circadian lighting and reload (3.0.0)

area_lighting now computes circadian brightness and color temperature itself
and drives the lights of areas in the `circadian` scene directly. The
circadian_lighting integration is no longer used, and
`area_lighting.reload` applies a changed config to running areas. Read
"Migrating from 2.x" below before upgrading.

- **BREAKING: Circadian switches are now circadian groups.** Rename the area
  key `circadian_switches` to `circadian_groups` and each light's
  `circadian_switch` to `circadian_group`. The group entries and names stay
  the same. A config that still uses an old key fails validation with a
  message naming the new one.

- **BREAKING: `circadian_kelvin_routes.source` is removed.** Routes follow
  the integration's own circadian color temperature. Delete the `source`
  key; a config that keeps it fails validation.

- **BREAKING: Circadian groups are validated.** Setup and
  `area_lighting.reload` now reject an area where two groups share a name, a
  group's `min_brightness` is above its `max_brightness`, a light's
  `circadian_group` is empty or names a group the area does not declare, or a
  light sets only one of `circadian_group` and `circadian_type`. Fix the named
  group or light, setting both keys or neither.

- **BREAKING: The circadian_lighting integration is no longer used.**
  area_lighting no longer loads after it or turns its switches on and off,
  and the Repairs issue for missing external entities no longer asks for
  `sensor.circadian_values` or the `switch.circadian_lighting_*` switches,
  nor offers YAML to create them. It still reports a light assigned to a
  circadian group that does not exist. Left installed, circadian_lighting
  keeps driving the same lights as a second writer, so remove it in the
  same restart.

- **Built-in circadian clock.** area_lighting computes the circadian percent
  and color temperature from Home Assistant's location and elevation, using
  circadian_lighting's curve, and reports them as
  `sensor.area_lighting_circadian` (state in percent, attribute `colortemp`
  in kelvin, hidden by default). The optional top-level `circadian:` block
  sets `min_colortemp` (default 2500), `max_colortemp` (5500), `interval`
  (300 seconds between updates) and `transition` (60 seconds).

- **Circadian lights are driven by area_lighting itself.** While an area is
  in `circadian`, every `interval` seconds its grouped lights that are on get
  the clock's values with the configured `transition`, and entering circadian
  sends every grouped light outside a route the values with a 1-second
  transition. A brightness percentage `b` is sent as `round(b * 2.55)`, and
  `rgb` lights get `rgb_color` as well as brightness. Bulbs shed for demand
  response, route lights outside the active route and areas an alert owns
  are skipped.
  Leaving circadian from the `last_scene` select stops the updates, and
  choosing `circadian` there or activating `scene.<area>_circadian` from
  outside starts them.

- **Sleep options on circadian groups.** An entry under `circadian_groups`
  accepts `sleep_entity`, `sleep_state` (one state or a list, required when
  `sleep_entity` is set), `sleep_brightness` (percent, default 1) and
  `sleep_colortemp` (kelvin, default 1000). `min_brightness` and
  `max_brightness` now default to 1 and 100, as in circadian_lighting.
  While the state of `sleep_entity` is one of `sleep_state`, the group's
  lights get `sleep_brightness` and `sleep_colortemp`.

- **Circadian reacts to lights, sleep entities and restarts.** While an area
  is in `circadian`, a grouped light that comes on (from off, unavailable or
  unknown) gets the current values with a 1-second transition, unless
  area_lighting commanded it in the last 5 seconds. A change in the state of a
  group's `sleep_entity` re-sends that group's lights that are on, also with a
  1-second transition. Both work whether or not the area sets
  `event_handlers`. An area that was in `circadian` when Home Assistant
  stopped resumes at startup: its lights that are on get the current values
  with the configured `transition`, and lights that are off stay off. When an
  alert ends, the area's lights that are on get the current values, including
  any sleep change made during the alert.

- **Kelvin routes follow the built-in clock.** `circadian_kelvin_routes` picks
  its route from the clock's kelvin at each `interval` update instead of
  watching a `source` entity's `colortemp`. A route light with a circadian
  group turns on with that group's brightness and color temperature, and a
  route light without one is only switched on. At startup the area keeps the
  route whose lights are on and turns on no route light that is off, unless
  the kelvin crossed into another route while Home Assistant was down.

- **`area_lighting.reload` applies the new config to running areas.** Changes
  to lights, circadian groups, sensors, remotes, kelvin routes,
  `scene_self_heal` and the `circadian:` block take effect without a restart.
  Each area keeps its scene, dimmed flag and timer deadlines, and its
  `select.<area>_last_scene` keeps its value. The select writes a state
  change only when its scene options or the area name change. A motion or occupancy
  countdown stops when the area loses those sensors or a newly configured one
  is already on. An empty `occupancy_light_sensor_ids` list no longer starts
  an occupancy countdown. A reload also adds and removes areas and scenes,
  and treats a changed area id as one removal and one addition. A removed or
  disabled area saves its state without its timers, and its entities become
  unavailable but keep their registry entries. An area added back restores
  that state. The global switches now register even when no area is
  enabled, so they exist when a reload enables the first one.
  A reload that fails validation now raises an error to the caller and
  changes nothing.

#### Migrating from 2.x

Make these changes and install 3.0.0 in one restart. 2.x rejects the new
keys and 3.0.0 rejects the old ones, so either half alone stops
area_lighting from loading.

1. In `area_lighting.yaml`, rename every `circadian_switches:` to
   `circadian_groups:` and every `circadian_switch:` to `circadian_group:`.
2. Delete `source:` from each `circadian_kelvin_routes` block.
3. Add a top-level `circadian:` block with the values your
   `circadian_lighting:` block set. Its defaults are circadian_lighting's,
   so copy only what you changed:

   ```yaml
   area_lighting:
     circadian:
       min_colortemp: 2700
       max_colortemp: 5600
       interval: 60
       transition: 60
     areas: [...]
   ```

4. Where a group in `area_lighting.yaml` has no `min_brightness` or
   `max_brightness`, copy the value from its circadian_lighting switch.
   A missing bound now means 1 or 100. Where both files set a bound, 3.0.0
   uses the value in `area_lighting.yaml`, so check that it is the one you
   want.
5. Move `sleep_entity`, `sleep_state`, `sleep_brightness` and
   `sleep_colortemp` from each circadian_lighting switch onto its group.
   `sleep_state` is required when `sleep_entity` is set.
6. Point automations, templates and dashboards that read
   `sensor.circadian_values` at `sensor.area_lighting_circadian`. The
   `colortemp` attribute keeps its name. The state is still the percent.
7. Remove the `circadian_lighting:` block from `configuration.yaml`, the
   `switch:` entries that create its switches, and the integration itself
   (through HACS if HACS installed it). Do this in the same restart as the
   upgrade. A circadian_lighting switch that restores to on keeps driving
   its lights alongside area_lighting.

### Added

- **Per-remote `on` sequence.** A remote's `buttons."on"` lists scenes the
  `on` button activates before the usual circadian and sun-position rotation.
  An active holiday scene still comes first, so `"on": [study]` gives
  `christmas → study → circadian → evening → daylight` during Christmas
  while daylight lights are enabled, and starts at `study` otherwise, whatever night mode or a leader says. Motion,
  dimmed restores and other remotes keep the default cycle. The list cannot
  name `off`, `circadian`, `daylight`, `evening` or a holiday scene, which
  the cycle already places. YAML reads a bare `on` as `true`, so the key must
  be quoted, and validation says so when it is not.

- **Global master switches** — the integration now creates two switches of
  its own: `switch.area_lighting_motion_lights_enabled` (motion turns lights
  on) and `switch.area_lighting_occupancy_timeout_enabled` (the occupancy
  timer turns lights off). Both default to on, survive restarts, and each is
  ANDed with the matching per-area switch, so a feature runs in an area only
  when its global switch and that area's switch are both on. Turning the
  global occupancy switch off cancels every running occupancy timer without
  touching the lights; turning it back on re-arms the timer in occupied
  areas. Area ids beginning with `__` are now rejected at config validation:
  that namespace is reserved for internal storage keys (the global switch
  state persists under `__global__`).

- **Scene self-healing** — out-of-band Hue glitches (power-on defaults, RF
  dropouts, recovery from `unavailable`) are now auto-corrected back to the
  active scene instead of latching the area to `manual`. A bulb that keeps
  diverging (more than 3 heals within 5 minutes) is left in `manual` and
  raises a Home Assistant Repairs issue (`scene_drift_unhealable`), cleared
  the next time the area changes scene or turns off. Disable globally with
  `scene_self_heal: false`.

- **Circadian kelvin routes** — new per-area `circadian_kelvin_routes:` config
  that, while the `circadian` scene is active, dispatches a configured set of
  lights between mutually-exclusive routes based on the live target color
  temperature. Solves the "fluorescent + Hue strip" mixed-fixture problem.
  See [`CONFIGURATION.md`](CONFIGURATION.md) § "Circadian kelvin routes".

- **Demand response** — a global master switch
  (`switch.area_lighting_demand_response_active`, default off) that, while on,
  sheds a per-activation fraction of each area's on-bulbs (50% for up to 5,
  80% for 6 or more), keeping the first-declared lights. Off commands, alerts,
  and `manual` areas are unaffected; non-manual areas restore when it clears.
  Manually relighting a shed bulb latches the area `manual` (detected even
  while the area is dimmed or in circadian), so the user's override survives
  later flag flips.
  While shedding, `light_clusters` entities (Hue Zones) are ignored as
  direct scene targets: the individual member bulbs are driven instead, so
  a zone captured as `on` in a scene snapshot cannot relight shed bulbs.
  Kept members still coalesce into a single zone command when every member
  of that zone is kept. With `circadian_kelvin_routes`, circadian shedding
  is sized over the currently active route's lights (plus circadian lights
  outside any route) and is recomputed whenever the route changes. A
  recompute only re-drives lights whose shed status actually changed, and
  every path that leaves circadian (scene changes, off fades, manual
  changes, the area's scene select entity) or suspends it (dimming)
  detaches the route listener before the circadian switches turn off, so
  the outgoing circadian setup never fights the incoming state. Flipping
  the flag re-drives each already-lit non-manual area through its NORMAL
  activation path with the filter applied, so the tail sheds on activate
  and the scene replays in full on clear. There is no separate reconcile
  subsystem and no lock: demand response can never block (or be blocked
  by) normal light control, and each re-activation reads the live flag,
  so rapid flips converge to the final flag state. A flag flip that
  lands while an alert is running, or whose re-activation the alert
  outran, is deferred and applied right after the alert restores its
  captured states, including a flip to off: previously shed bulbs are
  relit and the shed tracking clears. The flip re-drives leader and
  follower areas independently, without leader-to-follower propagation,
  so a follower keeps its own active scene across a flip. Because
  the flip re-fires the active scene, a manual dim level set before the
  event returns to scene brightness on the flip (an accepted tradeoff,
  prioritizing reliable lighting over demand-response precision).
  Raising or lowering a fully-dark area (which restores the remembered
  scene, then brings every light to the minimum step) converges to the
  all-lights shed: the shed tail is explicitly turned off, so a bulb the
  restored scene lit cannot survive the bring-up, and the shed tracking
  follows. Skeleton scenes honor `group_exclude` identically in the
  controller and the scene entity (excluded lights are left untouched
  and never counted when sizing the shed): they carry no tracked scene
  target, so a demand-response re-activation cannot turn them off and
  toggling one never flips the area to manual. A restart into an active
  scene or circadian area recomputes the diagnostics shed list
  immediately, without driving any lights. An externally activated visual
  scene (`scene.turn_on`) records its shed set too, so clearing the flag
  restores its shed bulbs. Circadian shed sizing uses the route selection
  hysteresis exactly as the kelvin router does, so inside the hysteresis
  band both agree on the active route and a multi-bulb route is never
  fully relit. Cluster entities are never driven on while shedding in any
  path: circadian activation skips a cluster bound to a circadian switch,
  brightness raise/lower steps only individual member bulbs, and a kelvin
  route whose light is a cluster entity is expanded to its members (kept
  on, shed off), and a stable route reconcile (same route, same shed set)
  still corrects a member whose physical state drifted, without relighting
  shed members; clearing the flag relights only the route lights that were
  actually shed, so a kept light the user manually turned off while
  shedding was active stays off; only clusters actually named as route lights join that
  member convergence, so a directly-routed light that merely belongs to an
  unrelated cluster keeps its manual state on stable reconciles; without
  demand response, clusters batch exactly as before. A route reconcile that loses its listener while asking the
  controller to refresh the shed set (the area is leaving circadian)
  drops its route commands instead of fighting the incoming scene.

### Changed

- **BREAKING: Area ids `global`, `area_lighting` and `all` are now reserved.**
  The first two collide with global master switch unique ids and entity
  ids, respectively, and `area_lighting.alert` reads `all` as every area.
  Rename affected areas before restarting.

- **BREAKING: Scene references reject unquoted YAML booleans.** An unquoted
  `off` in a light's `scenes`, a `cycle` or Lutron `favorite` list, or a
  `linked_motion` mapping used to become the scene id `False`. It now fails
  validation; quote it as `"off"`. Apart from Lutron `favorite` overrides,
  which must name declared scenes, references are not checked against the
  area's declared scenes, because the integration can activate undeclared
  ones, so when you rename an area or scene id, update what refers to it:
  `lights[].scenes`, `linked_motion`, `leader_area_id`, `cycle` and Lutron
  `favorite` lists.

- **BREAKING: Area and scene ids must form valid entity ids.** Both become
  part of entity ids (`switch.<area>_night_mode`, `scene.<area>_<scene>`),
  which Home Assistant 2026.9 warns about when invalid and will reject from
  2027.2. Use lowercase letters and digits separated by single underscores,
  with no leading, trailing or doubled underscore. Unquoted `off`, `on`,
  `yes` and `no` are YAML booleans, so area ids, scene ids and scene names
  now reject them instead of silently becoming `False`. Quote them, as in
  `id: "off"` and `name: "Off"`. The error names a valid id to use.

- **BREAKING: Home Assistant 2026.3 or later is required.** `hacs.json` now
  declares 2026.3.0 as the minimum, and the component uses Python 3.14
  syntax, which older Home Assistant releases (Python 3.13) cannot load.

- **Per-area entities and the diagnostics sensor are hidden by default.**
  Each area's scenes, switches, numbers, `last_scene` select and `occupied`
  binary sensor register hidden, as does `sensor.area_lighting_diagnostics`,
  so the dashboards Home Assistant generates by itself (the default Overview,
  the legacy Overview and the Areas dashboard) no longer list a dozen or more
  of them per area. The global master switches stay visible. Hidden entities
  work as before in automations, scripts and hand-built dashboards. Un-hide
  one from its entity settings (**Visible**). Hidden entities also drop out of
  area, device and label targets, so an action aimed at an area no longer
  flips that area's `area_lighting` switches, and they are not exposed to
  voice assistants by default. On upgrade, the first start hides the entities
  an earlier release registered, once, so an entity you un-hide afterwards
  stays visible. See README § "Entity visibility".

- **Development and CI moved to Python 3.14 and Home Assistant 2026.9.3.**
  Tests, lint and type checks now run against `pytest-homeassistant-custom-component`
  0.13.366, ruff 0.16 and mypy 2.3, and `uv.lock` resolves from PyPI only.
  The CI pipeline runs on Dagger 0.21.8.

- **GitHub releases are now published by GitLab CI, not GitHub Actions** —
  packaging only; no effect on the integration itself. GitHub is in the
  release chain purely because HACS installs from there, and the old path
  depended on the push mirror's tag landing triggering a GitHub Actions
  workflow. That trigger proved intermittent: it fired for v1.1.1 and
  silently did not for v1.2.0, leaving a tag with no release, no failed job
  and no notification, so HACS users simply never saw the version. The
  `release:github` job now publishes from the same pipeline that creates the
  tag, waiting for the mirror and verifying the tag resolves to the expected
  commit first (GitHub's API will otherwise invent a missing tag from the
  default branch tip and release the wrong commit). A scheduled
  `release:audit` job fails loudly on any tag still missing a release.
  Release notes now also exclude merge commits. Requires a new
  `GITHUB_RELEASE_TOKEN` CI/CD variable; see CONTRIBUTING.md.

- **A dark area now dims back up into the scene it was last showing, on that
  scene's lights only** — turning an area off used to forget its scene, so
  raising or lowering a dark room restored the area's default on-scene instead
  of the one that had been on. The remembered scene now survives going off
  (and a restart, since it is already persisted), so a room showing `evening`
  when it went dark comes back to `evening`. Turning off from `manual` records
  nothing to restore, and an area that is already off keeps the scene it
  remembered first. Now that the scene is known, the bring-up is scoped to it:
  only the bulbs that scene lights come up at the minimum dimming level, and
  lights it leaves out stay dark instead of the whole room coming on. A scene
  that lights nothing here still brings the area up, so dim up never leaves a
  dark room dark.

- **BREAKING: external `input_boolean.motion_light_enabled` helper removed** —
  the global motion kill switch is now the integration-owned
  `switch.area_lighting_motion_lights_enabled`. Automations or dashboards
  that toggled the old helper must target the new switch. On upgrade, motion
  lighting is re-enabled by default (the owned switch defaults to on), so if
  you had left the old helper off to suppress motion lighting, turn the new
  switch off. The old helper is no longer consulted and can be deleted.

- **`raise` / `lower` brightness behavior clarified** — dim up and dim down
  (Lutron `raise` / `lower`) now only adjust lights that are currently on,
  stepping each by `brightness_step_pct`; lights that are off stay off. When
  **no** lights in the area are on, both buttons instead restore the
  remembered scene for color and next-`on`-press context and bring that
  scene's lights up to the minimum dimming level (the brightness step). This
  makes `lower` from a dark area light the room (previously a no-op).

### Fixed

- **Fade times are no longer cut to whole seconds.** Turning lights off,
  scene activations and kelvin route crossfades sent `int(transition)`, so
  the default 1.5-second off fade reached the lights as 1 second, and a
  2.5-second motion fade as 2. Fades are now sent as given. Alert `off`
  steps now send their `transition`, 0 when unset like `on` steps.

- **A manual change cancels the motion timers.** Picking a scene on a Pico,
  in the app or on a dashboard, using the scene select, raise or lower, an
  `area_lighting.lighting_*` service call, or adjusting a light by hand used
  to leave a running motion timer armed, and it then faded the room out.
  The motion timers are now cancelled, and the occupancy timer still turns
  the room off once it empties. A press while the timer is cleaning up
  linked areas also stops the fade.

- **A manual action stops an alert in progress.** The same actions now end
  the alert where it is. Before, the alert carried on flashing and then
  restored the pre-alert lights and scene over whatever the person chose.
  The `area_lighting.alert` call still returns normally.

- **A scene swap no longer looks like every light turning off.** When a
  scene turns some lights off and others on, the off reports can arrive
  first. Lights-off detection then turned the area off and dropped the new
  scene's targets. A light the scene turned on now counts as on until its
  fade and the report grace have passed, and the area turns off then only
  if every light still reads off.

- **`scene.<area>_off` turns off an area in ambient.** With ambience on,
  activating the off scene through `scene.turn_on` (scripts, HomeKit,
  Google, dashboards) put ambient straight back, because the call was
  tracked as a user-owned off before `lighting_off` read the state. The off
  and circadian scenes are no longer tracked, since their own handlers set
  the state. `scene.<area>_circadian` and `area_lighting.lighting_circadian`
  now also clear the previous scene's light targets, so the post-settle
  check no longer restores that scene's brightness over circadian.

- **Off then On in quick succession leaves the area on.** An off that
  finished after a newer activation used to commit anyway. The lights
  stayed on while the area read `off`, with no timer to turn them off and
  circadian still adjusting them. The off now yields to any newer
  activation.

- **Motion during a motion timer's fade-out is not lost.** Motion that
  arrived while the timer cleaned up linked areas or faded the room did
  nothing, and the room went dark with someone in it. Motion during the
  cleanup now cancels the fade. Motion during the fade is handled again once
  it ends, if a sensor still reads on and the motion handler's switches,
  ownership rules and conditions still allow it.

- **A scene heal no longer writes over a newer scene's target.** A heal that
  finished after another scene was activated put the old scene's target
  back, and the post-settle check could then restore the old brightness.

- **Linked motion no longer overwrites a scene chosen while it runs.**
  Motion in a `linked_motion` area decided the remote area's scene, applied
  its own lights, and only then set the remote scene. A remote press in that
  second was overwritten by the linked scene, and the local motion timer
  later turned the remote area off. The remote scene is now set only if the
  remote area has not been activated since, is not still applying a scene,
  and still shows the scene the mapping was resolved from. A press that
  lands while the linked scene itself is applying wins too, and cleanup
  leaves that area alone. `scene.turn_on` on an area's scene and the
  `area_lighting.lighting_circadian` service now count as new activations for this
  check.

- **Linked cleanup turns off only what linked motion still owns.** A remote
  area that someone put in the linked scene by hand, or is applying another
  scene in, stays on when the local motion timer ends. A linked activation
  recorded while cleanup runs is kept for the next cleanup. Before, it was
  dropped, or with two remote areas it raised `RuntimeError` and stopped the
  local fade-out. If cleanup fails partway, the areas it had not reached
  stay pending.

- **An area with no `ambient` scene turns off instead of falling back to
  it.** With ambience active, `off` and the motion and occupancy fade-out
  sent such an area to `ambient` anyway. Every light without a `scenes` list
  got a bare `light.turn_on`, so pressing off lit the room, and the scene
  select read `unknown`. Ambience turning on did the same to an off area,
  and so did a follower that followed its leader into `ambient`. These now
  turn the area off, or leave it off. The holiday fallback is unchanged: with `input_select.ambient_scene`
  set to `holiday`, an area that has the current holiday's scene still drops
  to it.

- **A reload or restart that removes an area's current scene switches the
  area to `manual`.** The area used to stay in the removed scene. The next
  demand-response change or self-heal then sent a bare `light.turn_on` to its
  lights and left its scene select reading `unknown`, and a holiday-owned
  scene no longer turned off when holiday mode went back to `none`. The
  lights now stay as they are, and the next `on` or motion picks a
  configured scene. An alert running during the reload no longer restores
  the removed scene when it ends. An area in `circadian` stays in it, since
  every area has that mode whether or not it configures the scene.

- **`scene.turn_on` tracking matches the integration's own scene entities.**
  A call to a scene a reload removed no longer puts the area back into it,
  and a renamed scene entity (`scene.den_daylight_2`) is tracked as
  `daylight` instead of `daylight_2`.

- **A holiday an area has no scene for no longer lights it.** Setting
  `input_select.holiday_mode` to `halloween` turned on every off area that
  defined only `christmas`, with a bare `light.turn_on`, and left its scene
  select reading `unknown`. Holiday mode changes, the `holiday` ambient
  scene, the off fallback and the favorite button now treat a holiday the
  area does not define as `none`. An area showing a holiday-owned
  `christmas` turns off when holiday mode moves to `halloween`.

- **`sensor.area_lighting_circadian` exists from the moment the integration
  loads.** 3.0.0 added it only after Home Assistant had started. Automations
  attach their triggers before that, so a `numeric_state` trigger on it logged
  "Error initializing ... unknown entity" at every restart, and templates
  could not read it during startup.

- **An alert no longer overrides countdown changes made while it ran.** When
  an alert ends it puts back the motion and occupancy countdowns it paused.
  A countdown that a sensor or a reload stopped or restarted during the alert
  now keeps that change, so lights no longer turn off while the room is
  occupied, or at the end of an old countdown.

- **Occupied sensors could silently fail to load.** `manifest.json` did not
  list `binary_sensor` as a dependency, so when no other integration loaded
  it, every `binary_sensor.<area>_occupied` was skipped with only a log
  warning. It is now a declared dependency.

- **Lutron remotes with a stale device id failed silently.** A remote whose
  `id` no longer matches a device in Home Assistant had its button presses
  ignored with no sign of why. Startup now logs a warning and raises a
  Repairs issue listing each such remote, with how to find its current
  device id. Device ids change when a device is re-added, and Home
  Assistant 2026.8 gave new ids to devices shared by several integrations.
  A remote still configured with its pre-split id is reported too, although
  Home Assistant 2026.9 resolves that id to a composite device, because
  button events carry the new id. The issue clears once every configured
  remote exists.

- **Scenes with color failed to activate.** A snapshot from
  `area_lighting.snapshot_scene` stores every color value the light reports
  (a bulb in color-temperature mode also reports hs, rgb and xy), and replaying
  it sent all of them to `light.turn_on`, which accepts only one. Home
  Assistant rejected the call, so the scene never applied. Scene colors are
  now chosen the way Home Assistant's own scenes choose them: the color the
  target's `color_mode` names, `white` for white mode, no color for on/off
  and brightness-only modes, and otherwise the first color present. One
  deliberate difference: where Home Assistant skips a light whose snapshot
  lacks the color its mode names, Area Lighting sends the first color it has.
  Snapshots now also record `rgbw_color` and `rgbww_color`, so RGBW and RGBWW
  bulbs restore their native color. Separately, Home Assistant 2026.3
  removed the mired `color_temp` argument. Scene config and older snapshots
  that still use `color_temp` are now sent as `color_temp_kelvin`, and new
  snapshots no longer record it. A scene `color_temp` that is not a positive
  whole number of mireds now fails config validation at startup.

- **Diagnostics refresh timer outlived shutdown.** The once-a-second refresh
  of `sensor.area_lighting_diagnostics` kept running after Home Assistant
  began stopping. It now cancels on shutdown.

- **Dims did not stick** — scene self-healing treated a dim as drift and drove
  the lights back to full scene brightness a few seconds later, while the area
  still reported itself dimmed. A dimmed area sits below its scene targets by
  definition, but neither the post-settle self-check nor the re-assert it
  dispatches checked for that, and the check is armed by the very scene
  restore that a dim-up in a dark room performs. Both now stand down while the
  area is dimmed. The dimmed flag is also set before the brightness commands
  go out rather than after, so a bulb's state echo can no longer be classified
  as drift in the gap. Areas whose scenes carry no explicit brightness were
  never affected, which is why this looked intermittent.

- **A lighting alert was swallowed by scene self-healing** — an alert
  triggered within a few seconds of a scene activation (walking into a room on
  motion, then an automation raising an alert) was reverted almost
  immediately: the self-check armed by that scene activation fired mid-pattern
  and re-asserted the scene's brightness and color over the alert. The alert
  pauses the area's motion and occupancy timers but never touched the pending
  self-check, and the check did not consult the alert flag the way manual
  detection already did. Self-heal now stands down for the length of a
  pattern, including a check armed by a scene activated mid-alert, and the
  re-assert re-checks the flag when it runs rather than only when it is
  scheduled. A suppressed heal no longer spends the per-bulb heal budget, so
  it cannot latch the area to `manual` or raise a spurious Repairs issue.

- **Dimming a partly-lit area switched its dark bulbs on** — in an area that
  declares `light_clusters`, pressing dim up or dim down relit every light
  that was off. The zone entity reports itself as on while any single member
  is lit, so it was picked up as a step target, and Home Assistant resolves
  `brightness_step_pct` against the zone's averaged brightness and then
  forwards one absolute brightness to every member. Raise and lower now step
  individual bulbs only, so lights that are off stay off. This also ends two
  side effects of the same cause: a member that was already on no longer gets
  stepped twice (once directly, once through the zone) and no longer has its
  brightness flattened to the zone average, and a step can no longer relight a
  bulb the scene had turned off, which manual detection read as a genuine
  override and used to latch the whole area to `manual`. Areas that declare a
  zone without listing its members under `lights:` now step those members, and
  a declared zone nested inside another zone's `members:` is never commanded.
  What makes an entry a zone is having `members:`, not which list it appears
  under, so a zone misfiled under `lights:` is also left alone.

- **Demand response: undeclared and duplicate bulbs were not shed** — the shed
  universe was built by iterating an area's config light list, so an `on`
  target absent from that list could never be shed AND did not count toward
  `n`, which also weakened the shed applied to the declared bulbs. A scene's
  `entities:` block may name a light that is not declared under `lights:`, and
  nothing validates that it is, so such a bulb burned through an entire window.
  Undeclared bulbs now count toward `n` and sort last, so they are shed first.
  A light declared twice is also no longer counted twice, which could push `n`
  across the 5/6 tier boundary and burn a kept slot on the duplicate.
  Cluster entities are consequently removed *before* the shed is computed
  rather than after: they are undeclared by construction, and counting one
  over-sheds (five members plus a zone read as the 80% tier and kept two bulbs
  where the 50% tier should have kept three).

- **Demand response un-dimmed a dimmed room** — a flip re-drove dimmed areas,
  and re-activating a scene clears the `dimmed` modifier, so a room dimmed at
  16:55 jumped back to full scene brightness when the window opened, and again
  when it closed. Dimmed areas are now skipped like manual ones. Dimming is a
  relative step with no stored level, so it cannot be re-applied after a
  re-drive; not re-driving is the only way to honour it.

- **Stale shed set on areas a flip skipped** — off, manual and dimmed areas
  kept a populated `dr_shed_ids` after the flag cleared. It is read by the
  manual-detection bypasses, so it kept changing behaviour after the window
  closed. It is now cleared before the early return, except while an alert
  owns the area, where a surviving set is the alert's "a demand-response edge
  was deferred to me" marker.

- **`snapshot_scene` baked the shed into a stored scene** — taken during a
  window, it captured the shed bulbs as `off`, permanently, with no record of
  what they should have been. It now refuses with a warning while demand
  response is active.

- **Dimming a dark area left circadian armed** — `_adjust_brightness` disables
  the circadian switches before stepping, but returns early into
  `_bring_dark_area_to_min` when nothing is on, and that path did not. Because
  it restores scene context first, a remembered scene of *circadian* re-armed
  the kelvin router and the dim was applied underneath it, leaving the area
  dimmed with circadian live: the router's next reconcile drove route lights
  with no user action, and circadian re-pushed brightness over the dim.

- **A parked demand-response re-drive could clobber a newer activation** — the
  re-drive writes its targets before applying and its state after, with no
  lock, so one still applying when a Pico press landed wrote its own scene into
  the state while the targets described the user's. Activations now carry a
  monotonic claim and the re-drive yields when it loses it. Only the re-drive
  yields: activations legitimately cascade, and a cascade is not a supersession.

- **Scene `rgbw_color` / `rgbww_color` silently dropped** — the scene-apply
  allowlist omitted `rgbw_color` and `rgbww_color`, so a scene specifying them
  (e.g. a christmas scene driving WiZ `rgbww` bulbs) turned the lights on but
  never changed their color — only `brightness`/`state` were applied. Both keys
  are now forwarded to `light.turn_on`, letting Home Assistant convert them to
  the bulb's native color mode (e.g. `rgbw` → `rgbww`). The honored scene
  attributes now live in a single `SCENE_LIGHT_ON_ATTRIBUTES` allowlist shared
  by the apply paths and the config schema, so the two can't drift again.

  To prevent this class of silent failure, a scene's per-light `entities` block
  is now **strictly validated**: only supported attributes (`state` plus the
  allowlist) are accepted, so an unsupported key — e.g. `color_mode`, which is
  read-only on a light and was never applied — now raises at startup instead of
  being silently ignored. **Action required:** remove any `color_mode` (or other
  non-`light.turn_on`) keys from scene `entities`.

- **Linked-motion remote area stranded on after re-triggered motion** — a
  remote area lit by `linked_motion` (e.g. the theater raised to its pass-through
  scene by stairs motion) would never turn back off when the source area's
  motion re-triggered while the remote was still in the linked scene. That
  re-trigger resolves to no remote activation (the remote's current scene maps
  to `remote_scene: null`), and `_activate_linked_areas` cleared the
  pending-cleanup tracking unconditionally — so the later motion-timer expiry
  had nothing to turn off. `_activate_linked_areas` now merges into the tracking
  instead of replacing it; cleanup still guards on the remote's current scene,
  so stale entries are harmless.

- **Spurious manual detection during long fade transitions** — when a scene
  activation carried a long fade (e.g. `lighting_off_fade` with a 60 s
  motion fadeout), the area-wide 4 s grace window expired well before the
  fade did. The bulb kept reporting `state=on` with brightness gradually
  decreasing toward the fade endpoint; `state_matches_scene_target` saw
  the divergence and demoted the area to `manual` mid-transition, breaking
  the next remote / motion cycle. Every entry in `_active_scene_targets`
  now carries a `commanded_at` monotonic timestamp and the `transition`
  duration that went with it, and manual detection consults those per-entity
  values to skip comparisons until the commanded transition has elapsed
  (plus the existing 4 s buffer).
- **Spurious manual detection on xy-native bulbs** — when a scene targeted
  `hs_color` and the bulb's native color space is xy (Philips Hue and
  similar), the hs ↔ xy round trip across the bridge's gamut clamping shifted
  the reported hue past the 10° tolerance and demoted the area to `manual`
  even though the bulb was doing exactly what was asked. The most visible
  symptom was an "on" remote press in the bedroom appearing to do nothing
  while night mode was active: `manual` + `night_mode=True` correctly
  re-activates the night scene, which is already on-screen. `state_matches_scene_target`
  now compares in xy space when the bulb's actual `color_mode` is `xy`,
  converting an hs target via `homeassistant.util.color.color_hs_to_xy`. The
  hs-mode comparison path is unchanged.
