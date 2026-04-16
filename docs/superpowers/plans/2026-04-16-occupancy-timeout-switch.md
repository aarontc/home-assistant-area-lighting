# Occupancy Timeout Enabled switch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-area `switch.<area>_occupancy_timeout_enabled` (default on) that gates the occupancy-off timer — when off, starts are suppressed and any running timer is cancelled without firing lights-off.

**Architecture:** A single boolean `_occupancy_timeout_enabled` on `AreaLightingController` with matching property/setter/async-setter. A private `_start_occupancy_timer()` helper becomes the single choke point for timer starts, replacing the two existing `self._occupancy_timer.start(...)` call sites. The switch entity is a new tuple in `switch.SWITCH_DEFS` plus a branch in the platform's `async_turn_on`/`async_turn_off` dispatch. Persistence piggybacks on the existing `state_dict` / `load_persisted_state` flow — `_notify_state_change()` already triggers `_schedule_save`.

**Tech Stack:** Python 3.13, Home Assistant custom component, pytest + `pytest-homeassistant-custom-component`. Run from repo root; tests are under `custom_components/area_lighting/tests/`.

**Spec:** `docs/superpowers/specs/2026-04-16-occupancy-timeout-switch-design.md`

---

## File structure

Files this plan touches:

| File | Responsibility | Change |
| --- | --- | --- |
| `custom_components/area_lighting/controller.py` | Controller state, gate helper, async setter, persistence, diagnostics | Modify |
| `custom_components/area_lighting/switch.py` | Switch entity registration + dispatch | Modify |
| `custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py` | Behavior tests for the new switch | Create |
| `custom_components/area_lighting/tests/integration/test_persistence.py` | Round-trip test for the new persisted key | Modify |
| `custom_components/area_lighting/tests/integration/test_entity_naming.py` | Entity id registration check (no code change — relies on generic switch test that iterates all area_lighting switches) | Verify only |

No changes to YAML config schema, translations, services, or diagnostics.py (the payload dict is built from `controller.diagnostic_snapshot()`).

---

## Notes for the implementer

- **Run the tests before you start.** From repo root: `./venv-or-dagger-python -m pytest custom_components/area_lighting/tests/ -x -q`. If you don't have a local venv, the haconf repo's venv works: `/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest custom_components/area_lighting/tests/ -x -q`. All 350 tests should pass on `main`.
- **Commit-message format.** This repo enforces `(Major|Minor|Patch) <subject>` via the `commit-msg` hook (if `git config core.hooksPath hooks` is set). All commits in this plan use `(Minor)` — adding a new feature.
- **TDD order.** Every task writes the failing test before the production code.
- **Line-number drift.** Line numbers cited below are from `main` as of this plan. If the file has drifted, use the surrounding string context instead.

---

## Task 1: Add the `_occupancy_timeout_enabled` attribute and property/setter

**Goal:** Introduce the new boolean on the controller with a property/setter pair mirroring the other gate flags. No behavior change yet — just adds the state.

**Files:**
- Modify: `custom_components/area_lighting/controller.py` (attr init around line 65; property near line 449)
- Test: `custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py` (create)

- [ ] **Step 1: Create the new test file with the default-on test**

Create `custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py`:

```python
"""Tests for the Occupancy Timeout Enabled per-area switch.

The switch defaults to on and gates the occupancy-off timer:
  - On: existing enforcement logic applies unchanged.
  - Off: _start_occupancy_timer() is a no-op.
  - On→Off transitions cancel a running timer without firing lights-off.
  - Off→On transitions re-arm via _enforce_occupancy_timer.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _config_with_occupancy() -> dict:
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "media_room",
                    "name": "Media Room",
                    "event_handlers": True,
                    "lights": [
                        {"id": "light.media_room_overhead", "roles": ["dimming"]},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "ambient", "name": "Ambient"},
                    ],
                    "occupancy_light_sensor_ids": [
                        "binary_sensor.media_room_presence",
                    ],
                    "occupancy_light_timer_durations": {
                        "off": "00:30:00",
                    },
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.media_room_presence",
                    ],
                    "motion_light_timer_durations": {
                        "off": "00:08:00",
                    },
                }
            ]
        }
    }


@pytest.mark.integration
async def test_defaults_to_enabled(hass: HomeAssistant, helper_entities) -> None:
    """Fresh controller has occupancy_timeout_enabled == True."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    assert ctrl.occupancy_timeout_enabled is True
```

- [ ] **Step 2: Run the test to verify it fails**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_defaults_to_enabled \
  -v
```

Expected: FAIL with `AttributeError: 'AreaLightingController' object has no attribute 'occupancy_timeout_enabled'`.

- [ ] **Step 3: Add the attribute to `__init__`**

In `controller.py`, find the block at lines ~62–65:

```python
        # User-toggle state (orthogonal to lighting state machine)
        self._motion_light_enabled: bool = False
        self._ambience_enabled: bool = True
        self._night_mode: bool = False
        self._motion_override_ambient: bool = True
```

Add one line after `self._motion_override_ambient`:

```python
        self._occupancy_timeout_enabled: bool = True
```

- [ ] **Step 4: Add the property/setter near the other gate-flag properties**

In `controller.py`, find the `motion_override_ambient` property/setter at lines ~449–456:

```python
    @property
    def motion_override_ambient(self) -> bool:
        return self._motion_override_ambient

    @motion_override_ambient.setter
    def motion_override_ambient(self, value: bool) -> None:
        self._motion_override_ambient = value
        self._notify_state_change()
```

Immediately after the setter, add:

```python
    @property
    def occupancy_timeout_enabled(self) -> bool:
        return self._occupancy_timeout_enabled
```

(No plain setter — the only way to mutate this flag is through the async setter added in Task 3, which owns the timer side effects.)

- [ ] **Step 5: Run the test to verify it passes**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_defaults_to_enabled \
  -v
```

Expected: PASS.

- [ ] **Step 6: Run the full test suite to confirm no regressions**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/ -x -q
```

Expected: 351 passed (350 existing + 1 new).

- [ ] **Step 7: Commit**

```bash
git add custom_components/area_lighting/controller.py \
        custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py
git commit -m "(Minor) area_lighting: add occupancy_timeout_enabled flag (default on)

Introduces the per-area boolean that will gate the occupancy-off timer.
No behavior change yet — later tasks add the gate helper, async setter,
switch entity, and persistence."
```

---

## Task 2: Introduce the `_start_occupancy_timer()` gate helper

**Goal:** Replace the two existing `self._occupancy_timer.start(...)` call sites with a single helper that owns the enable-flag gate. With the flag on (default from Task 1), all behavior is unchanged.

**Files:**
- Modify: `custom_components/area_lighting/controller.py` (new helper below `_enforce_occupancy_timer`; two call-site swaps at lines ~1433 and ~1449)
- Test: `custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py`

- [ ] **Step 1: Write the failing test — switch off suppresses start**

Append to `test_occupancy_timeout_switch.py`:

```python
@pytest.mark.integration
async def test_start_suppressed_when_disabled(
    hass: HomeAssistant, helper_entities
) -> None:
    """With the flag off, activating a scene does not arm the occupancy timer."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    # Directly mutate the flag; the public async setter is added in Task 3.
    ctrl._occupancy_timeout_enabled = False

    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_handle_occupancy_off_suppressed_when_disabled(
    hass: HomeAssistant, helper_entities
) -> None:
    """With the flag off, a sensor-clear event also does not arm the timer."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "on")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert not ctrl._occupancy_timer.is_active  # sensor on → no timer

    ctrl._occupancy_timeout_enabled = False
    # Simulate sensor going clear
    await ctrl.handle_occupancy_off()
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active
```

- [ ] **Step 2: Run both tests to verify they fail**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_start_suppressed_when_disabled \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_handle_occupancy_off_suppressed_when_disabled \
  -v
```

Expected: both FAIL — currently the timer arms regardless of the flag.

- [ ] **Step 3: Add `_start_occupancy_timer` below `_enforce_occupancy_timer`**

In `controller.py`, after `_enforce_occupancy_timer` (ends at line ~1433), add:

```python
    def _start_occupancy_timer(self) -> None:
        """Arm the occupancy timer, respecting the enable flag.

        Single choke-point for every start so the `occupancy_timeout_enabled`
        gate lives in one place. Cancels remain independent of the flag.
        """
        if not self._occupancy_timeout_enabled:
            _LOGGER.debug(
                "Area %s: occupancy timer start suppressed (timeout disabled)",
                self.area.id,
            )
            return
        self._occupancy_timer.start(duration=self._occupancy_off_duration())
```

- [ ] **Step 4: Route `_enforce_occupancy_timer` through the helper**

In `controller.py`, at line ~1433, change:

```python
        if not any_sensor_on:
            self._occupancy_timer.start(duration=self._occupancy_off_duration())
```

to:

```python
        if not any_sensor_on:
            self._start_occupancy_timer()
```

- [ ] **Step 5: Route `handle_occupancy_off` through the helper**

In `controller.py`, at line ~1449, change:

```python
        # Restart timer with full duration (sensor cleared, countdown resets)
        self._occupancy_timer.start(duration=self._occupancy_off_duration())
        self._notify_state_change()
```

to:

```python
        # Restart timer with full duration (sensor cleared, countdown resets)
        self._start_occupancy_timer()
        self._notify_state_change()
```

- [ ] **Step 6: Run the two new tests to verify they pass**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_start_suppressed_when_disabled \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_handle_occupancy_off_suppressed_when_disabled \
  -v
```

Expected: both PASS.

- [ ] **Step 7: Run the full suite to confirm default-on behavior still works**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/ -x -q
```

Expected: 353 passed (350 existing + 3 new through this task). The existing occupancy-enforcement tests in `test_occupancy_enforcement.py` still pass because the flag defaults to True.

- [ ] **Step 8: Commit**

```bash
git add custom_components/area_lighting/controller.py \
        custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py
git commit -m "(Minor) area_lighting: gate occupancy-timer starts on enable flag

Extracts _start_occupancy_timer() as the single timer-start choke point
and swaps the two existing start call sites (_enforce_occupancy_timer,
handle_occupancy_off) to go through it. When occupancy_timeout_enabled
is False the start is suppressed (at DEBUG). Cancels unchanged."
```

---

## Task 3: Async setter for switch-driven transitions

**Goal:** Add `async_set_occupancy_timeout_enabled()` so the switch platform has a single entry point that handles On→Off cancel and Off→On re-arm, plus listener notification + persistence.

**Files:**
- Modify: `custom_components/area_lighting/controller.py` (new method adjacent to `async_set_ambience_enabled` at line ~427)
- Test: `custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py`

- [ ] **Step 1: Write the failing On→Off cancel test**

Append to `test_occupancy_timeout_switch.py`:

```python
@pytest.mark.integration
async def test_on_to_off_cancels_running_timer_without_firing(
    hass: HomeAssistant, helper_entities
) -> None:
    """Switching off mid-countdown cancels the timer; lights stay on."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active
    assert ctrl._state.is_on  # lights on, area in active scene

    await ctrl.async_set_occupancy_timeout_enabled(False)
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active
    # The lights-off callback (_on_occupancy_timer) must NOT have fired
    assert ctrl._state.is_on
    assert ctrl._state.scene_slug == "circadian"
```

- [ ] **Step 2: Write the failing Off→On re-arm test**

Append to `test_occupancy_timeout_switch.py`:

```python
@pytest.mark.integration
async def test_off_to_on_rearms_when_area_occupied_and_sensors_clear(
    hass: HomeAssistant, helper_entities
) -> None:
    """Turning the flag back on while area is occupied re-arms the timer."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    # Pre-disable so the scene activation doesn't arm the timer
    await ctrl.async_set_occupancy_timeout_enabled(False)
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert not ctrl._occupancy_timer.is_active

    # Now flip it on — area is occupied, sensor is clear → should arm.
    await ctrl.async_set_occupancy_timeout_enabled(True)
    await hass.async_block_till_done()

    assert ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_set_is_idempotent(hass: HomeAssistant, helper_entities) -> None:
    """Setting the flag to its current value is a no-op."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    deadline_before = ctrl._occupancy_timer.deadline_utc

    # Flag is already True by default — this call must not reset the deadline.
    await ctrl.async_set_occupancy_timeout_enabled(True)
    await hass.async_block_till_done()

    assert ctrl._occupancy_timer.is_active
    assert ctrl._occupancy_timer.deadline_utc == deadline_before
```

- [ ] **Step 3: Run the three new tests to verify they fail**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_on_to_off_cancels_running_timer_without_firing \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_off_to_on_rearms_when_area_occupied_and_sensors_clear \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_set_is_idempotent \
  -v
```

Expected: all three FAIL with `AttributeError: 'AreaLightingController' object has no attribute 'async_set_occupancy_timeout_enabled'`.

- [ ] **Step 4: Add the async setter next to `async_set_ambience_enabled`**

In `controller.py`, find `async_set_ambience_enabled` at lines ~427–438:

```python
    async def async_set_ambience_enabled(self, value: bool) -> None:
        """Set ambience_enabled and immediately apply ambient/off transition."""
        was_enabled = self._ambience_enabled
        self._ambience_enabled = value
        self._notify_state_change()
        if value == was_enabled:
            return
        if value:
            if self._is_ambient_zone_enabled():
                await self.handle_ambient_enabled()
        else:
            await self.handle_ambient_disabled()
```

Immediately after it, add:

```python
    async def async_set_occupancy_timeout_enabled(self, enabled: bool) -> None:
        """Set occupancy_timeout_enabled and apply timer side effects.

        On→Off: cancels any running occupancy timer without firing the
        lights-off callback. Off→On: re-arms the timer if the area is
        currently in an on-scene with all occupancy sensors clear
        (via _enforce_occupancy_timer, which encodes those preconditions).
        Idempotent.
        """
        if self._occupancy_timeout_enabled == enabled:
            return
        self._occupancy_timeout_enabled = enabled
        if enabled:
            self._enforce_occupancy_timer()
        else:
            self._occupancy_timer.cancel()
        self._notify_state_change()  # also schedules the persistence save
```

- [ ] **Step 5: Run the three tests to verify they pass**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_on_to_off_cancels_running_timer_without_firing \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_off_to_on_rearms_when_area_occupied_and_sensors_clear \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_set_is_idempotent \
  -v
```

Expected: all three PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/area_lighting/controller.py \
        custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py
git commit -m "(Minor) area_lighting: async setter for occupancy_timeout_enabled

async_set_occupancy_timeout_enabled() owns the switch-driven transitions:
On→Off cancels any running timer (callback not fired); Off→On calls
_enforce_occupancy_timer to re-arm when the area is occupied and sensors
are clear. Idempotent on no-change."
```

---

## Task 4: Expose the switch entity

**Goal:** Register a `switch.<area>_occupancy_timeout_enabled` entity that reads `controller.occupancy_timeout_enabled` and routes writes through `async_set_occupancy_timeout_enabled`.

**Files:**
- Modify: `custom_components/area_lighting/switch.py` (add tuple to `SWITCH_DEFS`, add dispatch branch in `async_turn_on`/`async_turn_off`)
- Test: `custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py`

- [ ] **Step 1: Write the failing test — switch entity exists and toggles**

Append to `test_occupancy_timeout_switch.py`:

```python
@pytest.mark.integration
async def test_switch_entity_registered_and_defaults_on(
    hass: HomeAssistant, helper_entities
) -> None:
    """Entity switch.media_room_occupancy_timeout_enabled exists and reads 'on'."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    state = hass.states.get("switch.media_room_occupancy_timeout_enabled")
    assert state is not None
    assert state.state == "on"
    assert state.attributes["friendly_name"] == "Media Room Occupancy Timeout Enabled"


@pytest.mark.integration
async def test_switch_service_call_flips_controller_flag(
    hass: HomeAssistant, helper_entities
) -> None:
    """Calling switch.turn_off on the entity flips the controller flag."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.media_room_occupancy_timeout_enabled"},
        blocking=True,
    )
    assert ctrl.occupancy_timeout_enabled is False
    assert not ctrl._occupancy_timer.is_active  # cancelled on On→Off

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.media_room_occupancy_timeout_enabled"},
        blocking=True,
    )
    assert ctrl.occupancy_timeout_enabled is True
    assert ctrl._occupancy_timer.is_active  # re-armed on Off→On
```

- [ ] **Step 2: Run the two new tests to verify they fail**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_switch_entity_registered_and_defaults_on \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_switch_service_call_flips_controller_flag \
  -v
```

Expected: both FAIL — the entity isn't registered yet.

- [ ] **Step 3: Add the new tuple to `SWITCH_DEFS`**

In `switch.py`, lines 19–26:

```python
# (attr_suffix, name_suffix, icon, default)
SWITCH_DEFS = [
    ("motion_light_enabled", "Motion Light Enabled", "mdi:motion-sensor", True),
    ("ambience_enabled", "Ambience Enabled", "mdi:television-ambient-light", False),
    ("night_mode", "Night Mode", "mdi:weather-night", False),
    # 'Shield off' conveys "the ambient guard is disabled" — motion is
    # allowed to take over ambient-like scenes.
    ("motion_override_ambient", "Motion Override Ambient", "mdi:shield-off-outline", False),
]
```

Append one tuple:

```python
# (attr_suffix, name_suffix, icon, default)
SWITCH_DEFS = [
    ("motion_light_enabled", "Motion Light Enabled", "mdi:motion-sensor", True),
    ("ambience_enabled", "Ambience Enabled", "mdi:television-ambient-light", False),
    ("night_mode", "Night Mode", "mdi:weather-night", False),
    # 'Shield off' conveys "the ambient guard is disabled" — motion is
    # allowed to take over ambient-like scenes.
    ("motion_override_ambient", "Motion Override Ambient", "mdi:shield-off-outline", False),
    ("occupancy_timeout_enabled", "Occupancy Timeout Enabled", "mdi:timer-cog-outline", True),
]
```

- [ ] **Step 4: Route writes through the async setter in `async_turn_on`/`async_turn_off`**

In `switch.py`, replace `async_turn_on` (lines 69–78) and `async_turn_off` (lines 80–89):

```python
    async def async_turn_on(self, **kwargs: Any) -> None:
        _LOGGER.debug(
            "Area %s: switch %s set to on",
            self._controller.area.id,
            self._attr_key,
        )
        if self._attr_key == "ambience_enabled":
            await self._controller.async_set_ambience_enabled(True)
        else:
            setattr(self._controller, self._attr_key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _LOGGER.debug(
            "Area %s: switch %s set to off",
            self._controller.area.id,
            self._attr_key,
        )
        if self._attr_key == "ambience_enabled":
            await self._controller.async_set_ambience_enabled(False)
        else:
            setattr(self._controller, self._attr_key, False)
```

with the added branch for the new key:

```python
    async def async_turn_on(self, **kwargs: Any) -> None:
        _LOGGER.debug(
            "Area %s: switch %s set to on",
            self._controller.area.id,
            self._attr_key,
        )
        if self._attr_key == "ambience_enabled":
            await self._controller.async_set_ambience_enabled(True)
        elif self._attr_key == "occupancy_timeout_enabled":
            await self._controller.async_set_occupancy_timeout_enabled(True)
        else:
            setattr(self._controller, self._attr_key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _LOGGER.debug(
            "Area %s: switch %s set to off",
            self._controller.area.id,
            self._attr_key,
        )
        if self._attr_key == "ambience_enabled":
            await self._controller.async_set_ambience_enabled(False)
        elif self._attr_key == "occupancy_timeout_enabled":
            await self._controller.async_set_occupancy_timeout_enabled(False)
        else:
            setattr(self._controller, self._attr_key, False)
```

No change to `is_on` — the generic `getattr(self._controller, self._attr_key)` path already works for this property. No change to `__init__` — the generic entity_id/name derivation already handles the new attr.

- [ ] **Step 5: Run the two new tests to verify they pass**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_switch_entity_registered_and_defaults_on \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_switch_service_call_flips_controller_flag \
  -v
```

Expected: both PASS.

- [ ] **Step 6: Run the entity-naming regression check**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_entity_naming.py -v
```

Expected: all tests pass. The generic `test_all_switch_entity_ids_match_friendly_names` test in that file iterates every registered `switch.network_room_*` entity and asserts the entity_id matches the snake-cased friendly name — it will cover the new entity automatically without code changes.

- [ ] **Step 7: Run the full suite**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/ -x -q
```

Expected: 358 passed (350 existing + 8 new through this task).

- [ ] **Step 8: Commit**

```bash
git add custom_components/area_lighting/switch.py \
        custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py
git commit -m "(Minor) area_lighting: add Occupancy Timeout Enabled switch entity

Registers switch.<area>_occupancy_timeout_enabled (default on, icon
mdi:timer-cog-outline) and routes toggles through the controller's
async_set_occupancy_timeout_enabled so cancel/re-arm side effects
run on the HA loop."
```

---

## Task 5: Persist the flag across restarts

**Goal:** Round-trip the flag through `state_dict()` / `load_persisted_state()` alongside the other booleans. Default to `True` on missing keys so existing installs start with the feature on.

**Files:**
- Modify: `custom_components/area_lighting/controller.py` (state_dict at ~194, load_persisted_state at ~146)
- Test: `custom_components/area_lighting/tests/integration/test_persistence.py`

- [ ] **Step 1: Write the failing round-trip test**

Append to `custom_components/area_lighting/tests/integration/test_persistence.py`:

```python
@pytest.mark.integration
async def test_occupancy_timeout_enabled_round_trips(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """state_dict persists occupancy_timeout_enabled; load_persisted_state restores it."""
    await _setup_with_config(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    # Flip the flag off and serialize
    await ctrl.async_set_occupancy_timeout_enabled(False)
    saved = ctrl.state_dict()
    assert saved["occupancy_timeout_enabled"] is False

    # Rehydrate into a fresh controller
    from custom_components.area_lighting.controller import AreaLightingController

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    assert fresh.occupancy_timeout_enabled is True  # default before load
    fresh.load_persisted_state(saved)
    assert fresh.occupancy_timeout_enabled is False


@pytest.mark.integration
async def test_occupancy_timeout_enabled_defaults_true_when_missing(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """load_persisted_state treats missing key as True (upgrade path)."""
    await _setup_with_config(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    from custom_components.area_lighting.controller import AreaLightingController

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    # Simulate a persistence dict saved before this feature existed
    legacy = {"motion_light_enabled": False, "ambience_enabled": True}
    fresh.load_persisted_state(legacy)
    assert fresh.occupancy_timeout_enabled is True
```

- [ ] **Step 2: Run the two new tests to verify they fail**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_persistence.py::test_occupancy_timeout_enabled_round_trips \
  custom_components/area_lighting/tests/integration/test_persistence.py::test_occupancy_timeout_enabled_defaults_true_when_missing \
  -v
```

Expected: the round-trip test fails (`saved["occupancy_timeout_enabled"]` raises KeyError). The defaults-true test passes already (init default), but we keep it as a regression guard.

- [ ] **Step 3: Add the key to `state_dict`**

In `controller.py`, find `state_dict` at lines ~192–223. Add one line after `"motion_override_ambient": self._motion_override_ambient,` (line ~199):

```python
            "occupancy_timeout_enabled": self._occupancy_timeout_enabled,
```

The resulting block:

```python
            "motion_override_ambient": self._motion_override_ambient,
            "occupancy_timeout_enabled": self._occupancy_timeout_enabled,
            "manual_fadeout_seconds": self._manual_fadeout_seconds,
```

- [ ] **Step 4: Add the restore to `load_persisted_state`**

In `controller.py`, find `load_persisted_state` at lines ~146–190. After the `motion_override_ambient` restore block (line ~163), add:

```python
        if "occupancy_timeout_enabled" in data:
            self._occupancy_timeout_enabled = bool(data["occupancy_timeout_enabled"])
```

Resulting context:

```python
        if "motion_override_ambient" in data:
            self._motion_override_ambient = bool(data["motion_override_ambient"])
        elif "override_ambient" in data:
            self._motion_override_ambient = bool(data["override_ambient"])
        if "occupancy_timeout_enabled" in data:
            self._occupancy_timeout_enabled = bool(data["occupancy_timeout_enabled"])
```

The missing-key path is implicit: the `__init__` default of `True` remains, which is exactly the desired upgrade behavior.

- [ ] **Step 5: Run the two tests to verify they pass**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_persistence.py::test_occupancy_timeout_enabled_round_trips \
  custom_components/area_lighting/tests/integration/test_persistence.py::test_occupancy_timeout_enabled_defaults_true_when_missing \
  -v
```

Expected: both PASS.

- [ ] **Step 6: Run the full suite**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/ -x -q
```

Expected: 360 passed.

- [ ] **Step 7: Commit**

```bash
git add custom_components/area_lighting/controller.py \
        custom_components/area_lighting/tests/integration/test_persistence.py
git commit -m "(Minor) area_lighting: persist occupancy_timeout_enabled

Adds the flag to state_dict/load_persisted_state. Missing-key path
falls through to the __init__ default of True, so existing installs
start with the feature on after upgrade."
```

---

## Task 6: Expose the flag in the diagnostic snapshot

**Goal:** One-line addition so the flag shows up in the existing `sensor.area_lighting_diagnostics` attribute dict.

**Files:**
- Modify: `custom_components/area_lighting/controller.py` (`diagnostic_snapshot` at ~288)
- Test: `custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py`

- [ ] **Step 1: Write the failing test**

Append to `test_occupancy_timeout_switch.py`:

```python
@pytest.mark.integration
async def test_diagnostic_snapshot_includes_flag(
    hass: HomeAssistant, helper_entities
) -> None:
    """diagnostic_snapshot exposes occupancy_timeout_enabled."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]
    snap = ctrl.diagnostic_snapshot()
    assert "occupancy_timeout_enabled" in snap
    assert snap["occupancy_timeout_enabled"] is True

    await ctrl.async_set_occupancy_timeout_enabled(False)
    snap = ctrl.diagnostic_snapshot()
    assert snap["occupancy_timeout_enabled"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_diagnostic_snapshot_includes_flag \
  -v
```

Expected: FAIL — the key is absent.

- [ ] **Step 3: Add the key to `diagnostic_snapshot`**

In `controller.py`, find `diagnostic_snapshot` at lines ~288–318. Add one line after `"motion_override_ambient": self._motion_override_ambient,` (line ~299):

```python
            "occupancy_timeout_enabled": self._occupancy_timeout_enabled,
```

- [ ] **Step 4: Run the test to verify it passes**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py::test_diagnostic_snapshot_includes_flag \
  -v
```

Expected: PASS.

- [ ] **Step 5: Run the full suite**

```
/home/aaron/git/aaron/haconf/custom_components/area_lighting/.venv/bin/python -m pytest \
  custom_components/area_lighting/tests/ -x -q
```

Expected: 361 passed.

- [ ] **Step 6: Commit**

```bash
git add custom_components/area_lighting/controller.py \
        custom_components/area_lighting/tests/integration/test_occupancy_timeout_switch.py
git commit -m "(Minor) area_lighting: surface occupancy_timeout_enabled in diagnostics

diagnostic_snapshot() now exposes the flag, so it shows up in the
sensor.area_lighting_diagnostics attributes alongside the other
switch booleans."
```

---

## Task 7: Full-stack verification via Dagger

**Goal:** Run the same pipeline CI runs to confirm lint + typecheck + tests all pass on the full set of changes.

**Files:** none (verification only).

- [ ] **Step 1: Run `dagger call all`**

```
dagger call all
```

Expected: all stages pass (`lint`, `typecheck`, `test`, `test-versioning`). This takes ~10s after cache warm-up.

- [ ] **Step 2: If anything fails, fix in a follow-up commit**

Most likely candidates: ruff format on the new test file (run `dagger call lint` alone to see the failure), or a stray unused import. Fix inline and commit with prefix `(Patch) area_lighting: ...`.

- [ ] **Step 3: Push and watch CI auto-tag**

```
git push origin main
```

Expected: the main-branch pipeline runs `check` and then `tag:auto`. Because at least one commit from this plan carries `(Minor)`, the resulting tag is the next minor bump (e.g., `v0.3.0` from the current `v0.2.0`). Confirm via `git fetch --tags origin && git tag --sort=-v:refname | head -3`.

---

## Self-review

Checked against the spec (`docs/superpowers/specs/2026-04-16-occupancy-timeout-switch-design.md`):

- **Summary / default-on, gate suppresses start, On→Off cancel without firing, Off→On re-arm:** covered by Tasks 1–3 and the corresponding integration tests in Task 1 Step 1, Task 2 Step 1, Task 3 Steps 1–2.
- **Switch entity `switch.<area>_occupancy_timeout_enabled` / `"Occupancy Timeout Enabled"` label / `mdi:timer-cog-outline` icon:** Task 4.
- **Persistence with default-True on missing key:** Task 5.
- **Diagnostic snapshot:** Task 6.
- **Cancels remain independent of the flag:** implicit — the plan leaves every cancel site untouched.
- **Timer restoration unaffected by gate:** implicit — `timer.restore()` in `restore_timers` is not modified.
- **Out-of-scope items (YAML config, motion-timer interaction, partial-gate semantics):** not addressed in the plan. Good.

No placeholders. Method/property names consistent across tasks (`occupancy_timeout_enabled`, `_occupancy_timeout_enabled`, `async_set_occupancy_timeout_enabled`, `_start_occupancy_timer`).
