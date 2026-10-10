# Internalize circadian lighting: design spec

Date: 2026-10-10
Status: approved for planning
Scope: one release (3.0.0) of `area_lighting`, plus a coordinated change to the
haconf repository
Plan: `docs/superpowers/plans/2026-10-10-internalize-circadian.md`

Line numbers below are from `main` at `20b3a85` (release 2.0.1) unless a path
starts with `haconf:`, which means `/home/aaron/git/aaron/haconf` at its
`origin/main` on 2026-10-10.

## 1. Goal and context

`area_lighting` stops depending on the `circadian_lighting` custom integration
and computes circadian brightness and color temperature itself. While an area
is in the `circadian` scene, its controller drives its lights on the curve
directly. In the same release, `area_lighting.reload` stops being a config swap
and applies a changed config to the running areas, entities and listeners.

Today the two integrations share the lights:

- `_activate_circadian` (`controller.py:922-966`) turns on the area's
  `switch.circadian_lighting_*` switches and sends one `light.turn_on` built
  from the switch's `brightness` attribute and `sensor.circadian_values`'
  `colortemp` (`controller.py:968-988`). At the same moment the switch's own
  turn-on runs a forced update. Two writers command the same bulbs, with
  different brightness scales (`int(pct * 2.55)` against `int(pct / 100 * 254)`).
- Every later adjustment comes from `circadian_lighting` on its own interval,
  from its own light lists in `haconf:switches/area_lighting.yaml` and
  `haconf:switches/circadian.yaml`. Those 43 hand-written entries have drifted
  from `area_lighting.yaml`: 22 groups differ in their brightness bounds, and
  one light (`light.main_bedroom_tv_backlight`) is only in the switch file.
- `circadian_lighting` knows nothing about demand response or alerts. It keeps
  pushing to shed bulbs and to bulbs mid-alert.
- The kelvin router turns route lights on with no brightness or color and
  waits for `circadian_lighting` to set them on its next push
  (`circadian_kelvin_router.py:271-296`).
- `circadian_lighting` has no reload, so a change to either file needs a
  restart, and `area_lighting.reload` only swaps the config object
  (`__init__.py:159-186`).

Upstream `claytonjn/hass-circadian_lighting` last changed on 2025-09-19
(release 2.1.6, which haconf vendors with one local patch). It calls
`homeassistant.helpers.sun.get_astral_location`, which Home Assistant 2026.9
marks deprecated, breaking in 2027.7.

## 2. Decisions

The user made decisions 1 to 7. They are final.

1. **Full internalization.** `circadian_lighting` fights `area_lighting` for the
   lights in the current design, and upstream is unmaintained. Keeping the
   vendored clock and dropping only the switches was considered and rejected.
2. **Reload does real work now.** `area_lighting.reload` applies light, group,
   sensor, remote and global circadian changes without a restart, and teardown
   removes every listener and timer the integration owns.
3. **Manual-change detection during circadian keeps today's behavior.** The
   blanket skip at `event_handlers.py:588-596` stays. Section 7 records both
   paths and what path B would need. `TODO.md` points at section 7.
4. **Brightness bounds come from `area_lighting.yaml`.** Where it defines no
   value, the value in haconf's switch files wins. This governs the haconf
   migration (section 9).
5. **`circadian_switches` and `circadian_switch` become `circadian_groups` and
   `circadian_group`.** No compatibility shim. haconf changes in the same
   cutover.
6. **The coat closet becomes an `area_lighting` area**, replacing the UI
   automation pair at `haconf:automations.yaml:909-947`. The investigation in
   section 9.3 found that the coat closet sunrise and sunset scene automations
   are not redundant, so they stay.
7. **The Stairs Main sleep options stay as a generic feature.** They implement a
   theater movie mode (`sleep_entity: select.theater_last_scene`,
   `sleep_state: movie`). Groups get `sleep_entity`, `sleep_state`,
   `sleep_brightness` and `sleep_colortemp`. haconf's `sleep_colortemp` becomes
   2700.

Constraints set with the decisions:

- The release is 3.0.0, made by exactly one `(Major)` commit. Versions are not
  edited by hand.
- Group bounds default to `circadian_lighting`'s values, min 1 and max 100.
- Brightness is `round(pct * 2.55)` everywhere.
- `rgb` lights get `rgb_color`.
- Sun times come from Home Assistant's helper, which applies
  `hass.config.elevation`. `circadian_lighting` ignores elevation.

Design decisions made in this spec:

- **`circadian_kelvin_routes.source` is removed.** A config that sets it fails
  validation with a message saying why. Routes follow the integration's own
  kelvin. There is no strong reason to keep it. haconf's only `source`
  (`haconf:area_lighting.yaml:1581`) names a switch that will not exist, and
  the only routed area (kitchen) has no sleep options, so a per-group,
  sleep-aware source would change nothing.
- **A route light's brightness group is its own `circadian_group`.** The router
  turns a grouped light on with that group's payload. A route light with no
  group is switched on and off only, as today. In the kitchen, the fallback
  route's `kitchen_overhead_1-3` belong to the Overhead group, and the 4000 K
  route's `light.kitchen_overhead_fluorescent_tubes` has no group. Every
  haconf scene gives the tubes an on/off state only, so on/off is all they
  get. This needs no new key, and the two-group kitchen needs no tie-break.
- **The sunrise and sunset update triggers are dropped.** The curve is
  continuous at sunrise and sunset (0 % either side, so brightness and kelvin
  do not jump there), and the interval tick covers them.
- **Reload updates kept areas in place** instead of rebuilding controllers and
  re-adding entities. Section 6 gives the reason.
- **The new sensor keeps the attribute name `colortemp`** so haconf's two
  daylight automations change only their entity id.

## 3. Architecture

### 3.1 Modules

| Module | Change |
|---|---|
| `circadian.py` (new) | Pure functions, no Home Assistant import except `homeassistant.util.color`. Picks the relevant sun events, computes percent, kelvin and group brightness, and builds a `light.turn_on` payload per circadian type. |
| `circadian_clock.py` (new) | `CircadianClock`, one per `hass`, reads sun events through `homeassistant.helpers.sun.get_astral_event_date`, ticks every `interval` seconds and sends a dispatcher signal. Also `AreaLightingCircadianSensor`. |
| `controller.py` | The circadian adjuster replaces `_enable_circadian_switches` and `_disable_circadian_switches` (`controller.py:1433-1458`). Adds the tick handler, the per-light and sleep listeners, startup resume, and `async_apply_config` for reload. `_circadian_turn_on_data` reads the clock and the group instead of entity attributes. |
| `circadian_kelvin_router.py` | No entity listener. The controller's tick drives reconciliation, and the kelvin comes from the clock. Route turn-ons carry the group payload for grouped lights. `read_source_colortemp` is deleted. |
| `config_schema.py`, `models.py` | Top-level `circadian:` block. `circadian_groups` with sleep options and defaults. Light key `circadian_group`. `source` removed from routes. Removed keys raise a message naming the replacement. |
| `event_handlers.py` | External-entity validation stops requiring `sensor.circadian_values` and the switches, and the `circadian_lighting` bootstrap YAML goes. The check that every grouped light exists stays. Listener setup reads its config from an argument so reload can call it again. |
| `__init__.py` | Builds the clock, registers the sensor, resumes circadian areas on start, and runs the new reload. `async_teardown(hass)` removes everything the integration started. |
| Entities (`switch.py`, `select.py`, `number.py`, `binary_sensor.py`, `scene.py`, `diagnostics.py`) | Each can refresh its config-derived attributes (name, select options, scene config) in place for reload. |
| `manifest.json` | `circadian_lighting` leaves `after_dependencies`. |

### 3.2 Data flow

The clock is a pure function of time and location, so any caller can ask for
the current values. The tick only decides when lights get pushed.

1. `CircadianClock.values(now)` takes yesterday's, today's and tomorrow's
   sunrise, sunset, solar noon and solar midnight, keeps the two events before
   `now` and the two after, and returns `CircadianValues(percent, kelvin)`.
2. Every `interval` seconds the clock stores the values, updates
   `sensor.area_lighting_circadian`, and sends `SIGNAL_CIRCADIAN_TICK`.
3. A controller whose adjuster runs handles the signal as one task, and skips
   the tick if its previous tick has not finished:
   1. `_sync_kelvin_router()`. The router reconciles against the clock's
      kelvin. A route change turns lights on, with the group payload for
      grouped lights, and off, with `crossfade_seconds`.
   2. For each adjustable light (defined below) that is on and was not
      commanded by the adjuster in the last 5 seconds, it sends `light.turn_on`
      with the group payload and `transition`.
   3. The whole tick is skipped while an alert owns the area.
4. When the area enters circadian, activation starts the adjuster and sends
   every grouped light outside a route the payload with a 1-second
   transition, which also turns on the ones that were off. Shed bulbs are
   turned off and the router syncs, as today.
5. While the adjuster runs, an adjustable light that goes from not-on (`off`,
   `unavailable`, `unknown` or no state) to `on` gets the payload with a
   1-second transition. The adjuster skips it if it was commanded within 5
   seconds (the echo of its own command) or if an alert owns the area.
6. While the adjuster runs, a state change of a group's `sleep_entity` (state
   value only, not attribute-only changes) re-sends that group's adjustable
   lights that are on the payload with a 1-second transition. The change is
   ignored while an alert owns the area. The alert's completion (below)
   applies the current sleep values.
7. When an alert ends and does not re-drive the area for demand response
   (`alert.py:307-321`), it calls `async_resume_circadian()` (section 5). A
   reload during the alert stopped the adjuster, and resume starts it again.
   Without a reload, resume finds the adjuster running and runs one tick,
   which brings the alert's restored lights to the current values.

A light is adjustable when all of these hold:

- it has a group and a `circadian_type`,
- it is not in `dr_shed_ids`,
- it is not a cluster while demand response is active, since a cluster turn-on
  relights shed members through the aggregate (`controller.py:949-954`),
- it is not a route light outside the router's active route, with cluster
  routes expanded to their members. A tick right after a route change would
  otherwise send `light.turn_on` to an outgoing bulb that still reports `on`
  mid-crossfade, because route turn-offs record no echo time.

The tick, the per-light listener and the sleep listener all use this one
predicate.

Payload for one light:

- If the group is sleeping, kelvin is `sleep_colortemp` and the brightness
  percentage `b` is `sleep_brightness`. A group sleeps when `sleep_entity`
  exists and its state is in `sleep_state`. A missing entity means awake, as
  in haconf's local patch.
- Otherwise, kelvin comes from the clock, and `b` is `max_brightness` when the
  curve's percent is above 0 and `min + (max - min) * (100 + percent) / 100`
  otherwise.
- `ct` lights get `brightness = round(b * 2.55)` and
  `color_temp_kelvin = round(kelvin)`. `rgb` lights get `brightness` and
  `rgb_color`, from `color_temperature_to_rgb(kelvin)` rounded to ints.
  `brightness` lights get `brightness` only.

The adjuster runs exactly while the area is `circadian`, not dimmed, and its
controller is open. These paths stop it (the old `_disable_circadian_switches`
callers, `controller.py:813, 837, 1717, 1878, 1917, 1955, 1958, 1976, 2007`):
off, a visual scene, the off fade, raise and lower, dark bring-up, an external
scene or off, all lights off, and manual detection. Two paths that do not
touch the switches today now stop or start it:

- The select entity's `current_scene` setter (`controller.py:443-459`).
  Leaving circadian stops the adjuster, and entering circadian starts it. The
  setter stays state-only, so lights follow at the next tick.
- `handle_scene_activated("circadian")` (`controller.py:1946-1951`) starts the
  adjuster. `BehavioralScene.async_activate` still runs the full activation.

Stopping deactivates the kelvin router first, as `_disable_circadian_switches`
does today. Nothing sends a state change when the adjuster stops, so the stale
fallback reconcile that ordering guarded against cannot happen any more.
`handle_circadian_enabled` (`controller.py:2426-2428`) has no caller and is
deleted.

### 3.3 Demand response

- The tick and both listeners skip `dr_shed_ids`, and clusters while demand
  response is active. A shed bulb turned on
  by hand still reaches manual detection through the existing exception at
  `event_handlers.py:588-596`, and gets no circadian payload first.
- `recompute_and_apply_circadian_dr` (`controller.py:990-1030`) keeps its
  flip-only behavior and builds its payload from the clock.
- `reactivate_for_demand_response` reruns `_activate_circadian`. Starting the
  adjuster again does nothing.
- The circadian on-set used for shed sizing (`_circadian_on_ids`,
  `controller.py:881-914`) is unchanged, except that the route is selected from
  the clock's kelvin.

### 3.4 Entity ids

| Entity | Status |
|---|---|
| `sensor.area_lighting_circadian` | New. unique_id `area_lighting_circadian`, name "Area Lighting Circadian". State is percent (two decimals, unit `%`), attribute `colortemp` is kelvin as an int. Icon `mdi:theme-light-dark`. Hidden by default, like every other entity except the global switches. |
| `scene.<area>_circadian`, `select.<area>_last_scene`, per-area switches and numbers | Unchanged. |
| `sensor.area_lighting_diagnostics` | Each area's snapshot gains `circadian_running`. |
| `sensor.circadian_values`, `switch.circadian_lighting_*` | No longer read or required. They belong to `circadian_lighting` and disappear when haconf removes it. Neither has a unique_id, so no registry entries are left behind. |

### 3.5 Config schema

```yaml
area_lighting:
  # Optional. The defaults are circadian_lighting's.
  circadian:
    min_colortemp: 2500   # kelvin, 1000-10000
    max_colortemp: 5500   # kelvin, 1000-10000, at least min_colortemp
    interval: 300         # seconds between periodic updates, at least 1
    transition: 60        # seconds each periodic update fades over, 0-6553
  areas:
    - id: stairs
      name: Stairs
      circadian_groups:            # was circadian_switches
        - name: Main
          min_brightness: 10       # percent 1-100, default 1
          max_brightness: 55       # percent 1-100, default 100, at least min
          sleep_entity: select.theater_last_scene   # optional
          sleep_state: [movie]     # required when sleep_entity is set
          sleep_brightness: 5      # percent 1-100, default 1
          sleep_colortemp: 2700    # kelvin 1000-10000, default 1000
      lights:
        - id: light.stairs_upper
          circadian_group: Main    # was circadian_switch
          circadian_type: ct       # ct | rgb | brightness
    - id: kitchen
      name: Kitchen
      circadian_kelvin_routes:     # `source` is gone
        crossfade_seconds: 2.0
        routes:
          - kelvin_range: [4000, 6000]
            lights: [light.kitchen_overhead_fluorescent_tubes]
          - lights: [light.kitchen_overhead_1, light.kitchen_overhead_2]
```

New validation errors:

- `circadian_switches`, `circadian_switch` and `circadian_kelvin_routes.source`
  are rejected with a message naming the replacement.
- A light's `circadian_group` must name a group declared in its area.
- `circadian_group` and `circadian_type` must be set together. haconf has no
  light with only one of them.
- Group names are unique within an area.
- `min_brightness <= max_brightness` and `min_colortemp <= max_colortemp`.
- `sleep_state` is required when `sleep_entity` is set. Upstream raises
  `TypeError` at runtime in that case.

## 4. Behavior parity with circadian_lighting 2.1.6

Kept means the same behavior. Changed means different on purpose. Dropped
means removed. Line numbers are in
`haconf:custom_components/circadian_lighting/`.

| circadian_lighting | 3.0.0 | |
|---|---|---|
| Percent curve: two half-parabolas a day and two a night, 0 at sunrise and sunset, +100 at solar noon, -100 at solar midnight (`__init__.py:263-298`) | Same formula and event window | Kept |
| Sun events from an astral `Location` with elevation forced to 0 (`__init__.py:206-236`) | `get_astral_event_date`, which uses `hass.config.elevation`. Sunrise comes earlier and sunset later, by 2 to 3 minutes for an elevation between 50 and 300 m (astral 2.2 at 47.6° N) | Changed |
| `t` exactly on sunrise or sunset leaves `h, k, x` unbound and raises | Returns 0 | Changed |
| Kelvin linear in percent by day, `min_colortemp` all night (`__init__.py:300-306`) | Same | Kept |
| Brightness: max by day, `min + (max - min) * (100 + pct) / 100` at night (`switch.py:300-309`) | Same, per group | Kept |
| Switch bounds default to 1 and 100 | Group bounds default to 1 and 100 | Kept |
| Brightness sent as `int(b / 100 * 254)` (`switch.py:350`) | `round(b * 2.55)` | Changed |
| Kelvin sent as `int(ct)` | `round(kelvin)` | Changed |
| Activation: area_lighting sends `int(pct * 2.55)` with no transition, and the switch's forced update sends its own command with `initial_transition` 1 s | One command per light with a 1 s transition | Changed |
| `ct` lights: kelvin and brightness | Same | Kept |
| `rgb` lights: `rgb_color` and brightness periodically, brightness only at activation | `rgb_color` and brightness everywhere | Changed |
| `brightness` lights: brightness only | Same | Kept |
| `lights_xy` | Not supported. haconf uses none | Dropped |
| Only lights that are on are updated | Same | Kept |
| Not-on to on: forced single-light update with 1 s (`switch.py:375-386`) | Same, plus the skips in 3.2 step 5 | Changed |
| Sleep: any state-changed event on `sleep_entity`, attribute-only included, forces an update (`switch.py:388-394`) | Only a change of the state value | Changed |
| Missing `sleep_entity` means awake (haconf patch `252233d6`) | Same | Kept |
| Defaults `sleep_brightness` 1, `sleep_colortemp` 1000 | Same | Kept |
| `interval` default 300 s, `transition` default 60 s | Same defaults. haconf sets `interval: 60` | Kept |
| Extra updates at sunrise and sunset (`__init__.py:166-186`) | None | Dropped |
| `sunrise_offset`, `sunset_offset`, `sunrise_time`, `sunset_time`, `latitude`, `longitude`, `elevation` | Not supported. haconf sets none, and the manual times are read as UTC upstream | Dropped |
| `disable_entity`, `disable_state`, `disable_brightness_adjust`, `only_once`, `initial_transition` | Not supported, initial transition fixed at 1 s. haconf sets none | Dropped |
| `sensor.circadian_values`: state percent, attributes `colortemp`, `rgb_color`, `xy_color` | `sensor.area_lighting_circadian`: state percent, attribute `colortemp` | Changed |
| `circadian_lighting.values_update` service | None | Dropped |
| Switch entities restored on through `RestoreEntity` keep adjusting after a restart | The area's persisted `circadian` state resumes the adjuster at start (section 5) | Changed |
| Switch polling every 30 s | None | Dropped |
| Calls are fire-and-forget with no context | Blocking calls gathered per area, still with no context | Changed |
| Light and sleep listeners never removed (`switch.py:233-245`) | Removed when the adjuster stops and on teardown | Changed |
| Pushes to bulbs shed for demand response | Skips them | Changed |
| Pushes during alerts | Skips the tick and both listeners while an alert owns the area, then runs one tick when the alert ends | Changed |
| No reload; switch-file edits need a restart | `area_lighting.reload` applies group and global changes | Changed |
| Router turns route lights on with no attributes | Grouped route lights get the group payload | Changed |

## 5. Startup

`async_setup` builds the clock and starts its interval
(`async_track_time_interval(..., cancel_on_shutdown=True)`, as `diagnostics.py`
does). Ticks before Home Assistant has started reach no controller, because no
adjuster runs yet.

`_on_started` gains two steps:

- It registers `sensor.area_lighting_circadian`, next to the diagnostics sensor.
- After `reconcile_startup_state` has run for every controller, it calls
  `await ctrl.async_resume_circadian()` for each.

Resume does nothing unless the state is `circadian`, the area is not dimmed,
no alert is active and the controller is open. Then, in order:

1. It starts the adjuster (idempotent).
2. It has the router adopt the lights as they are, without sending a command.
   The router takes the first route with a light that is on as its current
   route, or, when no route light is on, the route the clock's kelvin selects.
   It records the current shed set as its last one. A router that is already
   active keeps its route. An alert's end deactivates the router first,
   because route commands that finished during the alert can leave its route
   out of step with the lights the alert restored. A plain route sync here
   would see a fresh router with no index and turn on every active-route light
   that is off (`circadian_kelvin_router.py:250-283`), against
   `circadian_lighting`'s rule that only lights already on are adjusted
   (`haconf:custom_components/circadian_lighting/switch.py:344`).
3. It awaits `recompute_and_apply_circadian_dr()`, which refreshes the shed set
   for the adopted route and changes only the lights whose shed status
   flipped. Areas without routes need this too, since no router reconcile
   calls it for them.
4. It syncs the router. With the adopted route still selected and the shed set
   unchanged, the reconcile is stable and sends nothing. If the kelvin crossed
   a route boundary while Home Assistant was down, the router crossfades as it
   would have at the crossing.
5. It runs one tick with the configured `transition`, so bulbs that drifted
   while Home Assistant was down converge without waiting up to one interval.
   `circadian_lighting` waited for its first interval.

Lights that are off stay off, except where a demand-response flip in step 3
or a route change in step 4 drives them, as either would have while Home
Assistant was running. Reload (section 6.2) and alert completion (section
3.2) call the same method.

## 6. Reload

### 6.1 Why in place

A first design rebuilt every controller and removed and re-added every entity.
Re-adding an entity writes a state change from no state to its value, and a
Home Assistant state trigger with only `to:` fires on it
(`homeassistant/components/homeassistant/triggers/state.py`, the `old_state is
None` path). haconf has several automations triggered by
`select.<area>_last_scene` changing to a value, for example
`haconf:automations/coat_closet/sync_daylight.yaml`. They would fire on every
reload. So areas that survive a reload keep their controller and entity objects,
and only their config changes.

### 6.2 Sequence

`_handle_reload` runs under an `asyncio.Lock`, so two reloads cannot interleave.

1. Read and validate. It reads `async_hass_config_yaml`, then runs
   `CONFIG_SCHEMA`, `parse_config`, `validate_leader_follower_graph`,
   `validate_circadian_kelvin_routes` and the new `validate_circadian_groups`.
   On any failure it logs and raises
   `HomeAssistantError`, so the caller sees the error, and nothing in the
   runtime changes. Today it logs and returns `None`, or raises an unhandled
   `vol.Invalid` from the schema.
2. Unsubscribe every event-handler listener in `hass.data[DOMAIN]["unsubs"]`.
3. Stop the clock and build a new one from the new `circadian:` block.
4. Close the areas that left or were disabled: stop the adjuster, deactivate
   the router, save the area's state with its timer deadlines cleared (so a
   later re-add does not fire stale timers), close its timers, and mark the
   controller closed. Then remove its entities with `async_remove()`. Registry
   entries stay, as with Home Assistant's own YAML reloads, so re-adding the
   area keeps user customizations. Detach the diagnostics sensor's listener.
5. Apply the new config to each kept area with `await
   ctrl.async_apply_config(new_area, new_config)`:
   1. Stop the adjuster and deactivate the old router.
   2. Swap `self.area`, `self._global_config` and `self._scene_self_heal`, and
      build a new router from the new routes.
   3. Clear `leader` and `followers`.
   4. Reconcile the timers with the new sensor lists. Cancel the motion
      timers if the area lost its motion sensors, or if any configured motion
      sensor reads `on`. Cancel the occupancy timer if the area lost its
      occupancy sensors, or if any configured occupancy sensor reads `on`.
      Registering listeners for a sensor that is already on produces no
      event, and neither timer's expiry rechecks its sensors
      (`controller.py:2493-2509`), so without this a new sensor that is on
      would not stop a running countdown. Otherwise, timers keep their
      deadlines. Reload turns no lights on for a sensor that is on.
   5. In a scene, rebuild `_active_scene_targets` and the shed set with the
      same code `reconcile_startup_state` uses, so manual detection compares
      against the new scene contents.
   6. Resume circadian (section 5). Its step 3 recomputes the circadian shed
      set from the new light list, with or without routes.
   7. Run `_enforce_occupancy_timer()` and `_notify_state_change()`.
6. Build controllers for added areas, loading persisted state from
   `StateStorage` if the id was used before.
7. Rewire leaders and followers for all controllers, with the same code
   `async_setup` uses, moved into a helper. Propagation queues each follower's
   `handle_leader_change` as a task (`controller.py:747`), so one queued before
   the reload can run after it. The call carries the leader controller, and the
   follower ignores it when that is no longer its `leader`.
8. Assign `hass.data[DOMAIN]["config"]`.
9. Register event handlers again from the new config. This also fixes the
   `TODO.md` entry about Lutron remote buttons needing a restart.
10. Refresh kept entities from config, add and remove scene entities whose
    slugs changed, and add the entities of added areas with the startup
    registration helpers. Every kept entity then writes its state, which emits
    no state-changed event when nothing changed. The diagnostics sensor attaches
    to new controllers. `hass.data[DOMAIN]["entities"]` records every entity
    the integration adds, by area, and drops removed ones.
11. Rerun external-entity and Lutron validation, so Repairs issues follow the
    new config.
12. For added areas, run `restore_timers()`, `reconcile_startup_state()` and
    `async_resume_circadian()`.

What each kind of change does:

| Change | Result |
|---|---|
| Light added or removed, group bounds, sleep options, routes, sensors, remotes, `scene_self_heal` | Applied to the running area. State and timers are kept. |
| Scene added or removed | Its scene entity is added or removed. The select's options follow. |
| Scene contents | Applied at the next activation. If the area is in that scene, manual detection compares against the new contents at once. |
| Area name changed, same id | Friendly names update. Entity ids do not change, because they derive from the id. |
| Area added | A new controller and entities, with its persisted state if the id was used before. |
| Area removed or `enabled: false` | Controller closed, entities removed, registry entries and persisted state kept. |
| Area id changed | Treated as one removal and one addition. |
| Global `circadian:` block | New clock. Running adjusters pick up the new values at the next tick. |

### 6.3 Teardown

`async_teardown(hass)`:

- cancels the `homeassistant_started` listener if it has not fired yet
  (`__init__.py:236` drops its unsubscribe today), so teardown before startup
  registers no entities afterwards. `_on_started` clears the stored
  unsubscribe first, because removing a `listen_once` listener that already
  fired logs an error,
- unsubscribes the event handlers,
- stops the clock,
- closes every controller,
- removes every entity the integration added (the list section 6.2 step 10
  records), which runs each entity's `async_will_remove_from_hass`. That
  cancels the diagnostics sensor's one-second interval (`diagnostics.py:98`)
  and detaches entity listeners from controllers, while Home Assistant keeps
  running.

Closing a controller:

- stops the adjuster, which removes its dispatcher connection and its light and
  sleep listeners,
- deactivates the router,
- closes its timers, including the self-heal check,
- marks it closed.

A closed controller does not save state, sync its router or schedule a
self-heal check. Its `TimerHandle`s refuse `start()` and `restore()`. Cancelling
a `TimerHandle` cannot retract a `_fire()` task the loop already queued
(`timer_manager.py:50`, and `:85` for a past-due restore), so `_fire()` also
returns without calling back once its handle is closed. The self-heal
callback `_run_post_settle_selfcheck` returns when the controller is closed.
So an activation that was in flight during a reload cannot write stale state
over the new runtime, re-arm a timer or schedule a self-heal check, and no
timer callback queued before the close runs after it. The activation's light
commands may still finish, which is acceptable for the tail of an action that
started before the reload.

`remove_state_listener` (`controller.py:638-639`) compares callbacks with
`is`, but each `self._on_controller_change` access builds a new bound method
(`select.py:75`, `:78`, `number.py:54`, `:57`, `diagnostics.py:85`, `:103`),
so no entity listener is ever removed today. It compares with `==`, which
bound methods of the same function and instance satisfy.

The integration test fixture `shutdown_controllers`
(`tests/integration/conftest.py:27-37`) calls `async_teardown(hass)`, so the
harness's lingering-timer check covers the clock as well as the controllers.

### 6.4 Accepted gaps

- An activation in flight on a kept area during reload can finish after
  `async_apply_config` and leave scene targets built from the old config. The
  next activation corrects them.
- An alert running during reload restores the targets it saved before the
  reload. The tick skips the area until the alert ends, and the alert's
  completion resumes circadian (section 3.2 step 7).

## 7. Manual-change detection during circadian

While an area is `circadian`, manual detection is skipped except for bulbs shed
for demand response (`event_handlers.py:588-596`). The skip exists because
`circadian_lighting`'s pushes looked like manual changes
(`tests/integration/test_circadian_no_false_manual.py:1-10`). After
internalization area_lighting issues those pushes itself, so it could tell its
own changes apart from a user's. Two paths were considered.

### Path A: keep the skip (taken)

The skip stays as it is. A user's change during circadian is not detected, and
the next tick overwrites it, which is what happens today. A shed bulb relit by
hand still latches `manual`. The user chose A, keeping today's behavior.

### Path B: detect manual changes from circadian targets (future)

B needs all of the following. Contexts alone do not work. A tagged `Context`
labels only the state write made inside the service call, and later reports
pushed by a bridge (Hue, ZHA) arrive with fresh contexts.

1. **Circadian targets.** Every circadian command (activation, tick, the
   per-light listener, a sleep change, a demand-response relight, a route
   turn-on) writes `_active_scene_targets[light] = {state: on, brightness,
   color_temp_kelvin or rgb_color, commanded_at, transition}`. Shed bulbs and
   inactive route lights get off targets. `rgb_color` targets need an hs or xy
   comparison in `state_matches_scene_target` (`controller.py:1317-1390`),
   which compares hs and xy only.
2. **Transition-window exemption.** The per-entity window
   (`event_handlers.py:622-639`, transition plus 4 s) never closes when
   `transition` (60 s) is at least `interval` (60 s), so detection would never
   fire. Circadian targets must skip the window and rely on the tolerance
   alone (±10 brightness, ±100 K). With haconf's 2700-5600 K range the
   steepest kelvin slope, just after sunrise, is 15 to 25 K a minute depending
   on day length. Night brightness moves by under 1 unit (of 255) a minute for
   every haconf group. A report mid-fade therefore stays inside the tolerance
   of the newest target.
3. **No-target rule.** Lights in a circadian area with no group (for example
   `light.kitchen_overhead_fluorescent_tubes`) carry no target, and
   `state_matches_scene_target` returns False without one. Without a rule,
   switching such a light on by hand latches the whole area `manual` and stops
   circadian everywhere in it. B must skip lights with no target while the
   area is circadian.
4. **Not-on to on exemption.** A bulb switched on by hand first reports the
   values it remembers, before the 1-second circadian update lands. B must
   exempt the first on-report after a not-on to on transition and let the
   update set the target.
5. **Self-heal.** Decide whether glitches during circadian (power-on defaults,
   recovery from `unavailable`) re-assert the circadian payload through
   `handle_scene_drift_reassert` or simply wait for the next tick.
6. **Tests.** A tick does not latch, a mid-fade report does not latch, a change
   outside tolerance latches, a hand-switched light with no group does not
   latch, and a bulb's first report after turning on does not latch.

## 8. Test strategy

- **Pure unit tests** (`tests/test_circadian.py`). Synthetic events give exact
  values at noon, midnight, sunrise, sunset and the quarter points. Further
  tests cover the exact-boundary case, missing events, kelvin, group
  brightness, sleep, payloads per type and the brightness scale. A parity test
  compares `percent` with a transliteration of `circadian_lighting`'s
  `async_calc_percent` over a grid of times at a real location with elevation
  0. The transliteration lives in the test with a citation of the vendored
  lines.
- **Clock** (`tests/integration/test_circadian_clock.py`). The interval tick
  uses `async_fire_time_changed`. Further tests cover the dispatcher signal,
  the sensor's state and attribute, schema defaults and errors, sun events
  matching `astral.sun` for `hass.config`, and teardown stopping the interval.
- **A `pin_circadian` fixture** in `tests/integration/conftest.py`
  monkeypatches `CircadianClock.values` to fixed `CircadianValues`. It returns a
  helper that sets the values and runs one tick. Tests that assert payloads or
  routes use it. Other tests run on the real clock and assert no values.
- **Adjuster lifecycle.** Every path that leaves or suspends circadian stops
  the adjuster, and a tick afterwards sends nothing. This replaces the switch
  spies in `test_circadian_switch_sync.py`, `test_raise_lower.py` and
  `test_circadian_kelvin_routes.py`.
- **Tick, light events, sleep and resume** each get their own test file. The
  plan names them.
- **Router and demand response.** The existing route and demand-response tests
  drive kelvin with `pin_circadian` instead of setting a switch's `colortemp`.
  Tests that only existed for the switch-off listener race
  (`_make_switch_off_update_state`, `test_demand_response_kelvin.py:29-37`)
  become assertions that a stopped area's router has no active route and a
  tick sends nothing.
- **Validation.** The tests for the `circadian_lighting` bootstrap YAML go.
  The test that a missing grouped light is reported stays. A new test checks
  that no `circadian_lighting` entity is required.
- **Reload** (`tests/integration/test_reload.py`, new, since no reload test
  exists today). One test per row of the table in 6.2, plus: an invalid config
  raises and changes nothing, `select.<area>_last_scene` emits no state change,
  state and timers survive, a removed area's controller is closed, a reload
  during an alert resumes circadian when the alert ends, a newly configured
  sensor that is on cancels its countdown, and a leader notification queued
  before a rewiring is ignored.
- **Teardown.** `async_teardown` runs after every integration test. The
  harness fails any test that leaves a loop timer, so a leak shows up
  everywhere at once. Further tests cover teardown before
  `homeassistant_started`, a timer callback queued before close, and entity
  listeners detached while Home Assistant keeps running.

Files that stub `circadian_lighting` entities today, each changed by the plan:
`tests/integration/conftest.py` (sensor and switch stubs at 87-94, switch
service mocks at 117-118), `test_circadian_kelvin_routes.py`,
`test_demand_response_kelvin.py`, `test_demand_response_circadian.py`,
`test_validation.py`, `test_manual_cluster_ignored.py` and
`test_motion_off_and_fadeouts.py`. Files that declare `circadian_switches` and
`circadian_switch` also include `test_alert.py`, `test_remote_events.py` and
`tests/test_circadian_kelvin_routes_schema.py`.

The gate for every commit is ruff check, ruff format check, mypy run from the
repository root exactly as `dagger/main.go` runs it, and the full pytest suite
with `-rs`, ending with 0 skipped.

## 9. haconf migration

### 9.1 Changes, on haconf branch `internalize-circadian`

- `area_lighting.yaml`:
  - Add `circadian: {min_colortemp: 2700, max_colortemp: 5600, interval: 60}`
    next to `areas:`. `transition` stays at its default of 60, as today.
  - Rename the 22 `circadian_switches:` keys to `circadian_groups:`, and the 132
    `circadian_switch:` keys to `circadian_group:`.
  - Delete the kitchen's `source:` (line 1581).
  - Give Stairs Main `min_brightness: 10`, `max_brightness: 55`,
    `sleep_entity: select.theater_last_scene`, `sleep_state: [movie]`,
    `sleep_brightness: 5` and `sleep_colortemp: 2700`.
  - Add the `coat_closet` area (9.3).
- Bounds follow decision 4. `area_lighting.yaml` already defines them for every
  group except Stairs Main, so the house moves to these values, which differ
  from the switch files today:

  | Group | Live (switch file) max/min | 3.0.0 max/min |
  |---|---|---|
  | Den Bookcase Uplight | 100/85 | 98/85 |
  | Den Desk Lamp, Gym Overhead, Kitchen Light Strips, Kitchen Overhead, Living Room All, Main Bathroom Overhead, Main Bedroom All, Media Room Lamps, Media Room Overhead, Theater Floor Lamps, Theater Hallway | 100/75 | 98/75 |
  | Den Overhead, Den Bathroom Overhead, Den Closet Overhead, Laundry Room Overhead, Main Closet Overhead | 100/65 | 98/65 |
  | Pantry Main | 100/75 | 98/65 |
  | Dining Room Chandelier | 85/50 | 90/50 |
  | Downstairs Bathroom Vanity | 90/45 | 80/45 |
  | Upstairs Bathroom Vanity | 90/55 | 80/45 |
  | Stairs Main | 55/10 | 55/10, from the switch file |

- `light.main_bedroom_tv_backlight` is in `haconf:switches/area_lighting.yaml:222`
  and nowhere else. It is not a bounds question, so decision 4 does not settle
  it. Adding it to `main_bedroom` would also put it under manual detection and
  skeleton scenes. The migration checks whether the entity exists. If it does,
  the question goes to the user with the approval request. If it does not, the
  entry is dropped.
- `automations/_global/lighting_circadian_daylight_on.yaml` and `..._off.yaml`
  read `sensor.area_lighting_circadian` instead of `sensor.circadian_values`.
- The description of `automations/office/clock_foreground_behavioral_scenes.yaml`
  (line 6) says `circadian_group`. `DEVELOPMENT_PLAN.md:91` names
  `sensor.area_lighting_circadian`.
- `configuration.yaml`: delete the `circadian_lighting:` block (356-359) and
  `switch dirs: !include_dir_merge_list switches` (line 60), since the
  directory empties. Update the commented recorder line (315) to the new id.
- Delete `switches/area_lighting.yaml` and `switches/circadian.yaml`.
- Remove `custom_components/circadian_lighting/`. HACS manages it when
  `/config/.storage/hacs.repositories` on the host has an entry with category
  `integration`, domain `circadian_lighting` and `installed: true`. That is the
  test `hacs-commit` applies (`haconf:tools/hacs-commit/hacs-commit:83-88`). A
  catalog entry that is not installed does not count. If HACS manages it,
  uninstall it through HACS and commit the deletion by hand, because
  `hacs-commit` commits only installed repositories (`haconf:CLAUDE.md:627`).
  Otherwise remove it in the branch.
- `CLAUDE.md`: rewrite "Circadian Lighting" (112-114), "Adding a New Room"
  step 3, "Adding a New Light" with its two-file subsection (665-707), and
  "What `area_lighting.reload` does and does not pick up" (765-812). Add a
  `CHANGELOG.md` entry dated on the cutover day.

### 9.2 Order and rollback

Release 2.0.1 rejects the new keys, and 3.0.0 rejects the old ones. The top
level is `PREVENT_EXTRA` (`__init__.py:53-69`), so either mismatch fails the
whole integration. The release and the config therefore land in one restart.
With 3.0.0 running and `circadian_lighting` still loaded, the restored switches
would drive the lights as a second writer.

1. Merge the `area_lighting` branch. `tag:auto` tags 3.0.0, and
   `release:github` publishes it.
2. Prepare and validate the haconf branch. Run 3.0.0's schema against the
   branch's `area_lighting.yaml` locally (the plan gives the command).
3. Approval gate. Ping the user with the bounds table, the TV backlight
   question, the HACS state of `circadian_lighting`, and the proposed cutover
   time. Do nothing on the host before approval.
4. In HACS, download `area_lighting` 3.0.0 without restarting. Uninstall
   `circadian_lighting` there if HACS manages it.
5. Wait for `hacs-commit` to push the `area_lighting` update, 10 to 20 minutes
   (`haconf:CLAUDE.md:621`). Record that commit's SHA and the SHA `main` had
   before it, for rollback. Rebase the haconf branch onto the new
   `origin/main`, merge it, and push. Record the merge SHA.
6. On the host, stash `automations.yaml` if the UI has changed it, fetch,
   fast-forward and pop the stash (`haconf:CLAUDE.md`, "ALWAYS preserve the
   host's uncommitted work"). Then run `ha core check` and `ha core restart`,
   once, at the time the user chose, because a restart drops the GreenEye feed
   and relights pending reminder buttons.
7. Check that:
   - no `missing_external_entities` Repairs issue exists,
   - `sensor.area_lighting_circadian` reads the expected `colortemp` (record
     `sensor.circadian_values` at a known time before cutover and compare at the
     same time of day),
   - the kitchen tubes take over at 4000 K,
   - the stairs drop to 5 % and 2700 K while the theater is in `movie`,
   - opening the coat closet lights the bars and the strip on the curve.

Rollback:

1. In HACS, redownload `area_lighting` 2.0.1, and reinstall
   `circadian_lighting` if it was uninstalled, without restarting.
2. Wait for `hacs-commit` to push both, so `main` vendors 2.0.1 again. Reverting
   the migration merge alone would leave the 3.0.0 vendor commit from step 5
   in `main` while the host runs 2.0.1.
3. Revert the migration merge (`git revert -m 1 <merge SHA>`) and push. Where
   the revert and the `hacs-commit` commit both restore
   `custom_components/circadian_lighting/`, keep the `hacs-commit` files.
   `git diff <SHA before the vendor commit> origin/main -- custom_components/area_lighting custom_components/circadian_lighting configuration.yaml switches area_lighting.yaml`
   prints nothing, unless a commit after the cutover changed those paths.
4. On the host, fast-forward as in step 6 of the cutover, then check that
   `/config/custom_components/area_lighting/manifest.json` reads 2.0.1 and
   `/config/custom_components/circadian_lighting/` exists. Run `ha core check`,
   and restart only if it passes.
5. Within about 7 days of cutover, Home Assistant's restore state still holds
   the switches as they were at cutover, on for every area that was circadian.
   2.0.1 never turns a switch off at startup, so afterwards turn off every
   `switch.circadian_lighting_*`, then call `area_lighting.lighting_circadian`
   for the areas whose `select.<area>_last_scene` reads `circadian`.

### 9.3 Coat closet

Today `light.coat_closet_light_bars` (a Caseta dimmer) is switched on at the
Lutron bridge by the closet's door trigger. The UI automation "Coat Closet
Light Bars - On" then turns on both circadian switches and sets
`light.coat_closet_light_strip` from the switch and the sensor, and "- Off"
reverses it (`haconf:automations.yaml:909-947`).

In 3.0.0:

- Area `coat_closet` ("Coat Closet"). Group Light Bars (40/65) holds the bars
  as `brightness`. Group Light Strip (65/100) holds the strip as `ct`. Scenes
  are `circadian` and `"off"`.
- area_lighting does not see the bridge-level pairing as an action, so a YAML
  automation `automations/coat_closet/circadian_follows_light_bars.yaml` calls
  `area_lighting.lighting_circadian` when the bars turn on and
  `area_lighting.lighting_off` when they turn off.
- The two UI automations get `initial_state: false`, the repository's way to
  retire a UI automation (`haconf:CLAUDE.md`, "To disable a UI automation in
  version control").
- `automations/coat_closet/sunrise_scene_transition.yaml`,
  `sunset_scene_transition.yaml`, `sync_daylight.yaml` and `sync_evening.yaml`
  stay. They switch `switch.coat_closet_lighting_color_temperature_warm` and
  `_cool`, not the circadian lights. The first two follow
  `input_boolean.lighting_circadian_daylight_lights_enabled`, which the
  retargeted daylight automations keep driving.

## 10. Risks

| Risk | Mitigation |
|---|---|
| The release and haconf land in different restarts, so the integration fails to load, or two writers drive the lights | The single-restart order in 9.2 and the approval gate |
| The house dims from 100 % to 98 % by day in 18 groups, and 3 more groups change bounds | Intended by decision 4. Listed in the haconf CHANGELOG |
| Sun times shift with `hass.config.elevation` | 2 to 3 minutes at house elevations, which barely moves the curve. Accepted |
| Reload races with in-flight activations | Closed controllers do not save or re-arm. The remaining gaps are in 6.4 |
| An entity added or removed on reload triggers user automations | Kept entities are updated in place. Only added and removed areas and scenes change entity presence, as a restart would |
| A not-on to on echo arrives more than 5 s after our command (slow bridge) | It sends one duplicate command with the same values, which is harmless |
| A tick takes longer than `interval` | The next tick is skipped for that area |
| Command volume | Same cadence (60 s) and the same 132 grouped lights as today, plus the coat closet's 2, minus shed bulbs and duplicate activation commands |
