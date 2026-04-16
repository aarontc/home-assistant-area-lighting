# Occupancy Timeout Enabled switch — design

## Summary

Add a per-area switch `switch.<area>_occupancy_timeout_enabled` (default
**on**) that gates the occupancy-off timer. When on, the existing timer
logic is unchanged. When off, the timer does not arm, and any running
timer is cancelled immediately without firing the lights-off callback.

## Motivation

Today the occupancy timer is always armed when the area is in an on-scene,
occupancy sensors read clear, and a configured `occupancy_off_duration` is
set. There's no way to temporarily suppress the timeout for a given area
without editing configuration or turning off all occupancy-aware behavior.
A user-facing switch makes this a one-click override — useful for rooms in
extended use (e.g., a home office meeting), cleaning, or manually holding a
scene.

## Behavior

**Steady-state:**

- Switch on (default): existing occupancy-timer logic is applied unchanged.
- Switch off: `_start_occupancy_timer()` is a no-op — events that would
  normally arm the timer (scene activation, manual change, sensor-clear)
  skip the start.

**Switch transitions:**

- On → Off: cancel any running occupancy timer immediately. The
  lights-off callback does **not** fire.
- Off → On: if the area is currently in an on-scene and all occupancy
  sensors read clear, arm the timer now (we call `_enforce_occupancy_timer()`,
  which already encodes those preconditions). Otherwise no-op.

All existing cancel sites (motion-off setter, off/ambient branch of
`_enforce_occupancy_timer`, `handle_occupancy_on`,
`handle_occupancy_lights_off`) keep their current semantics — the gate
only affects *starts*, not cancels.

## Implementation

### Controller state

In `custom_components/area_lighting/controller.py`, add one boolean on
`AreaLightingController` alongside the existing gate flags (near lines
62–65):

```python
self._occupancy_timeout_enabled: bool = True
```

Expose a `@property` / `@setter` pair next to the others (around lines
410–455). The setter just assigns; toggle side effects live in the async
method below because they need to schedule work.

### The gate

Introduce one private helper that owns the single timer-start path:

```python
def _start_occupancy_timer(self) -> None:
    """Arm the occupancy timer, respecting the enable flag."""
    if not self._occupancy_timeout_enabled:
        _LOGGER.debug(
            "Area %s: occupancy timer start suppressed (timeout disabled)",
            self.area.id,
        )
        return
    self._occupancy_timer.start(duration=self._occupancy_off_duration())
```

Replace the two existing `self._occupancy_timer.start(...)` calls with
`self._start_occupancy_timer()`:

- `_enforce_occupancy_timer()` (line 1433)
- `handle_occupancy_off()` (line 1449)

All other call sites for the occupancy timer — including cancels and the
`timer.restore(deadline_utc)` path used during state restoration — are
untouched.

### Switch-triggered transitions

Add an async setter on the controller for the UI to call:

```python
async def async_set_occupancy_timeout_enabled(self, enabled: bool) -> None:
    if self._occupancy_timeout_enabled == enabled:
        return
    self._occupancy_timeout_enabled = enabled
    if enabled:
        # Off → On: re-arm if we're currently occupied and sensors are clear.
        # _enforce_occupancy_timer already encodes those preconditions.
        self._enforce_occupancy_timer()
    else:
        # On → Off: cancel any running timer without firing its callback.
        self._occupancy_timer.cancel()
    self._notify_state_change()  # also schedules the persistence save
```

Idempotent (early-return on no-change). Ordering: update the flag, then
apply the timer side effect, then `_notify_state_change()` — which fans
out to UI listeners and schedules a state-dict save via `_schedule_save`.

### Switch entity

In `custom_components/area_lighting/switch.py`, add one tuple to
`SWITCH_DEFS`:

```python
("occupancy_timeout_enabled", "Occupancy Timeout Enabled", "mdi:timer-cog-outline", True),
```

Add the `occupancy_timeout_enabled` case to the `async_turn_on` /
`async_turn_off` dispatch (matching how `ambience_enabled` routes to an
async controller method today):

```python
if self._attr_key == "occupancy_timeout_enabled":
    await self._controller.async_set_occupancy_timeout_enabled(True)  # or False
```

The generic path already handles the plain boolean read via `is_on`.
Registered entity id: `switch.<area>_occupancy_timeout_enabled`.

### Persistence

In `state_dict()` and `load_persisted_state()` (controller.py ~146–199),
add `occupancy_timeout_enabled` alongside the other booleans. Default to
`True` when absent so existing installs start with the feature on.

Timer restoration uses `timer.restore(deadline_utc)` and is deliberately
unaffected by the gate — if HA shut down with an active deadline, it
resumes on startup. The gate only intervenes on future *starts*
(`_start_occupancy_timer()`), so a stale deadline from a previous session
is not suppressed by the current switch state.

### Diagnostics

Extend `AreaLightingController.diagnostic_snapshot()` (controller.py ~288)
to include `occupancy_timeout_enabled` next to the existing switch
booleans. Single-line addition. `diagnostics.py` iterates the snapshot
dict, so no change there.

## Testing

New file `custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py`:

1. **Default is on.** Fresh controller has the switch on; a normal
   occupancy-clear event arms the timer.
2. **Switch off suppresses start.** Toggle off, drive the area to
   "occupancy cleared"; assert `controller._occupancy_timer.is_active` is
   `False`.
3. **Switch off mid-countdown cancels without firing lights-off.** Arm the
   timer, then toggle off. Assert timer inactive AND light state unchanged
   (i.e., `_on_occupancy_timer` did not execute).
4. **Switch off → on with area still occupied re-arms.** Put area in
   "occupied, sensors clear" state with switch off, toggle on, assert timer
   is now active.

Extensions to existing files:

- `tests/integration/test_persistence.py` — round-trip `occupancy_timeout_enabled=False`
  and verify it's honored after restore (subsequent occupancy-clear does not
  arm).
- `tests/integration/test_entity_naming.py` — assert the new entity id is
  registered for each configured area.

All tests use the existing `pytest-homeassistant-custom-component`
framework; no new dependencies.

## Out of scope

- Exposing the flag in YAML config. The switch is the only affordance.
  Users who want a scripted default can script the switch from automations.
- Granular sub-gates (e.g., "block starts but keep running timer ticking").
  Behavior on toggle-off is a hard cancel, matching the user's spec.
- Any change to the motion-off timer. This flag only affects the occupancy
  timer.
