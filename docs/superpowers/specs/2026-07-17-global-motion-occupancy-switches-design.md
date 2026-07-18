# Global motion & occupancy master switches — design

## Summary

Add two **integration-owned global switch entities** that act as master
kill-switches across every area:

| entity_id | friendly name | default | icon |
|---|---|---|---|
| `switch.area_lighting_motion_lights_enabled` | Area Lighting Motion Lights (Global) | **on** | `mdi:motion-sensor` |
| `switch.area_lighting_occupancy_timeout_enabled` | Area Lighting Occupancy Timeout (Global) | **on** | `mdi:timer-cog-outline` |

Each global switch is `AND`-ed with the matching per-area switch: a
behavior runs only when **global AND per-area** are both on. Both globals
default **on**, so out-of-the-box behavior is unchanged.

- **Motion → lights on** is suppressed globally when
  `switch.area_lighting_motion_lights_enabled` is off.
- **Occupancy timer → lights off** is suppressed globally when
  `switch.area_lighting_occupancy_timeout_enabled` is off (timers don't
  arm; any running timer is cancelled; a restored/past-due timer's expiry
  is a no-op).

The switches are owned and persisted by the integration (modeled on the
existing singleton `AreaLightingDiagnosticSensor`), so no YAML helpers are
required.

**Breaking change:** the pre-existing *external* helper
`input_boolean.motion_light_enabled` (`GLOBAL_MOTION_LIGHT_ENABLED_ENTITY`)
is **removed entirely** and replaced by the owned motion switch. See
[Migration](#migration).

## Motivation

Before this component, two global `input_boolean` helpers plus a per-automation
check globally suppressed (a) motion-triggered lights-on and (b) the
occupancy auto-off. Today only half of that survives, and awkwardly:

- Motion-on already consults an **external** helper
  `input_boolean.motion_light_enabled` (`const.py:98`, checked in
  `event_handlers.py:608`). It is not owned by the integration — the user
  must create it, and a *missing* helper silently force-disables all
  motion lighting (`event_handlers.py:608`: `if not global_state ...:
  return False`). The integration only nags the user to create it via a
  repairs issue.
- Occupancy auto-off has **no** global control at all — only the per-area
  `switch.<area>_occupancy_timeout_enabled` (gated in
  `_start_occupancy_timer`, `controller.py:1768-1780`).

Two owned global switches restore the original two-toggle model cleanly:
self-documenting entities, persisted like the per-area toggles, no
external helpers, and no missing-helper footgun.

## Behavior

### Gate semantics (both globals)

The global is a hard master gate, `AND`-ed with the per-area switch. It
never turns lights **off** on its own and never turns them **on** on its
own — it only gates the automatic behaviors:

- Global **off** → the behavior is suppressed in every area regardless of
  the per-area switch.
- Global **on** → each area follows its own per-area switch (today's
  behavior).

`is_on` for a global switch reflects only the global flag, independent of
any per-area switch.

### Motion → lights on

- Global motion switch **on** (default): unchanged. Per-area
  `motion_light_enabled`, ownership, and illuminance/motion-condition
  gates apply as today.
- Global motion switch **off**: `event_handlers._check_conditions()`
  returns `False` before any controller task is scheduled — no area turns
  lights on from motion. Lights already on are untouched (motion-on only
  ever *adds* light).
- Transitions have no cross-area side effect: the gate is consulted live
  on the next motion edge, so flipping the switch takes effect immediately
  with no fan-out work.

### Occupancy timer → lights off

- Global occupancy switch **on** (default): unchanged.
- Global occupancy switch **off**:
  - `_start_occupancy_timer()` is a no-op → no area arms a new occupancy
    timer (scene transitions, sensor-clear, `handle_occupancy_off`, etc.).
  - `_on_occupancy_timer()` (expiry) is a no-op → any already-armed timer
    that fires (e.g. a deadline restored at startup) does **not** turn
    lights off.
- **Transitions (fanned out across all controllers):**
  - On → Off: cancel every area's running occupancy timer immediately.
    Lights that were counting down to auto-off stay on. No lights-off
    callback fires.
  - Off → On: re-run each area's `_enforce_occupancy_timer()`, which
    re-arms the timer only where the area is in an on-scene with all
    occupancy sensors clear (it already encodes those preconditions). The
    per-area `occupancy_timeout_enabled` gate still applies, so an area
    whose per-area switch is off stays unarmed.

This mirrors the existing per-area `async_set_occupancy_timeout_enabled`
(`controller.py:498-514`), applied over every controller.

### Independence

The two globals are orthogonal: disabling motion globally does not affect
occupancy timers, and disabling occupancy timeout globally does not affect
motion-on.

## Implementation

### New module: `global_state.py`

A small holder owning the two flags, their persistence, and a listener
list for the switch entities. It reaches controllers lazily via
`hass.data[DOMAIN]["controllers"]`, so it holds no back-references.

```python
"""Global (all-area) master toggles for area_lighting."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .state_storage import StateStorage


class GlobalToggles:
    def __init__(self, hass: HomeAssistant, state_storage: StateStorage) -> None:
        self._hass = hass
        self._state_storage = state_storage
        self._motion_lights_enabled = True
        self._occupancy_timeout_enabled = True
        self._listeners: list = []

    @property
    def motion_lights_enabled(self) -> bool:
        return self._motion_lights_enabled

    @property
    def occupancy_timeout_enabled(self) -> bool:
        return self._occupancy_timeout_enabled

    # --- listener plumbing (mirrors controller.add_state_listener) ---
    def add_state_listener(self, cb) -> None:
        self._listeners.append(cb)

    def remove_state_listener(self, cb) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def _schedule_save(self) -> None:
        self._hass.async_create_task(
            self._state_storage.async_save_global_state(self.state_dict())
        )

    # --- persistence ---
    def state_dict(self) -> dict:
        return {
            "motion_lights_enabled": self._motion_lights_enabled,
            "occupancy_timeout_enabled": self._occupancy_timeout_enabled,
        }

    def load_persisted_state(self, data: dict) -> None:
        if not data:
            return
        if "motion_lights_enabled" in data:
            self._motion_lights_enabled = bool(data["motion_lights_enabled"])
        if "occupancy_timeout_enabled" in data:
            self._occupancy_timeout_enabled = bool(data["occupancy_timeout_enabled"])

    # --- setters (called by the switch entities) ---
    async def async_set_motion_lights_enabled(self, enabled: bool) -> None:
        if self._motion_lights_enabled == enabled:
            return
        self._motion_lights_enabled = enabled
        # No cross-controller side effect: _check_conditions reads the flag
        # live on the next motion edge.
        self._notify()
        self._schedule_save()

    async def async_set_occupancy_timeout_enabled(self, enabled: bool) -> None:
        if self._occupancy_timeout_enabled == enabled:
            return
        self._occupancy_timeout_enabled = enabled
        controllers = self._hass.data.get(DOMAIN, {}).get("controllers", {})
        for ctrl in controllers.values():
            if enabled:
                ctrl.enforce_occupancy_timer()   # re-arm where appropriate
            else:
                ctrl.cancel_occupancy_timer()     # stop pending auto-off
        self._notify()
        self._schedule_save()
```

Both setters are idempotent (early-return on no-change), matching the
per-area setter's ordering: mutate flag → apply side effect → notify +
save.

### `state_storage.py` — a reserved non-area key

`StateStorage._data` is keyed strictly by `area_id`. Add a reserved key
and two dedicated accessors (near lines 45-61):

```python
GLOBAL_STATE_KEY = "__global__"  # double underscore — cannot collide with an HA area slug

def get_global_state(self) -> dict[str, Any]:
    return self._data.get(GLOBAL_STATE_KEY, {})

async def async_save_global_state(self, state: dict[str, Any]) -> None:
    self._data[GLOBAL_STATE_KEY] = state
    await self.async_save()
```

HA area slugs never begin with `__`, so `__global__` cannot shadow a real
area's `get_area_state`. Storage version/key are unchanged (no migration).

### `switch.py` — a global switch class + def table

Add alongside `AreaLightingSwitch`:

```python
# (flag, name, icon, unique_id, entity_id)
GLOBAL_SWITCH_DEFS = [
    (
        "motion_lights_enabled",
        "Area Lighting Motion Lights (Global)",
        "mdi:motion-sensor",
        "area_lighting_global_motion_lights_enabled",
        "switch.area_lighting_motion_lights_enabled",
    ),
    (
        "occupancy_timeout_enabled",
        "Area Lighting Occupancy Timeout (Global)",
        "mdi:timer-cog-outline",
        "area_lighting_global_occupancy_timeout_enabled",
        "switch.area_lighting_occupancy_timeout_enabled",
    ),
]


class AreaLightingGlobalSwitch(SwitchEntity):
    """A global master switch backed by a GlobalToggles flag."""

    _attr_should_poll = False

    def __init__(self, toggles, flag, name, icon, unique_id, entity_id) -> None:
        self._toggles = toggles
        self._flag = flag
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = unique_id
        self.entity_id = entity_id

    @property
    def is_on(self) -> bool:
        return bool(getattr(self._toggles, self._flag))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, value: bool) -> None:
        if self._flag == "motion_lights_enabled":
            await self._toggles.async_set_motion_lights_enabled(value)
        else:
            await self._toggles.async_set_occupancy_timeout_enabled(value)

    async def async_added_to_hass(self) -> None:
        self._toggles.add_state_listener(self._on_change)

    async def async_will_remove_from_hass(self) -> None:
        self._toggles.remove_state_listener(self._on_change)

    @callback
    def _on_change(self) -> None:
        self.async_write_ha_state()
```

Naming note: the global motion switch entity id is
`switch.area_lighting_motion_lights_enabled` (plural "lights"),
deliberately distinct from the per-area `switch.<area>_motion_light_enabled`
(singular) so the two never collide and read differently in the UI.

### `controller.py` — global gate + public wrappers

**1. Occupancy timer-start gate.** In `_start_occupancy_timer()`
(`controller.py:1768-1780`), add the global check after the existing
per-area gate:

```python
def _start_occupancy_timer(self) -> None:
    if not self._occupancy_timeout_enabled:
        _LOGGER.debug("Area %s: occupancy timer start suppressed (timeout disabled)", self.area.id)
        return
    toggles = self.hass.data.get(DOMAIN, {}).get("global")
    if toggles is not None and not toggles.occupancy_timeout_enabled:
        _LOGGER.debug("Area %s: occupancy timer start suppressed (global timeout disabled)", self.area.id)
        return
    self._occupancy_timer.start(duration=self._occupancy_off_duration())
```

**2. Expiry no-op guard.** In `_on_occupancy_timer()`
(`controller.py:1935-1939`), short-circuit when globally disabled — this
covers timers armed before the flip and deadlines restored at startup
(which fire via `TimerHandle.restore()` → `_fire()`, bypassing
`_start_occupancy_timer`):

```python
async def _on_occupancy_timer(self) -> None:
    if self._state.is_off or self._state.is_ambient_like:
        return
    toggles = self.hass.data.get(DOMAIN, {}).get("global")
    if toggles is not None and not toggles.occupancy_timeout_enabled:
        return
    await self.lighting_off_fade(source=ActivationSource.OCCUPANCY)
```

**3. Public wrappers** for the fan-out (so `GlobalToggles` doesn't touch
privates), added near the other occupancy methods:

```python
def cancel_occupancy_timer(self) -> None:
    """Cancel any running occupancy timer without firing lights-off."""
    self._occupancy_timer.cancel()

def enforce_occupancy_timer(self) -> None:
    """Public wrapper: re-evaluate whether the occupancy timer should arm."""
    self._enforce_occupancy_timer()
```

Both gates use fail-open semantics (`toggles is None → enabled`) so
controllers built in isolation (unit tests, or before `hass.data` is
populated) keep today's behavior.

No controller **constructor** change is required — the gates read
`hass.data[DOMAIN]["global"]` lazily via `self.hass`, the same pattern the
controller already uses for `state_storage`/`scene_storage`
(`controller.py:376,838`). `DOMAIN` is already imported (`controller.py:24`).

### `event_handlers.py` — replace the external gate, remove the helper

**1. Motion gate.** In `_check_conditions()` (`event_handlers.py:605-609`),
replace the external-helper lookup with the owned global flag:

```python
def _check_conditions() -> bool:
    """Check global + area motion conditions."""
    toggles = hass.data.get(DOMAIN, {}).get("global")
    if toggles is not None and not toggles.motion_lights_enabled:
        return False
    # Area motion enabled
    if not ctrl.motion_light_enabled:
        return False
    ...
```

(`DOMAIN` is already imported in `event_handlers.py:20`.)

**2. Remove all external-helper references** (the "nuke"):
- `const.py:98` — delete `GLOBAL_MOTION_LIGHT_ENABLED_ENTITY`.
- `event_handlers.py:21` — remove the import.
- `event_handlers.py:127-130` — remove the `input_boolean.motion_light_enabled`
  bootstrap-suggestion block.
- `event_handlers.py:191-194` — remove the
  `("input_boolean.motion_light_enabled", "global motion lighting kill-switch ...")`
  entry from the `required` list in `async_validate_external_entities`.

### `__init__.py` — construct, persist, register

**Construct + load + expose** (near the `hass.data[DOMAIN]` setup,
`__init__.py:102-111`):

```python
state_storage = StateStorage(hass)
await state_storage.async_load()

global_toggles = GlobalToggles(hass, state_storage)
global_toggles.load_persisted_state(state_storage.get_global_state())

hass.data[DOMAIN] = {
    "config": area_config,
    "controllers": {},
    "unsubs": [],
    "scene_storage": scene_storage,
    "state_storage": state_storage,
    "global": global_toggles,
}
```

**Register the two switches** in `_register_helper_entities()`
(`__init__.py:291-365`). Append the globals to the `switches` list after
the per-controller loop, before `async_add_entities`:

```python
toggles = hass.data.get(DOMAIN, {}).get("global")
if toggles is not None:
    for flag, name, icon, uid, eid in GLOBAL_SWITCH_DEFS:
        switches.append(AreaLightingGlobalSwitch(toggles, flag, name, icon, uid, eid))
```

The global switches have no `_controller` attribute, so
`_assign_entities_to_ha_areas` (`__init__.py:368-410`) already skips them
(its loop `continue`s when `getattr(entity, "_controller", None)` is
`None`) — they stay area-independent, like the diagnostic sensor. Their
`is_on` reads the holder that was created before entity registration, so
persisted state is reflected on the first render.

Caveat: `_register_helper_entities` early-returns when there are **no**
controllers (`__init__.py:304-305`), so the globals only register on
installs with at least one enabled area. That is acceptable (with zero
areas the globals gate nothing). If we later want them present regardless,
move their registration into a dedicated `_register_global_switches(hass)`
called from `_on_started`, mirroring `_register_diagnostic_sensor`.

### `diagnostics.py` — surface global state (optional, low-cost)

Add a `=== global ===` block to `AreaLightingDiagnosticSensor._build_state_text()`
and a `"global"` key to `extra_state_attributes`, reading
`hass.data[DOMAIN]["global"].state_dict()`. Purely observability; skip if
it complicates the change.

## Testing

New file `tests/integration/test_global_master_switches.py` (uses the
existing `pytest-homeassistant-custom-component` fixtures — `hass`,
`helper_entities`, `network_room_config`, `service_calls`):

1. **Entities exist & default on.** After setup, both
   `switch.area_lighting_motion_lights_enabled` and
   `switch.area_lighting_occupancy_timeout_enabled` are registered and
   `on`.
2. **Global motion off blocks motion-on everywhere.** With the per-area
   `motion_light_enabled` on, turn the global motion switch off; fire a
   motion sensor; assert no `light.turn_on` call. Turn it back on; fire
   motion; assert lights come on.
3. **Global occupancy off prevents auto-off + cancels a pending timer.**
   Arm an occupancy timer (area lit, sensors clear), turn the global
   occupancy switch off; assert the controller's occupancy timer is
   inactive and no `light.turn_off` fired. Advancing time past the old
   deadline produces no auto-off.
4. **Expiry no-op guard.** With the global occupancy switch off, invoke
   `_on_occupancy_timer()` directly (simulating a restored/past-due timer)
   on a lit area; assert light state is unchanged.
5. **Global occupancy off → on re-arms.** Area lit + sensors clear + per-area
   switch on; toggle global occupancy off then on; assert the timer is
   active again. An area with its **per-area** switch off stays unarmed.
6. **Independence.** Global motion off does not cancel occupancy timers;
   global occupancy off does not block motion-on.
7. **Persistence.** Set both globals off, round-trip through
   `StateStorage` / a fresh `GlobalToggles.load_persisted_state`, assert
   both restore to off and gate behavior accordingly.

Existing-test updates (external-helper removal):

- `tests/integration/conftest.py:61-64` — remove the
  `motion_light_enabled` `input_boolean` fixture entry (dead after the
  nuke; the owned global switch defaults on, so motion tests still pass).
- `tests/integration/test_validation.py:58,112,180` — remove the setup
  entry and the two assertions that expect the validator to flag /
  bootstrap `input_boolean.motion_light_enabled`.
- `tests/integration/test_entity_naming.py` — add the two new global
  switch entity ids to the expected-entity assertions.

All other `motion_light_enabled` references in tests are the **per-area**
switch/property and are unaffected.

## Migration

This removes the external `input_boolean.motion_light_enabled` contract.
Document in `CHANGELOG.md` and `README.md`:

- The global motion kill-switch is now the owned entity
  `switch.area_lighting_motion_lights_enabled`; the old
  `input_boolean.motion_light_enabled` is **no longer consulted**.
  Automations/dashboards that toggled the old helper must target the new
  switch.
- **Behavior change on upgrade:** because the owned switch defaults **on**
  and the old helper is ignored, anyone who left
  `input_boolean.motion_light_enabled` *off* to suppress motion will find
  motion re-enabled after upgrade until they turn the new switch off.
- The old `input_boolean.motion_light_enabled` helper is now unused and
  can be deleted (the integration no longer creates a repairs issue for
  it).

Because this changes a user-facing external contract, the removal commit's
subject should use the `(Major)` prefix so `tag:auto` bumps accordingly.
(Do not hand-edit `pyproject.toml` / `manifest.json` / `uv.lock` versions;
`tag:auto` computes the bump.)

## Out of scope / rejected alternatives

- **Seeding the new motion switch from the old `input_boolean` at first
  startup.** Rejected per the explicit "nuke it entirely" decision. The
  clean cut is documented as a migration note instead.
- **Keeping the external helper for back-compat (honor-if-present).**
  Rejected for the same reason.
- **A single combined "automation mode" `select`.** Two independent
  booleans map directly to the two behaviors and the prior mental model.
- **Bulk-toggling the per-area switches from a global switch.** Rejected:
  it would clobber and lose per-area intent. The globals are a separate,
  orthogonal master gate.
- **Per-area override of the global.** The global is a hard gate; there is
  no per-area "ignore the global" escape hatch. Per-area switches only
  further *restrict*, never *widen*.
- **Exposing the globals in YAML config.** The switches (persisted) are the
  only affordance, consistent with the per-area toggles.
