# Internalize circadian lighting: implementation plan

> **For agentic workers:** execute task by task, a fresh implementer per task
> and an independent review after each. Read the spec first.

**Goal:** `area_lighting` computes and applies circadian brightness and color
temperature itself, no longer depends on `circadian_lighting`, and
`area_lighting.reload` applies a changed config to running areas. Released as
3.0.0, followed by the haconf cutover.

**Spec:** `docs/superpowers/specs/2026-10-10-internalize-circadian-design.md`.
Section numbers below (for example "spec 6.2") refer to it.

**Worktree:**
`/home/aaron/git/aaron/home-assistant-area-lighting/.claude/worktrees/internalize-circadian`,
branch `internalize-circadian`, based on `origin/main` at `20b3a85` (2.0.1).
Paths below are relative to `custom_components/area_lighting/` unless they
start with a repository-level file name (`README.md`, `CHANGELOG.md`, ...) or
`haconf:`.

## Ground rules

- **Order.** Tasks run in order. Each one leaves the suite green and is one
  commit.
- **Test first.** Write the named tests, watch them fail, implement, run the
  gate, commit.
- **Commit subjects** start with `(Major)`, `(Minor)` or `(Patch)`. Task 9 is
  the only `(Major)`. No em dashes, and never the string `skip ci`. Never
  mention an AI assistant anywhere.
- **Versions.** Do not edit the version in `pyproject.toml`, `manifest.json` or
  `uv.lock`. If `uv run` rewrites `uv.lock`, do not stage it.
- **Sandboxes.** An implementer whose sandbox cannot write `.git` leaves the
  work unstaged, and the controller commits it with the task's subject.
- **CHANGELOG.** Each code task adds or extends one bullet under
  `## Unreleased` in `CHANGELOG.md`. Task 14 merges them into the release entry
  and writes the migration notes. `README.md` and `CONFIGURATION.md` are
  rewritten once, in task 14, because the commits in between are not a
  release.
- **Line numbers** are from `20b3a85`. If a file has drifted, find the place by
  the quoted code.

## The gate

Baseline: 741 passed, 0 skipped. CI runs mypy from the repository root, and
from the component directory it reports 10 errors that CI does not see, so the
gate runs it from the root as `dagger/main.go` does.

```sh
cd /home/aaron/git/aaron/home-assistant-area-lighting/.claude/worktrees/internalize-circadian/custom_components/area_lighting \
  && uv run ruff check . \
  && uv run ruff format --check . \
  && (cd ../.. && uv run mypy custom_components/area_lighting --ignore-missing-imports) \
  && uv run pytest -n auto -rs
```

A pass has ruff and mypy clean, every test passing, no `skipped` in the
summary line, and nothing listed in the `-rs` section.

## File map

| File | Tasks |
|---|---|
| `circadian.py` (new) | 2 |
| `circadian_clock.py` (new) | 3, 11 |
| `controller.py` | 1, 5, 6, 7, 9, 10, 11, 12 |
| `alert.py` | 6 |
| `circadian_kelvin_router.py` | 1, 7, 9 |
| `config_schema.py`, `models.py` | 3, 4, 7, 9 |
| `event_handlers.py` | 8, 9, 11 |
| `__init__.py` | 1, 3, 6, 9, 10, 11, 12 |
| `timer_manager.py` | 1 |
| `switch.py`, `select.py`, `number.py`, `binary_sensor.py`, `scene.py`, `diagnostics.py` | 10 |
| `const.py` | 3, 5 |
| `manifest.json` | 8 |
| `services.yaml`, `strings.json`, `translations/en.json` | 13 |
| `README.md`, `CONFIGURATION.md`, `CHANGELOG.md`, `tests/README.md`, `TODO.md` | 11, 14, plus a CHANGELOG bullet in every code task |
| `tests/integration/conftest.py` | 1, 3, 8, 9 |

---

## Task 1: Close controllers, timers and listeners completely

Reload and the clock both need a teardown that leaves nothing running. Today
`shutdown()` (`controller.py:2513-2519`) cancels three timers and the self-heal
check, and nothing else. The event-handler unsubscribe list in
`hass.data[DOMAIN]["unsubs"]` is stored but never called.

**Files:** `timer_manager.py`, `controller.py`, `circadian_kelvin_router.py`,
`__init__.py`, `tests/integration/conftest.py`, `tests/test_timer_manager.py`,
`tests/integration/test_teardown.py` (new).

**Behavior:**

- `TimerHandle.close()` cancels the timer and sets a closed flag. Once closed,
  `start()` and `restore()` log at debug and return without scheduling, and
  `_fire()` returns without calling the callback. The last one matters because
  `call_later` and a past-due `restore()` queue `_fire()` as a task
  (`timer_manager.py:50`, `:85`), which cancelling the handle cannot retract.
- `AreaLightingController.shutdown()`:
  - sets `self._closed = True`, exposed as the read-only property `closed`,
  - closes the motion, motion-night and occupancy timers and cancels
    `_heal_selfcheck_handle`, as today,
  - calls `self._kelvin_router.deactivate()` when a router exists.
- After `shutdown()`, `_schedule_save()`, `_sync_kelvin_router()`,
  `_schedule_post_settle_selfcheck()` and `_run_post_settle_selfcheck()`
  return immediately. `_notify_state_change()` still calls its listeners but
  saves nothing.
- `async_setup` stores the unsubscribe that `hass.bus.async_listen_once`
  returns (`__init__.py:236`) as `hass.data[DOMAIN]["unsub_started"]`.
  `_on_started` sets it to `None` before anything else, because removing a
  `listen_once` listener that already fired logs an error.
- New `async def async_teardown(hass: HomeAssistant) -> None` in `__init__.py`:
  - calls `unsub_started` if it is set, then clears it,
  - calls every unsubscribe in `hass.data[DOMAIN]["unsubs"]` and empties the
    list,
  - calls `shutdown()` on every controller,
  - does nothing if `DOMAIN` is not in `hass.data`, and can be called twice.
- The `shutdown_controllers` fixture (`tests/integration/conftest.py:27-37`)
  awaits `async_teardown(hass)` instead of looping over controllers.

**Tests:**

- `tests/test_timer_manager.py::test_close_cancels_and_blocks_restart`
- `tests/test_timer_manager.py::test_close_drops_a_fire_already_queued`:
  `restore()` with a past deadline queues `_fire()`, `close()` runs before the
  loop gets to it, and the callback is never called.
- `tests/integration/test_teardown.py`:
  - `test_teardown_unsubscribes_event_handlers`: after teardown, a motion
    sensor going on does not call the controller's `handle_motion_on`
    (patched spy).
  - `test_closed_controller_does_not_save_state`: after `shutdown()`,
    `_notify_state_change()` does not call
    `StateStorage.async_save_area_state` (spy).
  - `test_closed_controller_does_not_sync_kelvin_router`
  - `test_teardown_deactivates_kelvin_router`: a routed area in circadian has
    `current_index is None` after teardown.
  - `test_teardown_is_idempotent`
  - `test_teardown_before_started_registers_no_entities`: teardown, then fire
    `homeassistant_started`, and no `area_lighting` entity exists.
  - `test_closed_controller_schedules_no_self_heal_check`

**CHANGELOG:** none. No user-visible change.

**Commit:** `(Patch) area_lighting: close controllers, timers and listeners completely`

**Verify:** the gate.

---

## Task 2: Circadian curve as pure functions

**Files:** `circadian.py` (new), `tests/test_circadian.py` (new).

**Behavior** (spec 3.2, 4). `circadian.py` imports nothing from Home Assistant
except `homeassistant.util.color.color_temperature_to_rgb`. Its module
docstring credits the formulas to circadian_lighting 2.1.6 (claytonjn,
Apache-2.0), the same license as this repository.

```python
def relevant_events(events: Iterable[tuple[str, float]], now_ts: float) -> dict[str, float]
def percent(events: Mapping[str, float], now_ts: float) -> float | None
def kelvin(pct: float, min_colortemp: float, max_colortemp: float) -> float
def brightness_pct(pct: float, min_brightness: float, max_brightness: float) -> float
def to_brightness(brightness_pct: float) -> int
def turn_on_payload(circadian_type: str, kelvin: float, brightness_pct: float) -> dict[str, Any]
```

- `relevant_events` sorts `(name, timestamp)` pairs by timestamp, takes
  `i = bisect_right(timestamps, now_ts)`, and returns `dict(events[i-2:i+2])`.
- `percent` copies `async_calc_percent`
  (`haconf:custom_components/circadian_lighting/__init__.py:263-298`):
  - `sunrise <= t <= sunset` uses the day parabola (`h` noon, `k` 100, `x`
    sunrise before noon and sunset after).
  - Otherwise `sunset <= t <= sunrise` uses the night parabola (`h` midnight,
    `k` -100, `x` sunset before midnight and sunrise after).
  - The result is `k * (1 - ((t - h) / (h - x)) ** 2)`. The inclusive bounds
    make the exact-boundary case return 0 instead of failing. Both are needed:
    at `t` equal to sunset, `bisect_right` puts tomorrow's sunrise in the
    window, so only the night branch matches.
  - It returns `None` when an event is missing, neither branch matches, or
    `h == x`.
- `kelvin` is `min + (max - min) * pct / 100` when `pct > 0`, else `min`.
- `brightness_pct` is `max` when `pct > 0`, else
  `min + (max - min) * (100 + pct) / 100`.
- `to_brightness` is `round(brightness_pct * 2.55)`, clamped to 1..255.
- `turn_on_payload` sets `brightness` to `to_brightness(brightness_pct)`:
  - `ct` gives `{"brightness", "color_temp_kelvin": round(kelvin)}`,
  - `rgb` gives `{"brightness", "rgb_color": (r, g, b)}`, ints rounded from
    `color_temperature_to_rgb(kelvin)`,
  - `brightness` gives `{"brightness"}`,
  - anything else raises `ValueError`.

**Tests** (`tests/test_circadian.py`). Synthetic events at midnight 00:00,
sunrise 06:00, noon 12:00 and sunset 18:00 give exact values.

- `test_relevant_events_returns_two_before_and_two_after`
- `test_percent_is_100_at_noon_and_minus_100_at_midnight`
- `test_percent_is_75_at_quarter_points` (09:00 gives 75, 21:00 gives -75)
- `test_percent_is_zero_exactly_at_sunrise_and_sunset`
- `test_percent_uses_separate_half_parabolas_when_noon_is_off_centre`
- `test_percent_is_none_when_an_event_is_missing`
- `test_kelvin_is_minimum_overnight_and_linear_by_day`
- `test_brightness_is_maximum_by_day_and_follows_the_curve_overnight`
- `test_to_brightness_rounds_and_clamps` (75 gives 191, 1 gives 3, 100 gives
  255, 0 gives 1)
- `test_payload_per_circadian_type`
- `test_percent_matches_circadian_lighting_reference`. Copy
  `async_calc_percent`'s arithmetic into a `_reference_percent` helper in the
  test, citing the vendored file and lines. Build events with astral 2.2
  `astral.sun.sunrise/sunset/noon/midnight` and `Observer(47.6, -122.3, 0)`
  for local dates around 2026-06-21 and 2026-12-21. Assert agreement within
  1e-9 at every 15 minutes over each day.

**CHANGELOG:** none until task 5. The functions are not wired in yet.

**Commit:** `(Minor) area_lighting: add the circadian curve as pure functions`

**Verify:** the gate.

---

## Task 3: Built-in clock, global `circadian:` block and sensor

**Files:** `const.py`, `models.py`, `config_schema.py`, `circadian_clock.py`
(new), `__init__.py`, `tests/integration/conftest.py`,
`tests/integration/test_circadian_clock.py` (new).

**Behavior** (spec 3.1, 3.4, 3.5, 5):

- `const.py` gains:
  - `SIGNAL_CIRCADIAN_TICK = "area_lighting_circadian_tick"`
  - `DEFAULT_CIRCADIAN_MIN_COLORTEMP = 2500`
  - `DEFAULT_CIRCADIAN_MAX_COLORTEMP = 5500`
  - `DEFAULT_CIRCADIAN_INTERVAL_SECONDS = 300`
  - `DEFAULT_CIRCADIAN_TRANSITION_SECONDS = 60`
- `models.CircadianConfig` holds `min_colortemp`, `max_colortemp`, `interval`
  and `transition`, defaulting to those constants.
  `AreaLightingConfig.circadian: CircadianConfig` uses a default factory.
- `config_schema.CIRCADIAN_SCHEMA` validates:
  - `min_colortemp` and `max_colortemp` as ints from 1000 to 10000,
  - `interval` as an int of at least 1,
  - `transition` as a float from 0 to 6553,
  - and rejects `min_colortemp > max_colortemp`.
  `parse_config` reads `raw.get("circadian", {})`. In `__init__.py`,
  `CONFIG_SCHEMA` gains `vol.Optional("circadian", default={})`.
- `circadian_clock.py`:
  - `CircadianValues(percent: float, kelvin: float)` is a frozen dataclass.
  - `CircadianClock(hass, config: CircadianConfig)`:
    - `values(now: datetime | None = None) -> CircadianValues | None`
      collects sunrise, sunset, noon and midnight for the local dates
      yesterday, today and tomorrow through
      `homeassistant.helpers.sun.get_astral_event_date` (skipping `None`),
      then calls `circadian.relevant_events`, `percent` and `kelvin`.
    - `last` is the newest non-`None` result.
    - `async_start()` registers `async_track_time_interval(hass,
      self._on_interval, timedelta(seconds=config.interval),
      cancel_on_shutdown=True)`.
    - `async_stop()` cancels it, and can be called twice.
    - `async_tick()` computes values, stores them in `last`, and sends
      `SIGNAL_CIRCADIAN_TICK` with the values. When `values()` returns `None`
      it keeps `last`, logs one warning per clock, and sends nothing.
  - `AreaLightingCircadianSensor` is a `SensorEntity`:
    - `entity_id` `sensor.area_lighting_circadian`, unique_id
      `area_lighting_circadian`, name "Area Lighting Circadian",
    - unit `%`, icon `mdi:theme-light-dark`, `should_poll = False`,
      `entity_registry_visible_default = False`,
    - `native_value` is `round(percent, 2)`, and `extra_state_attributes` is
      `{"colortemp": round(kelvin)}`, both read from `clock.last` or else
      `clock.values()`,
    - connects to the signal in `async_added_to_hass` with `async_on_remove`.
- `__init__.py`:
  - `async_setup` builds the clock after parsing, calls `async_start()`, and
    stores it as `hass.data[DOMAIN]["circadian_clock"]`.
  - `_on_started` registers the sensor through the sensor `DATA_COMPONENT`,
    the same way `_register_diagnostic_sensor` does.
  - `async_teardown` calls `clock.async_stop()`.
- `tests/integration/conftest.py` gains the fixture `pin_circadian`. It
  monkeypatches `CircadianClock.values` to return a `CircadianValues` the test
  chooses, and returns `async def pin(percent: float, kelvin: float, *, tick:
  bool = True)`, which sets the values and, when `tick` is true, awaits
  `clock.async_tick()` and `hass.async_block_till_done()`.

**Tests** (`tests/integration/test_circadian_clock.py`):

- `test_clock_sends_signal_every_interval` (`async_fire_time_changed`)
- `test_sensor_reports_percent_and_colortemp`
- `test_sensor_updates_on_tick`
- `test_circadian_block_defaults_match_circadian_lighting`
- `test_circadian_block_rejects_min_colortemp_above_max`
- `test_clock_events_match_astral_for_hass_config_location` (compares with
  `astral.sun` over an `Observer` built from `hass.config` latitude, longitude
  and elevation)
- `test_clock_keeps_last_values_when_sun_events_are_missing` (patch
  `get_astral_event_date` to return `None`)
- `test_teardown_stops_the_clock_interval`
- `test_pin_circadian_sets_values_and_ticks`

**CHANGELOG:** Added, `sensor.area_lighting_circadian` and the `circadian:`
block.

**Commit:** `(Minor) area_lighting: compute circadian values with a built-in clock`

**Verify:** the gate.

---

## Task 4: Sleep options and default bounds on circadian switches

The user-facing key is still `circadian_switches` until task 9.

**Files:** `config_schema.py` (`CIRCADIAN_SWITCH_SCHEMA`, lines 36-42, and the
parser at 330-339), `models.py` (`CircadianSwitchConfig`, lines 8-29),
`tests/test_circadian_group_schema.py` (new).

**Behavior:**

- The schema gains:
  - `sleep_entity` (`cv.entity_id`),
  - `sleep_state` (`vol.All(cv.ensure_list, [cv.string])`),
  - `sleep_brightness` (int, 1-100),
  - `sleep_colortemp` (int, 1000-10000).
- A schema-level validator rejects `sleep_entity` without `sleep_state`, with
  the message "sleep_state is required when sleep_entity is set".
- The model fields default to `circadian_lighting`'s values: `min_brightness`
  1, `max_brightness` 100, `sleep_entity` None, `sleep_state` empty,
  `sleep_brightness` 1, `sleep_colortemp` 1000. The old `None` bounds go.
  `parse_config` fills the defaults.
- The bootstrap text in `_build_circadian_switches_block` now always prints
  both bounds. Task 8 deletes it.

**Tests** (`tests/test_circadian_group_schema.py`):

- `test_bounds_default_to_1_and_100`
- `test_sleep_options_parse`
- `test_sleep_entity_requires_sleep_state`
- `test_sleep_defaults_match_circadian_lighting`
- `test_sleep_state_accepts_a_single_string`

**CHANGELOG:** Added, sleep options on circadian groups.

**Commit:** `(Minor) area_lighting: accept sleep options and default bounds on circadian switches`

**Verify:** the gate.

---

## Task 5: Controller drives circadian lights from the clock

**Files:** `const.py`, `controller.py`,
`tests/integration/test_circadian_switch_sync.py` (renamed with `git mv` to
`tests/integration/test_circadian_adjuster_lifecycle.py`),
`tests/integration/test_raise_lower.py`, `tests/integration/test_logging.py`
(comment at line 77), `tests/integration/test_circadian_tick.py` (new).

**Behavior** (spec 3.2, 3.3):

- `const.py`: `CIRCADIAN_INITIAL_TRANSITION_SECONDS = 1` and
  `CIRCADIAN_COMMAND_ECHO_SECONDS = 5`.
- Payload. `_circadian_payload(self, light: LightConfig, values:
  CircadianValues) -> dict | None` returns `None` when the light has no group
  or no `circadian_type`. Otherwise:
  - the group sleeps when `sleep_entity` is set, its state exists, and that
    state is in `sleep_state`,
  - kelvin is `sleep_colortemp` when sleeping, else `values.kelvin`,
  - the brightness percentage is `sleep_brightness` when sleeping, else
    `circadian.brightness_pct(values.percent, min, max)`,
  - the result is `{"entity_id": light.id, **circadian.turn_on_payload(type,
    kelvin, brightness percentage)}`.
  It replaces `_circadian_turn_on_data` (`controller.py:968-988`), which read
  the switch and `sensor.circadian_values`.
- `_circadian_values()` returns `clock.values()`, or `None` when no clock
  exists.
- Sending. `_send_circadian(self, payload, transition)` sets
  `self._circadian_commanded_at[entity_id] = time.monotonic()`, then calls
  `_call_service("light.turn_on", transition=int(transition), **payload)`.
  Every circadian command goes through it.
- Lifecycle. These replace `_enable_circadian_switches` and
  `_disable_circadian_switches` (`controller.py:1433-1458`) at every caller
  (lines 813, 837, 940, 1717, 1878, 1917, 1955, 1958, 1976, 2007):
  - `_start_circadian()`, synchronous and idempotent. It returns if the
    controller is closed. Otherwise it connects `SIGNAL_CIRCADIAN_TICK` with
    `async_dispatcher_connect` and keeps the unsubscribe.
  - `_stop_circadian()`, synchronous and idempotent. It deactivates the kelvin
    router first, then disconnects.
  - The property `circadian_running` reports whether it is connected.
- Tick. The signal handler schedules `_circadian_tick(values)` with
  `hass.async_create_task`. The tick:
  - returns if not running, closed, or `_alert_active`,
  - returns with a debug log if the previous tick for this area is still
    running (a `_circadian_tick_running` flag),
  - awaits `_sync_kelvin_router()`,
  - for each light in `area.all_lights` for which `_circadian_adjustable(light)`
    holds, sends the payload with the configured `transition`, gathered. It
    skips lights that are not `on` or were commanded within
    `CIRCADIAN_COMMAND_ECHO_SECONDS`.
- `_circadian_adjustable(light) -> bool` is the predicate in spec 3.2. It is
  false for a light with no group or no type, a light in `dr_shed_ids`, a
  cluster while demand response is active, and a route light outside the
  router's active route. Route membership expands clusters with
  `_expand_cluster_ids`, as `_circadian_on_ids` does. Tasks 6 and 7 reuse it.
- Activation. `_activate_circadian` (`controller.py:922-966`) calls
  `_start_circadian()` where it awaited `_enable_circadian_switches`. Each
  non-route grouped light gets its payload through `_send_circadian` with
  `CIRCADIAN_INITIAL_TRANSITION_SECONDS`. Shed lights and clusters are handled
  as today.
- `recompute_and_apply_circadian_dr` (`controller.py:990-1030`) relights
  through `_send_circadian` with the 1-second transition.
- Gap fixes:
  - In the `current_scene` setter (`controller.py:443-459`), leaving
    circadian calls `_stop_circadian()` instead of only deactivating the
    router, and setting `circadian` calls `_start_circadian()` without sending
    any light command.
  - `handle_scene_activated("circadian")` (`controller.py:1946-1951`) calls
    `_start_circadian()`.
- Delete `handle_circadian_enabled` (`controller.py:2426-2428`).
- `shutdown()` calls `_stop_circadian()`.
- `diagnostic_snapshot()` gains `"circadian_running"`.
- No code calls a `switch.*` service any more.

**Tests:**

- `test_circadian_adjuster_lifecycle.py` (renamed). The 7 existing tests become
  the following, each asserting `not ctrl.circadian_running` and that a pinned
  tick sends no `light.turn_on`:
  - `test_lighting_on_from_circadian_stops_adjuster`
  - `test_lighting_favorite_from_circadian_stops_adjuster`
  - `test_external_scene_activation_non_circadian_stops_adjuster`
  - `test_manual_light_change_from_circadian_stops_adjuster`
  - `test_holiday_change_from_circadian_stops_adjuster`
  - `test_off_from_circadian_stops_adjuster`
  - `test_activating_non_circadian_scene_stops_adjuster`

  New:
  - `test_select_leaving_circadian_stops_adjuster`
  - `test_select_entering_circadian_starts_adjuster_without_commands`
  - `test_external_circadian_scene_starts_adjuster`
  - `test_circadian_never_calls_switch_services`
- `test_raise_lower.py::test_lighting_raise_disables_circadian_switches` becomes
  `test_lighting_raise_stops_circadian_adjuster`.
- `tests/integration/test_circadian_tick.py` (all use `pin_circadian`):
  - `test_activation_sends_payload_with_one_second_transition`
  - `test_tick_updates_only_lights_that_are_on`
  - `test_tick_uses_configured_transition`
  - `test_tick_skips_shed_lights`
  - `test_tick_skips_while_alert_active`
  - `test_tick_skips_lights_commanded_within_echo_window`
  - `test_tick_skips_route_lights_outside_the_active_route`: a routed area
    with the router on its fallback route, and a grouped light of the other
    route reporting `on`. The tick sends that light nothing. Task 7 adds the
    crossfade case.
  - `test_tick_skips_clusters_under_demand_response`
  - `test_rgb_light_gets_rgb_color`
  - `test_brightness_light_gets_brightness_only`
  - `test_group_bounds_shape_overnight_brightness` (percent -50 with bounds
    40/100 gives `round(70 * 2.55)` = 178)
  - `test_dimmed_circadian_area_is_not_adjusted`
  - `test_overlapping_ticks_do_not_run_concurrently`

Existing demand-response tests keep passing unchanged. The switch stubs they
set are now ignored, and task 8 removes them. A test that advances time past
`interval` with `async_fire_time_changed` (the motion and occupancy timer
tests do) now also fires a clock tick. If such a test has an area in circadian
and counts light calls, pin the values and expect the tick's calls.

**CHANGELOG:** Changed, circadian lights are driven by area_lighting itself
(one writer, `round(pct * 2.55)`, `rgb_color` for rgb lights, shed and alert
bulbs skipped).

**Commit:** `(Minor) area_lighting: drive circadian lights from the built-in clock`

**Verify:** the gate.

---

## Task 6: Lights switched on mid-circadian, sleep changes, and resume at start

**Files:** `controller.py`, `alert.py`, `__init__.py` (`_on_started`),
`tests/integration/test_circadian_light_events.py`,
`tests/integration/test_circadian_sleep.py`,
`tests/integration/test_circadian_resume.py` (all new),
`tests/integration/test_alert.py`.

**Behavior** (spec 3.2 steps 5 to 7, spec 5):

- When `_start_circadian()` connects, it also subscribes with
  `async_track_state_change_event` to:
  - the ids of every light in `area.all_lights` that has a group, with
    `_on_circadian_light_event`,
  - each group's `sleep_entity`, with `_on_circadian_sleep_event`.

  `_stop_circadian()` removes both. This does not depend on the area's
  `event_handlers` flag, as `circadian_lighting`'s listeners did not.
- `_on_circadian_light_event` acts only when `new_state.state == "on"` and the
  old state is missing or not `on`. It does nothing when:
  - the controller is closed or an alert is active,
  - `_circadian_adjustable(light)` is false. That covers shed lights (manual
    detection's shed exception keeps handling them), clusters while demand
    response is active (a cluster turn-on would relight shed members), and
    route lights outside the active route,
  - the light was commanded within `CIRCADIAN_COMMAND_ECHO_SECONDS`.

  Otherwise it sends that light's payload with the 1-second transition.
- `_on_circadian_sleep_event` acts only when the state value changed
  (attribute-only changes are ignored), and does nothing while the controller
  is closed or an alert is active. It sends the group's lights that are on and
  adjustable their payload with the 1-second transition.
- `async_resume_circadian()` does nothing unless the state is circadian, not
  dimmed, no alert is active, and the controller is open. When it runs, it
  calls `_start_circadian()`, awaits `recompute_and_apply_circadian_dr()` (so
  areas without routes refresh their shed set too), awaits
  `_sync_kelvin_router()`, then runs one tick with the configured transition,
  using `_circadian_values()`. Task 7 inserts the router adoption before the
  recompute.
- `alert.py`, in the `finally` block (`alert.py:303-321`): when the
  demand-response branch does not run, await
  `controller.async_resume_circadian()` after `_alert_active` is cleared. Its
  guards cover closed, non-circadian and dimmed areas. This restarts an
  adjuster a reload stopped mid-alert, and applies sleep changes the alert
  suppressed.
- `_on_started` calls `await ctrl.async_resume_circadian()` for every
  controller after the `reconcile_startup_state` loop, in its own
  `try`/`except` with a logged exception, like its neighbors.

**Tests:**

- `test_circadian_light_events.py`:
  - `test_light_switched_on_externally_gets_circadian_values`
  - `test_activation_echo_is_not_commanded_twice`
  - `test_shed_light_switched_on_is_left_to_manual_detection` (it also still
    latches `manual`)
  - `test_unavailable_to_on_gets_circadian_values`
  - `test_attribute_change_while_on_is_ignored`
  - `test_light_without_group_is_ignored`
  - `test_events_after_adjuster_stops_are_ignored`
  - `test_events_handled_when_event_handlers_disabled`
  - `test_cluster_switched_on_under_demand_response_is_ignored`
- `test_circadian_sleep.py`:
  - `test_sleeping_group_uses_sleep_brightness_and_colortemp`
  - `test_sleep_entity_change_updates_group_with_one_second_transition`
  - `test_sleep_attribute_only_change_is_ignored`
  - `test_missing_sleep_entity_means_awake`
  - `test_sleep_applies_only_to_its_group`
  - `test_sleep_change_during_alert_is_applied_after_the_alert`: no command
    while the alert runs, the sleep payload once it ends
  - `test_sleep_change_skips_clusters_under_demand_response`
- `test_circadian_resume.py`. Seed persisted state with the `hass_storage`
  fixture under key `area_lighting.state`, then fire `homeassistant_started`.
  - `test_started_resumes_persisted_circadian_area`
  - `test_resume_updates_only_lights_that_are_on`
  - `test_resume_skips_dimmed_circadian_area`
  - `test_resume_skips_manual_and_scene_areas`
  - `test_resume_syncs_kelvin_router`
  - `test_resume_recomputes_shed_set_without_routes`
- `test_alert.py`:
  - `test_alert_end_resumes_a_stopped_circadian_adjuster`: stop the adjuster
    while the alert runs (as reload does), let the alert finish with demand
    response off, and assert `circadian_running` and that a pinned tick
    commands the lights.
  - `test_alert_end_leaves_non_circadian_area_alone`

Existing tests that set a grouped light from off to on while its area is
circadian now also get one `light.turn_on` from the listener. Where such a test
counts calls, decide case by case. If the on-report stands for the echo of the
integration's own command, the echo window must suppress it, so fix the code.
If it stands for a person switching the light on, expect the extra call.

**CHANGELOG:** Added, lights switched on during circadian get the current
values within a second, sleep groups react to their entity, and circadian
resumes after a restart.

**Commit:** `(Minor) area_lighting: update lights switched on mid-circadian and resume after restart`

**Verify:** the gate.

---

## Task 7: Kelvin router follows the clock

**Files:** `circadian_kelvin_router.py`, `controller.py`, `config_schema.py`,
`models.py`, `tests/integration/test_circadian_kelvin_routes.py`,
`tests/integration/test_demand_response_kelvin.py`,
`tests/test_circadian_kelvin_routes_schema.py`.

**Behavior** (spec 2, design decisions on routes):

- The router no longer subscribes to an entity.
  - Delete `read_source_colortemp` (lines 26-44) and `_on_source_changed`.
  - `_active: bool` replaces `_unsub` as the "router is live" sentinel in
    `sync_to_state`, `deactivate` and both stale-reconcile guards in
    `_reconcile` (lines 166 and 188).
  - The reconcile reads kelvin from `ctrl.circadian_kelvin()`, a new
    controller method returning `values.kelvin` from `_circadian_values()`, or
    `None`. `None` selects the fallback route, as an unavailable source does
    today.
- The controller's `_route_source_colortemp` (lines 870-879) returns
  `self.circadian_kelvin()`.
- The tick from task 5 already calls `_sync_kelvin_router()`, so routes now
  follow the clock at each tick.
- Route turn-ons. The router turns each light on through a new controller
  method, `async_turn_on_route_light(entity_id, transition)`. For a light with
  a group and type, it sends the group payload through `_send_circadian`,
  which also records the echo time. For any other light it sends only
  `entity_id` and `transition`, as the router does today, so the kitchen tubes
  are switched on and off only. Turn-offs are unchanged.
- Adoption (spec 5 step 2). New router method `adopt_current_state()` sets
  `_active`, picks `_current_index` as the first route with a light that is
  on (clusters expanded), or, when no route light is on, `select_route` over
  `ctrl.circadian_kelvin()` with no prior index, and sets `_last_shed_ids` to
  the controller's `dr_shed_ids`. It sends no command, and does nothing when
  the router is already active.
  `async_resume_circadian()` calls it right after `_start_circadian()`, so the
  sync that follows is stable unless the kelvin crossed a route boundary.
- `source` is still accepted and parsed, but nothing reads it.
  `validate_circadian_kelvin_routes` drops the "must specify source" check
  (lines 638-644), so areas with two or more groups need no `source`. Task 9
  rejects the key.

**Tests:**

- `test_circadian_kelvin_routes.py`. Drive kelvin with `pin_circadian` instead
  of `hass.states.async_set(source, ..., {"colortemp": ...})`:
  - `test_entering_circadian_activates_route_for_current_colortemp`, with
    the same name
  - `test_colortemp_change_swaps_active_route` (pin, then tick)
  - `test_hysteresis_suppresses_flap_at_boundary`
  - `test_source_unavailable_selects_fallback` becomes
    `test_missing_clock_values_select_fallback`
  - `test_listener_inactive_outside_circadian` becomes
    `test_tick_outside_circadian_does_not_route`
  - `test_select_scene_change_deactivates_router`
  - `test_crossfade_passed_as_transition`
  - new `test_route_turn_on_carries_group_payload`
  - new `test_route_light_without_group_is_switched_only`
  - new `test_tick_after_route_change_leaves_outgoing_lights_fading`: pin a
    kelvin that swaps routes, hold the outgoing grouped route light at `on`
    (its off-state report delayed), tick again, and assert it gets no
    `light.turn_on`. Repeat with a cluster route under demand response.
- `test_circadian_resume.py`:
  - new `test_resume_leaves_off_route_lights_off`, for a grouped and an
    ungrouped route light
  - new `test_resume_adopts_the_route_that_is_on`
  - new `test_resume_crossfades_when_the_route_changed_while_down`
- `test_demand_response_kelvin.py`:
  - Replace every write to `_SWITCH`, `_LOFT_SWITCH`, `_MEDIA_SOURCE` and
    `_STUDY_SOURCE` with `pin_circadian`, and remove the `source` keys from
    the configs.
  - Delete `_make_switch_off_update_state` and every call to it (lines 405,
    442, 474, 506, 1079 and 1119). The race it simulated (the switch turning
    off fires the router listener) cannot happen without a listener.
  - Replace every `_kelvin_router._unsub` assertion (lines 397, 411, 436, 448,
    469, 480, 513, 1067, 1086 and 1126 here, and
    `test_circadian_kelvin_routes.py:330` and `:333`) with `_active`.
  - Drive the den hysteresis tests with `pin_circadian` instead of writing
    `_DEN_SWITCH` (lines 517, 581 and 615), and delete the constant.
  - `test_visual_scene_transition_deregisters_router`,
    `test_off_fade_from_circadian_deregisters_router`,
    `test_circadian_dim_deregisters_router`,
    `test_external_off_shed_clear_sticks`,
    `test_dim_from_dark_disables_circadian_and_unarms_router` and
    `test_dim_from_dark_does_not_leave_circadian_armed` keep their names and
    now assert `router.current_index is None` and that a pinned tick sends no
    route command.
  - `test_unavailable_source_controller_agrees_with_router_fallback` becomes
    `test_missing_clock_values_controller_agrees_with_router_fallback`.
- `tests/test_circadian_kelvin_routes_schema.py`:
  - delete `test_parse_defaults_source_to_only_circadian_switch`,
    `test_validator_requires_explicit_source_when_two_switches` and
    `test_validator_requires_explicit_source_when_no_switches`,
  - add `test_validator_accepts_routes_without_source_and_two_groups`.

**CHANGELOG:** Changed, kelvin routes follow the built-in clock, and route
lights turn on with their group's brightness and color temperature.

**Commit:** `(Minor) area_lighting: route kelvin from the built-in clock`

**Verify:** the gate, then
`grep -rn "_make_switch_off_update_state\|_unsub\b\|_DEN_SWITCH\|read_source_colortemp" custom_components/area_lighting`
from the worktree root finds nothing.

---

## Task 8: Stop depending on circadian_lighting

**Files:** `event_handlers.py`, `manifest.json`,
`tests/integration/conftest.py`, `tests/integration/test_validation.py`,
`tests/integration/test_dependencies.py`,
`tests/integration/test_manual_cluster_ignored.py`,
`tests/integration/test_motion_off_and_fadeouts.py`,
`tests/integration/test_demand_response_circadian.py`,
`tests/integration/test_circadian_no_false_manual.py` (docstring).

**Behavior** (spec 3.1, 3.4):

- `async_validate_external_entities` (`event_handlers.py:173-278`):
  - drops `sensor.circadian_values` (lines 194-197) and the per-switch
    requirement (lines 210-216),
  - keeps requiring every light assigned to a group, with the description
    "light assigned to circadian group {area.name}/{group.name}
    ({circadian_type})".
- Delete `_build_circadian_switches_block` (lines 73-102) and the
  `circadian_lighting:` and `switch:` parts of `_build_bootstrap_yaml` (lines
  145-168).
- `manifest.json`: `after_dependencies` becomes `["lutron_caseta"]`.
- `tests/integration/conftest.py`:
  - remove the `sensor.circadian_values` and switch stubs (lines 87-94) and fix
    the module docstring,
  - remove the `switch.turn_on` and `switch.turn_off` mocks from
    `_AggregatedCalls` (lines 117-118) and fix the fixture docstring.
- Remove every remaining `switch.circadian_lighting_*` state stub in the test
  files listed above.

**Tests:**

- `test_validation.py`:
  - delete `test_bootstrap_yaml_includes_circadian_lighting_top_level` and
    `test_bootstrap_yaml_includes_circadian_switches_block`,
  - drop the stubs in the other tests,
  - keep `test_validator_flags_light_referenced_by_circadian_switch_but_missing`
    (task 9 renames it),
  - in `test_validator_multiple_missing_single_error_line` (line 111) and
    `test_validator_issue_includes_yaml_bootstrap` (line 182), assert that
    `sensor.circadian_values` is absent from the report instead of present,
  - add `test_validator_does_not_require_circadian_lighting_entities`.
- `test_dependencies.py::test_manifest_does_not_load_after_circadian_lighting`
  (new).

**CHANGELOG:** Removed, the `circadian_lighting` integration is no longer used
or required, and Repairs no longer asks for its entities.

**Commit:** `(Minor) area_lighting: stop depending on the circadian_lighting integration`

**Verify:** the gate, then
`grep -rn "circadian_lighting\|circadian_values" custom_components/area_lighting --include=*.py`
from the worktree root. Matches may remain only in comments, in
`circadian.py`'s credit line, in tests that check the integration is gone or
compare with its formula, and in `CircadianSwitchConfig.entity_id`
(`models.py:29`), which task 9 deletes. Task 9 repeats the search with no
`models.py` exception.

---

## Task 9: Rename to circadian groups and remove the routes source (the Major)

**Files:** `config_schema.py`, `models.py`, `controller.py`, `event_handlers.py`,
`circadian_kelvin_router.py`, `__init__.py`, and the test configs in
`tests/integration/conftest.py`, `test_alert.py`,
`test_circadian_kelvin_routes.py`, `test_demand_response_circadian.py`,
`test_demand_response_kelvin.py`, `test_manual_cluster_ignored.py`,
`test_motion_off_and_fadeouts.py`, `test_remote_events.py`,
`test_validation.py`, `tests/test_circadian_kelvin_routes_schema.py`,
`tests/test_circadian_group_schema.py`, `tests/test_circadian_kelvin_router.py`
(it builds `CircadianKelvinRoutesConfig` with `source=` at line 28), and every
test file added in tasks 1 to 8 that declares the old keys.

**Behavior** (spec 3.5):

- Keys:
  - Area `circadian_switches` becomes `circadian_groups`
    (`CIRCADIAN_GROUP_SCHEMA`).
  - Light `circadian_switch` becomes `circadian_group`.
  - The old keys and `circadian_kelvin_routes.source` stay in the schemas only
    as validators that always raise:
    - "circadian_switches was renamed to circadian_groups in 3.0.0"
    - "circadian_switch was renamed to circadian_group in 3.0.0"
    - "circadian_kelvin_routes.source was removed in 3.0.0: routes follow the
      integration's own circadian color temperature; delete the key"
- Models:
  - `CircadianSwitchConfig` becomes `CircadianGroupConfig`, losing `area_id`,
    `area_name`, `short_name`, `full_name` and `entity_id`.
  - `LightConfig.circadian_switch` becomes `circadian_group`.
  - `AreaConfig.circadian_switches` becomes `circadian_groups`, and
    `circadian_switch_for_light` becomes `circadian_group_for_light`.
  - `CircadianKelvinRoutesConfig.source` and its parse branch
    (`config_schema.py:475-484`) go.
- New `validate_circadian_groups(config)` in `config_schema.py`, called next to
  `validate_circadian_kelvin_routes` in `async_setup` and in `_handle_reload`.
  It raises `vol.Invalid` when:
  - two groups in an area share a name,
  - `min_brightness > max_brightness`,
  - a light's `circadian_group` is not declared in its area,
  - only one of `circadian_group` and `circadian_type` is set.
- Rename the remaining internal identifiers (`_circadian_on_ids` reads
  `light.circadian_group`, and so on), plus log and error text.

**Tests:**

- Rename the keys in every listed test config, and the test
  `test_validator_flags_light_referenced_by_circadian_switch_but_missing` to
  `..._circadian_group_but_missing`.
- `tests/test_circadian_group_schema.py` gains:
  - `test_circadian_switches_key_is_rejected_with_rename_hint`
  - `test_circadian_switch_light_key_is_rejected_with_rename_hint`
  - `test_routes_source_is_rejected_with_removal_hint`
  - `test_unknown_group_is_rejected`
  - `test_type_without_group_is_rejected`
  - `test_group_without_type_is_rejected`
  - `test_duplicate_group_name_is_rejected`
  - `test_min_brightness_above_max_is_rejected`
- `tests/test_circadian_kelvin_routes_schema.py`:
  `test_explicit_source_and_crossfade_round_trip` becomes
  `test_crossfade_round_trips`.
- `tests/test_circadian_kelvin_router.py`: drop the `source=` argument.

**CHANGELOG:** Changed, three BREAKING bullets: the key rename, the removal of
`source`, and the stricter group validation. Each says what to edit.

**Commit:** `(Major) area_lighting: rename circadian switches to circadian groups and remove the routes source`

**Verify:** the gate, then from the worktree root:

- `grep -rn "circadian_switch\|source=" custom_components/area_lighting`. Only
  the rename-hint validators and their tests may match `circadian_switch`, and
  no `CircadianKelvinRoutesConfig` call may pass `source=`.
- Task 8's `circadian_lighting` search, now without the `models.py`
  exception.

---

## Task 10: Entities refresh from a new config

Groundwork for reload (spec 6.1). No user-visible change.

**Files:** `controller.py`, `switch.py`, `select.py`, `number.py`,
`binary_sensor.py`, `scene.py`, `diagnostics.py`, `__init__.py`,
`tests/integration/test_entity_refresh.py` (new),
`tests/integration/test_teardown.py`.

**Behavior:**

- Every per-area entity class gains `refresh_from_config()`. It recomputes the
  attributes it derived from config at construction from the live
  `self._controller.area`: `_attr_name` everywhere, and `_attr_options` on
  `AreaLastSceneSelect`. Then it calls `async_write_ha_state()` if it is
  added.
- `AreaLightingScene.refresh_from_config(area, scene_cfg)` and
  `BehavioralScene.refresh_from_config(area, name)` replace the stored config
  objects and the name.
- `AreaLightingDiagnosticSensor.sync_controller_listeners()` removes its
  listener from controllers no longer in `hass.data[DOMAIN]["controllers"]`
  and adds it to new ones. It tracks which controllers it is attached to.
- `__init__.py` records every entity it adds in
  `hass.data[DOMAIN]["entities"]`, a dict from area id to list (scenes and
  helpers), with the global switches and both sensors under the key
  `"__global__"`.
- `remove_state_listener` (`controller.py:638-639`) compares with `!=`
  instead of `is not`. Entities pass `self._on_controller_change`, a new bound
  method object on every access, so the identity test never matches and no
  listener is removed today.
- `async_teardown` removes every recorded entity with `async_remove()` before
  closing the controllers, then empties the record. That runs
  `async_will_remove_from_hass`, which detaches controller listeners and
  cancels the diagnostics sensor's one-second interval.

**Tests** (`tests/integration/test_entity_refresh.py`):

- `test_select_options_follow_a_new_scene_list`
- `test_area_name_change_updates_friendly_names`
- `test_scene_entity_applies_refreshed_scene_config`
- `test_refresh_without_changes_writes_no_state_change`
- `test_diagnostics_listens_to_new_controllers_only`
- `test_registered_entities_are_recorded_by_area`
- `test_removed_entity_no_longer_listens_to_its_controller`: remove a select
  entity, call `_notify_state_change()`, and assert the controller's listener
  list no longer holds it and no state is written.
- `test_teardown.py::test_teardown_removes_entities_and_the_diagnostics_interval`,
  with Home Assistant still running.

**CHANGELOG:** none.

**Commit:** `(Patch) area_lighting: let entities refresh from a new config and detach on removal`

**Verify:** the gate.

---

## Task 11: Reload applies a new config to running areas

**Files:** `__init__.py`, `controller.py`, `event_handlers.py`,
`circadian_clock.py`, `TODO.md`, `tests/integration/test_reload.py` (new).

**Behavior** (spec 6.2, steps 1 to 3, 5 and 7 to 11). Areas being added or
removed is task 12. In this task, a reload whose set of enabled area ids or
scene slugs differs raises `HomeAssistantError` with "adding or removing areas
or scenes needs a restart".

- `_handle_reload`:
  - runs under `hass.data[DOMAIN]["reload_lock"]`,
  - validates as today plus `validate_circadian_groups`,
  - turns every failure, `vol.Invalid` from `CONFIG_SCHEMA` included, into a
    logged error and a raised `HomeAssistantError`, leaving the runtime
    untouched.
- `async_setup_event_handlers(hass, config)` takes the config as an argument
  instead of reading it once through `_config(hass)`. Reload calls the stored
  unsubscribes, then registers again.
- Rebuild the clock. Stop the old one, construct a new one from
  `new_config.circadian`, start it, and run one `async_tick()`.
- New `async_apply_config(self, area, global_config)` on the controller (spec
  6.2 step 5):
  - stops circadian, swaps `area`, `_global_config` and `_scene_self_heal`,
    builds a new router, and clears `leader` and `followers`,
  - cancels the motion timers when the new area has no motion sensors or any
    configured motion sensor reads `on`, and the occupancy timer when it has
    no occupancy sensors or any configured occupancy sensor reads `on`. It
    turns no lights on,
  - in a scene, rebuilds scene tracking. Extract the scene branch of
    `reconcile_startup_state` (`controller.py:325-328`) into
    `_rebuild_scene_tracking()` and call it from both places,
  - awaits `async_resume_circadian()`, which recomputes the circadian shed set
    from the new light list whether or not the area has routes, then runs
    `_enforce_occupancy_timer()` and `_notify_state_change()`.
- Extract leader and follower wiring (`__init__.py:130-147`) into
  `_wire_leaders(controllers)`. Reload calls it after clearing every
  controller's links.
- `_propagate_to_followers` (`controller.py:730-747`) passes itself as a
  required `leader` argument to `handle_leader_change`, which returns at once
  when `leader is not self.leader`. A notification queued before a reload
  rewired the pair then does nothing. Tests that call `handle_leader_change`
  directly pass the leader.
- Assign `hass.data[DOMAIN]["config"]`, call `refresh_from_config()` on every
  recorded entity, and rerun `async_validate_external_entities` and
  `async_validate_lutron_remotes` with the new config.
- `TODO.md`: mark the "Hot reload of `lutron_remotes` config" entry done, as the
  file's other finished entries are marked.

**Tests** (`tests/integration/test_reload.py`). Each test sets up from one
config, patches `custom_components.area_lighting.async_hass_config_yaml` to
return the changed config, and calls `area_lighting.reload` with
`blocking=True`.

- `test_reload_applies_a_new_light_list`
- `test_reload_applies_new_motion_sensors`
- `test_reload_applies_new_remote_favorite_cycle`
- `test_reload_applies_group_bounds_and_sleep_options`
- `test_reload_applies_the_global_circadian_block`
- `test_reload_applies_scene_self_heal`
- `test_reload_keeps_state_dimmed_flag_and_timer_deadlines`
- `test_reload_cancels_timers_for_removed_sensors`
- `test_reload_cancels_occupancy_countdown_when_a_new_sensor_is_on`
- `test_reload_cancels_motion_countdown_when_a_new_sensor_is_on`
- `test_reload_recomputes_circadian_shed_set_without_routes`: demand
  response active, a circadian area with no routes, and a reload that adds a
  light. The shed set and the lights follow the new list.
- `test_reload_during_alert_resumes_circadian_after_the_alert`
- `test_leader_notification_queued_before_rewiring_is_ignored`
- `test_reload_resumes_circadian_with_new_groups`
- `test_reload_rewires_leader_and_followers`
- `test_reload_rebuilds_scene_targets_for_the_active_scene`
- `test_invalid_reload_raises_and_keeps_the_running_config`
- `test_reload_emits_no_select_state_change`
- `test_reload_reruns_external_entity_validation`
- `test_reload_refuses_area_set_change` (task 12 replaces it)

**CHANGELOG:** Changed, `area_lighting.reload` now applies lights, groups,
sensors, remotes, routes, `scene_self_heal` and the `circadian:` block without
a restart, and reports a failed reload as an error.

**Commit:** `(Minor) area_lighting: apply a reloaded config to running areas`

**Verify:** the gate.

---

## Task 12: Reload adds and removes areas and scenes

**Files:** `__init__.py`, `controller.py`, `tests/integration/test_reload.py`.

**Behavior** (spec 6.2 steps 4, 6, 10 and 12, and the table in 6.2):

- Remove the refusal from task 11.
- Removed or disabled areas:
  - `ctrl.async_close(persist=True)` saves `state_dict()` with
    `timer_deadlines` emptied, then calls `shutdown()`,
  - every entity recorded for the area gets `await entity.async_remove()`.
    Registry entries stay,
  - the controller leaves `controllers`, and the diagnostics sensor resyncs
    its listeners.
- Added areas:
  - build the controller and `load_persisted_state(storage.get_area_state(id))`,
  - add its scene and helper entities through the startup helpers, refactored
    to take a list of controllers or areas,
  - assign HA areas, then run `restore_timers()`,
    `reconcile_startup_state()` and `async_resume_circadian()`.
- In kept areas, scene slugs that appear get an `AreaLightingScene` entity,
  slugs that disappear have theirs removed, and the select's options refresh.
- A changed area id is one removal plus one addition.

**Tests** (`tests/integration/test_reload.py`):

- delete `test_reload_refuses_area_set_change`
- `test_reload_adds_an_area_with_its_entities`
- `test_reload_removes_an_area_and_keeps_registry_entries`
- `test_reload_treats_a_changed_area_id_as_remove_and_add`
- `test_reload_adds_and_removes_scene_entities`
- `test_removed_area_controller_is_closed` (a late timer fires nothing, and
  nothing saves state)
- `test_removed_area_state_is_saved_without_timer_deadlines`
- `test_readded_area_restores_persisted_state`
- `test_reload_disabling_an_area_removes_it`

**CHANGELOG:** extend task 11's bullet: areas and scenes can be added, removed
or renamed with a reload.

**Commit:** `(Minor) area_lighting: add and remove areas and scenes on reload`

**Verify:** the gate.

---

## Task 13: Service descriptions, Repairs text and translations

**Files:** `services.yaml`, `strings.json`, `translations/en.json`,
`tests/test_translations.py`.

**Behavior:**

- `lighting_circadian` (`services.yaml:85-87`, `translations/en.json:78-80`):
  "Activates circadian lighting for an area. area_lighting sets each grouped
  light's brightness and color temperature from the sun and keeps them
  updated."
- `reload` (`services.yaml:135-137`, `translations/en.json:116` and on):
  "Re-reads the area_lighting YAML and applies it to running areas, adding and
  removing areas and scenes. Area state and timers are kept."
- `missing_external_entities` in `strings.json:6` and `translations/en.json:6`:
  drop "circadian sensor readings" from the list of degraded features. The two
  files stay identical in that issue.

**Tests:** `tests/test_translations.py::test_user_text_does_not_mention_circadian_lighting`
checks `services.yaml`, `strings.json` and `translations/en.json`.

**CHANGELOG:** none. The task 8 and 11 bullets cover it.

**Commit:** `(Patch) area_lighting: describe built-in circadian and full reload in services and repairs`

**Verify:** the gate.

---

## Task 14: README, CONFIGURATION, CHANGELOG and test docs

**Files:** `README.md`, `CONFIGURATION.md`, `CHANGELOG.md`,
`custom_components/area_lighting/tests/README.md`.

**Behavior:**

- `README.md`:
  - Design goal 2 (line 68): the circadian scene drives the lights from the
    sun itself.
  - "Manual light changes" (lines 574-594): state the circadian skip and its
    shed exception, and point at spec section 7.
  - All-lights-off notes (line 653): "circadian adjustment stops".
  - New "Circadian lighting" section: the curve, groups, types, sleep,
    `interval` and `transition`, routes, demand response, and the sensor.
  - "Required external entities" (lines 983-1041): remove the
    `sensor.circadian_values` row, the per-area switch row and the
    `circadian_lighting` integration row. Add `sensor.area_lighting_circadian`
    to the entities the integration creates.
  - New "Reload" paragraph.
  - Demand-response text that mentions circadian switches (around lines
    950-973).
- `CONFIGURATION.md`:
  - The top-level table (line 30) gains `circadian`, and the `scene_self_heal`
    note now says reload re-reads it.
  - The `circadian_switches` section (lines 89-110) becomes `circadian_groups`
    with sleep options and defaults.
  - Light keys (lines 123-139) use `circadian_group`.
  - "Circadian kelvin routes" (lines 158-245) loses `source`, gains the
    route-light group rule, and replaces the reload paragraph at 242-244.
  - The examples at 498-507 and the reload line at 603.
- `CHANGELOG.md`: merge the bullets from tasks 3-12 into one 3.0.0 story under
  `## Unreleased`, BREAKING items first. Add a "Migrating from 2.x" list:
  - rename the two keys,
  - delete `source`,
  - add the `circadian:` block with the old `circadian_lighting:` values,
  - give groups the bounds the switch file had where `area_lighting.yaml` has
    none,
  - move `sleep_*` options onto the group,
  - point automations at `sensor.area_lighting_circadian`,
  - remove the `circadian_lighting:` block, its `switch:` entries and the
    integration, in the same restart as the upgrade.
- `tests/README.md`: the `pin_circadian` fixture, and the teardown that the
  autouse fixture now runs.

**Tests:** none.

**Commit:** `(Patch) docs: document built-in circadian lighting, groups and reload`

**Verify:** the gate, then from the worktree root
`grep -n "circadian_switch\|circadian_lighting\|circadian_values" README.md CONFIGURATION.md`.
Matches may remain only where the text explains the migration.

---

## Task 15: haconf migration (separate repository, deploy gated on approval)

Runs after task 14 is merged and 3.0.0 is published (`tag:auto`, then
`release:github`). Spec section 9 holds the reasons. Commit subjects follow
haconf's style (plain imperative, no prefix).

**Worktree.** Read `haconf:CLAUDE.md` first. From any directory:

```sh
git -C /home/aaron/git/aaron/haconf fetch --all --prune
git -C /home/aaron/git/aaron/haconf worktree add \
    /home/aaron/git/aaron/haconf/.claude/worktrees/internalize-circadian \
    -b internalize-circadian origin/main
```

**Changes** (spec 9.1), each checked against the files at branch time:

1. `area_lighting.yaml`:
   - add `circadian: {min_colortemp: 2700, max_colortemp: 5600, interval: 60}`
     next to `areas:`,
   - rename the 22 `circadian_switches:` keys to `circadian_groups:` and the
     132 `circadian_switch:` keys to `circadian_group:`,
   - delete the kitchen `source:` line,
   - give Stairs Main bounds 10/55 and the sleep options with
     `sleep_colortemp: 2700`,
   - add the `coat_closet` area (spec 9.3). Confirm the HA area for
     `area_id: coat_closet` is named "Coat Closet", since `area_lighting`
     matches HA areas by name.
   - Leave all other bounds as they are.
2. `light.main_bedroom_tv_backlight`: read its state through the HA REST API
   (`haconf:CLAUDE.md`, "HA REST API"). If it does not exist, drop it with the
   switch file. If it exists, add the question to the approval message and do
   not add the light until the user answers.
3. `automations/_global/lighting_circadian_daylight_on.yaml` and `_off.yaml`:
   change `sensor.circadian_values` to `sensor.area_lighting_circadian`. In
   the description of `automations/office/clock_foreground_behavioral_scenes.yaml`
   (line 6), change `circadian_switch` to `circadian_group`. In
   `DEVELOPMENT_PLAN.md:91`, change `sensor.circadian_values` to
   `sensor.area_lighting_circadian`.
4. New `automations/coat_closet/circadian_follows_light_bars.yaml`. It calls
   `area_lighting.lighting_circadian` with `area_id: coat_closet` when
   `light.coat_closet_light_bars` goes from `off` to `on`, and
   `area_lighting.lighting_off` when it goes to `off`. Add `initial_state:
   false` to UI automations `1648338608804` and `1648338658736` in
   `automations.yaml`. Leave the four other `automations/coat_closet/` files
   alone.
5. `configuration.yaml`: delete the `circadian_lighting:` block and the
   `switch dirs:` line, and update the commented recorder entry.
6. Delete `switches/area_lighting.yaml` and `switches/circadian.yaml`.
7. `custom_components/circadian_lighting/`. Check HACS ownership read-only,
   with the test `hacs-commit` uses (`haconf:tools/hacs-commit/hacs-commit:83-88`).
   A catalog entry that is not installed does not count:

   ```sh
   ssh root@ha.unmd.xyz cat /config/.storage/hacs.repositories \
     | jq '[.data[] | select(.category == "integration"
                               and .domain == "circadian_lighting"
                               and .installed == true)] | length'
   ```

   - If it prints 0, `git rm -r` the directory in the branch.
   - Otherwise leave it for the cutover: it is uninstalled through HACS, and
     its deletion is committed by hand afterwards.
8. `CLAUDE.md`:
   - rewrite the sections listed in spec 9.1,
   - move any `area_lighting` line citations to 3.0.0 (as commit `872e2458`
     did for 2.0.1),
   - add a `CHANGELOG.md` entry with the bounds table.

**Validate before asking.** This runs 3.0.0's schema and validators against the
branch's file, from a throwaway checkout of the release tag (the
`area_lighting` worktree may already be gone after its merge):

```sh
git -C /home/aaron/git/aaron/home-assistant-area-lighting fetch --tags origin
git -C /home/aaron/git/aaron/home-assistant-area-lighting worktree add --detach \
    /tmp/area-lighting-v3.0.0 v3.0.0
cd /tmp/area-lighting-v3.0.0
uv run python -I -c '
import sys
sys.path.insert(0, ".")
from homeassistant.util.yaml import load_yaml
from custom_components.area_lighting import CONFIG_SCHEMA
from custom_components.area_lighting.config_schema import (
    parse_config, validate_circadian_groups,
    validate_circadian_kelvin_routes, validate_leader_follower_graph,
)
conf = CONFIG_SCHEMA({"area_lighting": load_yaml(sys.argv[1])})["area_lighting"]
cfg = parse_config(conf)
validate_leader_follower_graph(cfg)
validate_circadian_kelvin_routes(cfg)
validate_circadian_groups(cfg)
print(len(cfg.enabled_areas), "areas valid")
' /home/aaron/git/aaron/haconf/.claude/worktrees/internalize-circadian/area_lighting.yaml
```

Expected: `27 areas valid` (26 today plus `coat_closet`). Then remove the
throwaway checkout with
`git -C /home/aaron/git/aaron/home-assistant-area-lighting worktree remove /tmp/area-lighting-v3.0.0`.

Also check that no file in the branch still names `circadian_switch`,
`switch.circadian_lighting_` or `sensor.circadian_values`, apart from
`CHANGELOG.md`, the retired `templater.yaml`, the dated records under
`docs/superpowers/`, and the HACS-managed `custom_components/`:

```sh
grep -rn "circadian_switch\|switch\.circadian_lighting_\|sensor\.circadian_values" \
    /home/aaron/git/aaron/haconf/.claude/worktrees/internalize-circadian \
    --include=*.yaml --include=*.md \
    | grep -v "CHANGELOG.md\|templater.yaml\|/docs/superpowers/\|/custom_components/"
```

The only expected matches are in `automations.yaml`, inside the two UI
automations retired with `initial_state: false` (`1648338608804` and
`1648338658736`, about six lines). Any other match is a missed reference.

**Approval gate.** Commit the branch and push it. Do not merge it, and do not
touch the host. Ping the user on Discord with the session label. The message
gives:

- the branch name,
- the bounds table from spec 9.1,
- the TV backlight question, if any,
- the HACS state of `circadian_lighting`,
- the cutover steps below, and a request for a time.

Stop here until the user approves.

**Cutover** (after approval only, spec 9.2):

1. Record `sensor.circadian_values` `colortemp` and the time, for the
   comparison afterwards.
2. In HACS, download `area_lighting` 3.0.0 without restarting. If HACS owns
   `circadian_lighting`, uninstall it there.
3. Wait for `hacs-commit` to push the `area_lighting` update (10 to 20
   minutes). Write down that commit's SHA and its parent, for rollback.
   Rebase `internalize-circadian` onto `origin/main`. If HACS owned
   `circadian_lighting`, commit its deletion. Merge to `main`, push, and write
   down the merge SHA.
4. On the host, keep the UI's uncommitted `automations.yaml` edits, as
   `haconf:CLAUDE.md` requires. First look:

   ```sh
   ssh root@ha.unmd.xyz git -C /config status --short
   ```

   If `automations.yaml` shows as modified, stash only that file:

   ```sh
   ssh root@ha.unmd.xyz git -C /config stash push -m internalize-circadian -- automations.yaml
   ```

   Then fast-forward and check:

   ```sh
   ssh root@ha.unmd.xyz 'git -C /config fetch origin && git -C /config merge --ff-only origin/main'
   ssh root@ha.unmd.xyz git -C /config stash pop   # only if the stash push above ran
   ssh root@ha.unmd.xyz ha core check
   ```

5. If the check passes, run `ssh root@ha.unmd.xyz ha core restart`, once. A
   restart drops the GreenEye feed for minutes and relights pending reminder
   buttons (`haconf:CLAUDE.md`, both restart caveats). That is why the
   approval message asks the user for a time.
6. Check each item in spec 9.2 step 7, and report what was seen.

**Rollback** (spec 9.2, which has the reasons):

1. In HACS, redownload `area_lighting` 2.0.1, plus `circadian_lighting` if it
   was uninstalled. Do not restart.
2. Wait for `hacs-commit` to push both, so `main` vendors 2.0.1 again.
3. Revert the migration merge and push. Where the revert and the
   `hacs-commit` commit both restore `custom_components/circadian_lighting/`,
   keep the `hacs-commit` files. Then check that `main` matches its state
   before the cutover's vendor commit:

   ```sh
   git -C /home/aaron/git/aaron/haconf fetch origin
   git -C /home/aaron/git/aaron/haconf diff <parent of the vendor commit> origin/main -- \
       custom_components/area_lighting custom_components/circadian_lighting \
       configuration.yaml switches area_lighting.yaml
   ```

   It prints nothing, unless a commit after the cutover changed those paths.
4. Fast-forward the host as in cutover step 4. Check the versions on disk:

   ```sh
   ssh root@ha.unmd.xyz cat /config/custom_components/area_lighting/manifest.json | jq -r .version
   ssh root@ha.unmd.xyz ls -d /config/custom_components/circadian_lighting
   ```

   They print `2.0.1` and the directory. Run `ssh root@ha.unmd.xyz ha core check`,
   and restart once only if it passes.
5. Turn off every `switch.circadian_lighting_*`, then call
   `area_lighting.lighting_circadian` for each area whose
   `select.<area>_last_scene` reads `circadian`.

**Commit:** `Move circadian lighting into area_lighting 3.0.0`. If HACS owned
`circadian_lighting`, the cutover adds `Commit the circadian_lighting removal
HACS made`.

**Verify:** the validation script passes, `ha core check` passes on the host,
and every check in spec 9.2 step 7 holds after the restart.
