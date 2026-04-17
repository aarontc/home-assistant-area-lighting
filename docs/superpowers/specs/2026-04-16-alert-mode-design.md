# Alert mode — design

## Summary

Add a named-pattern alert system that flashes lights in one or all areas
for a short time, then restores their previous state. Patterns are defined
globally in the `area_lighting` YAML config and triggered via a new
`area_lighting.alert` service. The system is orthogonal to the scene
state machine — alerts don't cause scene transitions, but they coordinate
with the controller to suppress manual detection and pause timers for the
duration of the flash.

## Motivation

The user needs attention-grabbing light effects (doorbell ring, alarm,
notification) that work across areas with mixed light capabilities
(color Hue bulbs, white-only Hue bulbs, Lutron dimmers). Today there's
no built-in way to do this without ad hoc automations that risk fighting
with area_lighting's state machine.

## Config schema

Patterns are defined at the top level of the `area_lighting` config,
under `alert_patterns`. The schema is designed so the same shape can
be nested under an individual area in a future extension.

```yaml
area_lighting:
  alert_patterns:
    blue_alert:
      steps:
        - target: color
          state: "on"
          brightness: 255
          rgb_color: [0, 0, 255]
        - target: white
          state: "off"
      delay: 3.0
      restore: true

    three_flashes:
      steps:
        - target: all
          state: "on"
          brightness: 255
          delay: 1.0
        - target: all
          state: "off"
          delay: 1.0
      repeat: 3
      start_inverted: true
      restore: true
```

### Pattern fields

| Field            | Type         | Default | Description |
| ---------------- | ------------ | ------- | ----------- |
| `steps`          | list         | required | Ordered list of `AlertStep`s executed per cycle. |
| `delay`          | float (sec)  | `0`     | Pattern-level delay: hold after all steps complete (for non-repeating patterns like `blue_alert`). |
| `repeat`         | int          | `1`     | Number of times to execute the full step sequence. |
| `start_inverted` | bool         | `false` | If true, reverse step order on the first cycle when the majority of targeted lights are already in the first step's state. Ensures the flash always starts with a visible contrast. |
| `restore`        | bool         | `true`  | Capture all light states before the alert and restore after. |

### Step fields

| Field              | Type         | Default    | Description |
| ------------------ | ------------ | ---------- | ----------- |
| `target`           | string       | required   | Which lights: `all`, `color`, or `white`. Resolved at runtime via HA's `supported_color_modes` attribute. |
| `state`            | string       | required   | `on` or `off`. |
| `delay`            | float (sec)  | `0`        | Hold after this step before the next step executes. |
| `brightness`       | int (0–255)  | —          | Optional; passed to `light.turn_on`. |
| `rgb_color`        | [R, G, B]    | —          | Optional; requires color-capable target. |
| `color_temp_kelvin`| int          | —          | Optional. |
| `hs_color`         | [H, S]       | —          | Optional. |
| `xy_color`         | [X, Y]       | —          | Optional. |
| `transition`       | float (sec)  | —          | Optional; HA transition for the light call. |

### Target resolution

At execution time, each step's `target` is resolved against the area's
full light list using HA's state machine:

- `all` — every light entity in the area.
- `color` — lights whose `supported_color_modes` attribute includes any
  of `ColorMode.HS`, `ColorMode.RGB`, `ColorMode.RGBW`, `ColorMode.RGBWW`,
  or `ColorMode.XY`.
- `white` — lights that are NOT color-capable (complement of `color`).

A light whose state is `unavailable` is skipped silently.

## Service

New service `area_lighting.alert`:

```yaml
alert:
  description: Flash lights in an area using a named alert pattern.
  fields:
    area_id:
      description: Area ID, or "all" for every area.
      required: true
      selector:
        text:
    pattern:
      description: Name of the alert pattern defined in alert_patterns.
      required: true
      selector:
        text:
```

**Service handler logic (in `services.py`):**

1. Look up `pattern` in the parsed global config. Raise
   `ServiceValidationError` if unknown.
2. If `area_id == "all"`, dispatch `execute_alert` to every controller
   concurrently via `asyncio.gather`.
3. Otherwise, look up the single controller by `area_id`. Raise if
   not found.

## Alert module (`alert.py`)

Single public function:

```python
async def execute_alert(
    hass: HomeAssistant,
    controller: AreaLightingController,
    pattern: AlertPattern,
) -> None:
```

### Execution flow

```
1. Set controller._alert_active = True
2. Capture light states (all lights in area)
3. Snapshot timer deadlines + cancel all timers
4. Execute steps × repeat
   ├─ For each cycle:
   │   ├─ (first cycle only, if start_inverted) check majority state,
   │   │   reverse step order if needed
   │   └─ For each step:
   │       ├─ Filter lights by target (all/color/white)
   │       ├─ Issue light.turn_on or light.turn_off
   │       └─ asyncio.sleep(step.delay)
   └─ After last cycle: asyncio.sleep(pattern.delay)
5. Restore light states (if pattern.restore)
6. Restore timer deadlines (fires immediately if past-due)
7. Set controller._alert_active = False
```

Steps 1–3 and 5–7 are wrapped in `try/finally` so the guard and timers
are always restored even if the alert is interrupted (e.g., HA shutdown).

### Light state capture

For each light entity in the area, read:
- `state` (`on` / `off`)
- `brightness`
- `color_mode`
- `color_temp_kelvin` (if in color_temp mode)
- `rgb_color` / `hs_color` / `xy_color` (if in color mode)

Restore replays these attributes via `light.turn_on` (or `light.turn_off`
if the light was off). Use `transition: 0` on restore calls so lights
snap back instantly.

### Start-inverted logic

When `start_inverted` is true and `repeat > 1`:

1. Look at the first step's `target` and `state`.
2. Check the majority state of those targeted lights (are more on or off?).
3. If strictly more than half the targeted lights already match the first
   step's state (e.g., 3 of 4 lights are on and the first step is
   `state: on`), reverse the step list for the first cycle. On a 50/50
   tie, do not invert — play steps as written. Subsequent cycles always
   use normal order.

This ensures a strobe pattern always starts with a visible change.

### Empty-target handling

If a step's `target` resolves to zero lights (e.g., `target: color` in
an area with no color-capable bulbs), the step is a no-op — no service
call is issued, the step's `delay` still runs.

## Controller integration

### Flag

```python
self._alert_active: bool = False
```

Declared in `__init__`. Not persisted. Exposed in `diagnostic_snapshot()`
for debugging.

### Manual-detection suppression

At the top of the manual-detection code path (where incoming light
state-change events are compared against scene targets), add:

```python
if self._alert_active:
    return
```

This prevents the controller from interpreting alert-driven light
changes as manual user adjustments.

### Timer pause/resume

The alert module manages timers directly on the controller:

**On alert start:**
- Read `deadline_utc` from each of the three timers (`_motion_timer`,
  `_motion_night_timer`, `_occupancy_timer`). Store non-None deadlines.
- Call `cancel()` on all three.

**On alert end (in `finally`):**
- For each timer that had a saved deadline, call
  `timer.restore(saved_deadline)`. If the deadline is now past-due,
  `restore()` fires the callback immediately — the timer "would have
  fired" during the alert, so it fires now against the restored light
  state.

### What the guard does NOT do

- **Does not block scene transitions.** If a user presses a button during
  an alert, the scene transition fires normally. The alert's restore will
  be overwritten by the scene activation. User intent wins.
- **Does not interact with leader/follower propagation.**

## Data model

New dataclasses in `models.py`:

```python
@dataclass
class AlertStep:
    target: str          # "all", "color", "white"
    state: str           # "on", "off"
    delay: float = 0.0
    brightness: int | None = None
    rgb_color: tuple[int, int, int] | None = None
    color_temp_kelvin: int | None = None
    hs_color: tuple[float, float] | None = None
    xy_color: tuple[float, float] | None = None
    transition: float | None = None

@dataclass
class AlertPattern:
    steps: list[AlertStep]
    delay: float = 0.0
    repeat: int = 1
    start_inverted: bool = False
    restore: bool = True
```

## Parsing

In `config_schema.py`, add an optional `alert_patterns` key to the
top-level schema. Voluptuous schema validates types and enums. Parsed
`AlertPattern` objects are stored in `hass.data[DOMAIN]["alert_patterns"]`
(a `dict[str, AlertPattern]`).

## Testing

### Unit tests (`tests/test_alert.py`)

- Light state capture and restore (mock `hass.states.get` and service
  calls).
- Step execution order and timing (mock `asyncio.sleep`, verify call
  sequence).
- Target filtering: mock lights with various `supported_color_modes`,
  verify `all`/`color`/`white` filter correctly.
- `start_inverted` logic: verify step order reversal based on majority
  light state.
- `repeat` produces the correct number of cycles.
- Timer pause/resume: verify deadlines are captured, timers cancelled,
  then restored via `timer.restore(deadline)`.

### Integration tests (`tests/integration/test_alert.py`)

- End-to-end: trigger the `area_lighting.alert` service, verify lights
  change, verify restore.
- Manual detection suppressed during alert (`_alert_active` flag set).
- Timer deadline preserved across alert (deadline before == deadline
  after, unless past-due in which case callback fires).
- `area_id: "all"` dispatches to multiple areas concurrently.
- Unknown pattern name raises `ServiceValidationError`.

### Config parsing tests

- Valid pattern parses correctly into `AlertPattern` / `AlertStep`.
- Missing required fields (`steps`, `target`, `state`) rejected.
- Invalid `target` or `state` values rejected.
- Default values applied correctly (`repeat: 1`, `restore: true`,
  `delay: 0`, `start_inverted: false`).

## Out of scope

- Per-area pattern overrides. The schema is reusable at the area level
  but only the global `alert_patterns` dict is wired up in this version.
- "Party mode" / continuous color cycling. Separate feature (listed
  independently in TODO.md).
- Alert priority / queuing. If two alerts are triggered simultaneously,
  they race. A future version could add a lock or queue.
- Sound or notification integration. Alerts are light-only.
