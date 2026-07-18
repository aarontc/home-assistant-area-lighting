# Global Motion & Occupancy Master Switches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two integration-owned global master switches
(`switch.area_lighting_motion_lights_enabled`,
`switch.area_lighting_occupancy_timeout_enabled`) that suppress
motion-triggered lights-on and occupancy-timer lights-off across every
area, replacing the external `input_boolean.motion_light_enabled` helper.

**Architecture:** A new singleton `GlobalToggles` holder (modeled on
`AreaLightingDiagnosticSensor`) owns two booleans, persisted via a
reserved key in `StateStorage`. Each behavior gate is `AND`-ed with its
existing per-area switch: motion in `event_handlers._check_conditions()`,
occupancy in `controller._start_occupancy_timer()` /
`_on_occupancy_timer()`. Two `SwitchEntity` instances expose the flags.

**Tech Stack:** Python 3.13, Home Assistant custom component (YAML-config,
no config flow), `pytest-homeassistant-custom-component`, `uv`, `ruff`.

## Global Constraints

- **Never mention Claude, Anthropic, or any AI** in code, comments, commit
  messages, commit trailers, docs, or CHANGELOG. No `Co-Authored-By`.
- **Commit subjects must start with `(Major)`, `(Minor)`, or `(Patch)`**
  (enforced by `hooks/commit-msg`; read by `tag:auto`).
- **No em dashes in commit messages** — use commas, colons, or parentheses.
- **Never** put the literal string `skip ci` in a subject or body.
- **Do NOT hand-edit** `pyproject.toml` / `manifest.json` / `uv.lock`
  versions — `tag:auto` computes the bump from commit subjects.
- **Always end files with a newline. Use full words, not abbreviations.**
- **Gate fail-open semantics:** a missing holder
  (`hass.data[DOMAIN].get("global")` is `None`) means *enabled* (today's
  behavior), never disabled.
- **Both globals default `True` (on).**
- **Versioning callout (decide before Task 5's commit):** the spec calls
  for a `(Major)` prefix on the external-helper removal. On this 0.x
  project, `(Major)` bumps to `1.0.0`. If a 1.0 jump is not intended, use
  `(Minor)` (→ `0.15.0`) and keep the `BREAKING:` note in the commit body
  and CHANGELOG. **Confirm the prefix with the maintainer.** This plan
  writes `(Major)` per the approved spec; change it at commit time if the
  maintainer prefers `(Minor)`.
- **Verify command** (run from `custom_components/area_lighting/`):
  ```bash
  uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto
  ```
- All test commands below assume CWD `custom_components/area_lighting/`.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `state_storage.py` | persistence | add reserved `__global__` key + accessors |
| `global_state.py` | **new** — holds/persists/broadcasts the two flags | create |
| `controller.py` | occupancy timer gates + public wrappers | modify |
| `event_handlers.py` | motion gate; remove external helper | modify |
| `const.py` | remove `GLOBAL_MOTION_LIGHT_ENABLED_ENTITY` | modify |
| `switch.py` | global switch class + def table | modify |
| `__init__.py` | construct/load holder; register global switches | modify |
| `tests/test_global_toggles.py` | **new** — pure-unit `GlobalToggles` | create |
| `tests/integration/test_global_state_persistence.py` | **new** — StateStorage round-trip | create |
| `tests/integration/test_global_master_switches.py` | **new** — behavioral | create |
| `tests/integration/conftest.py` | remove external-helper fixture | modify |
| `tests/integration/test_validation.py` | remove external-helper assertions | modify |
| `CHANGELOG.md`, `README.md` | feature + migration note | modify |

Deferred (out of this plan, per spec "optional"): the diagnostic-sensor
`=== global ===` block. Not implemented; can be added later.

---

### Task 1: StateStorage reserved global-state key

**Files:**
- Modify: `custom_components/area_lighting/state_storage.py`
- Test: `custom_components/area_lighting/tests/integration/test_global_state_persistence.py` (create)

**Interfaces:**
- Produces: `StateStorage.get_global_state() -> dict[str, Any]`,
  `StateStorage.async_save_global_state(state: dict[str, Any]) -> None`,
  module constant `GLOBAL_STATE_KEY = "__global__"`.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_global_state_persistence.py`:

```python
"""Round-trip tests for the StateStorage reserved global-state key."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.area_lighting.state_storage import StateStorage


@pytest.mark.integration
async def test_global_state_roundtrips(hass: HomeAssistant) -> None:
    s1 = StateStorage(hass)
    await s1.async_load()
    assert s1.get_global_state() == {}

    await s1.async_save_global_state(
        {"motion_lights_enabled": False, "occupancy_timeout_enabled": True}
    )

    s2 = StateStorage(hass)
    await s2.async_load()
    assert s2.get_global_state() == {
        "motion_lights_enabled": False,
        "occupancy_timeout_enabled": True,
    }


@pytest.mark.integration
async def test_global_key_does_not_shadow_area_state(hass: HomeAssistant) -> None:
    s = StateStorage(hass)
    await s.async_load()
    await s.async_save_area_state("living_room", {"current_scene": "evening"})
    await s.async_save_global_state({"motion_lights_enabled": False})

    assert s.get_area_state("living_room") == {"current_scene": "evening"}
    assert s.get_global_state() == {"motion_lights_enabled": False}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_global_state_persistence.py -v`
Expected: FAIL — `AttributeError: 'StateStorage' object has no attribute 'get_global_state'`.

- [ ] **Step 3: Implement**

In `state_storage.py`, add the constant below `STORAGE_VERSION` (line 22):

```python
# Reserved key for non-area (global) state. Double underscore cannot
# collide with a Home Assistant area slug.
GLOBAL_STATE_KEY = "__global__"
```

Add these two methods to `StateStorage` after `async_save_area_state`
(after line 61):

```python
    def get_global_state(self) -> dict[str, Any]:
        """Get stored global (all-area) toggle state (empty dict if none)."""
        return self._data.get(GLOBAL_STATE_KEY, {})

    async def async_save_global_state(self, state: dict[str, Any]) -> None:
        """Save global (all-area) toggle state under the reserved key."""
        _LOGGER.debug("Persisted global state (%d keys)", len(state))
        self._data[GLOBAL_STATE_KEY] = state
        await self.async_save()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_global_state_persistence.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add custom_components/area_lighting/state_storage.py \
        custom_components/area_lighting/tests/integration/test_global_state_persistence.py
git commit -m "(Patch) area_lighting: add reserved global-state key to StateStorage"
```

---

### Task 2: GlobalToggles holder module

**Files:**
- Create: `custom_components/area_lighting/global_state.py`
- Test: `custom_components/area_lighting/tests/test_global_toggles.py` (create)

**Interfaces:**
- Consumes: `StateStorage.get_global_state()` /
  `async_save_global_state()` (Task 1); at runtime calls
  `controller.enforce_occupancy_timer()` / `cancel_occupancy_timer()`
  (Task 4) via `hass.data[DOMAIN]["controllers"]`.
- Produces: `GlobalToggles(hass, state_storage)` with read-only properties
  `motion_lights_enabled: bool`, `occupancy_timeout_enabled: bool`;
  `add_state_listener(cb)` / `remove_state_listener(cb)`;
  `load_persisted_state(dict)` / `state_dict() -> dict`;
  `async_set_motion_lights_enabled(bool)` /
  `async_set_occupancy_timeout_enabled(bool)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_global_toggles.py`:

```python
"""Pure-unit tests for GlobalToggles (no Home Assistant dependency)."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.global_state import GlobalToggles


class _FakeStorage:
    def __init__(self, initial: dict | None = None) -> None:
        self.saved: list[dict] = []
        self._initial = initial or {}

    def get_global_state(self) -> dict:
        return dict(self._initial)

    async def async_save_global_state(self, state: dict) -> None:
        self.saved.append(dict(state))


class _FakeController:
    def __init__(self) -> None:
        self.enforced = 0
        self.cancelled = 0

    def enforce_occupancy_timer(self) -> None:
        self.enforced += 1

    def cancel_occupancy_timer(self) -> None:
        self.cancelled += 1


class _FakeHass:
    def __init__(self) -> None:
        self.data: dict = {}
        self._tasks: list = []

    def async_create_task(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.append(task)
        return task

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()


def test_defaults_enabled():
    t = GlobalToggles(_FakeHass(), _FakeStorage())
    assert t.motion_lights_enabled is True
    assert t.occupancy_timeout_enabled is True


def test_load_persisted_state_applies_and_ignores_empty():
    t = GlobalToggles(_FakeHass(), _FakeStorage())
    t.load_persisted_state({})
    assert t.motion_lights_enabled is True
    t.load_persisted_state(
        {"motion_lights_enabled": False, "occupancy_timeout_enabled": False}
    )
    assert t.motion_lights_enabled is False
    assert t.occupancy_timeout_enabled is False


def test_state_dict_shape():
    t = GlobalToggles(_FakeHass(), _FakeStorage())
    t.load_persisted_state({"motion_lights_enabled": False})
    assert t.state_dict() == {
        "motion_lights_enabled": False,
        "occupancy_timeout_enabled": True,
    }


@pytest.mark.asyncio
async def test_set_motion_notifies_and_saves():
    hass, storage = _FakeHass(), _FakeStorage()
    t = GlobalToggles(hass, storage)
    calls: list[int] = []
    t.add_state_listener(lambda: calls.append(1))

    await t.async_set_motion_lights_enabled(False)
    await hass.drain()

    assert t.motion_lights_enabled is False
    assert calls == [1]
    assert storage.saved == [
        {"motion_lights_enabled": False, "occupancy_timeout_enabled": True}
    ]


@pytest.mark.asyncio
async def test_set_motion_idempotent():
    hass, storage = _FakeHass(), _FakeStorage()
    t = GlobalToggles(hass, storage)
    calls: list[int] = []
    t.add_state_listener(lambda: calls.append(1))

    await t.async_set_motion_lights_enabled(True)  # already True
    await hass.drain()

    assert calls == []
    assert storage.saved == []


@pytest.mark.asyncio
async def test_set_occupancy_fans_out_cancel_then_enforce():
    hass, storage = _FakeHass(), _FakeStorage()
    c1, c2 = _FakeController(), _FakeController()
    hass.data[DOMAIN] = {"controllers": {"a": c1, "b": c2}}
    t = GlobalToggles(hass, storage)

    await t.async_set_occupancy_timeout_enabled(False)
    await hass.drain()
    assert (c1.cancelled, c2.cancelled) == (1, 1)
    assert (c1.enforced, c2.enforced) == (0, 0)

    await t.async_set_occupancy_timeout_enabled(True)
    await hass.drain()
    assert (c1.enforced, c2.enforced) == (1, 1)


@pytest.mark.asyncio
async def test_remove_state_listener_stops_notifications():
    hass, storage = _FakeHass(), _FakeStorage()
    t = GlobalToggles(hass, storage)
    calls: list[int] = []

    def cb() -> None:
        calls.append(1)

    t.add_state_listener(cb)
    t.remove_state_listener(cb)
    await t.async_set_motion_lights_enabled(False)
    await hass.drain()
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_global_toggles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.area_lighting.global_state'`.

- [ ] **Step 3: Implement**

Create `global_state.py`:

```python
"""Global (all-area) master toggles for area_lighting.

Owns two booleans that gate motion-triggered lights-on and the
occupancy-timeout lights-off across every area. Persisted via the
StateStorage reserved global key. Reaches controllers lazily through
hass.data so it holds no back-references.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .state_storage import StateStorage

_LOGGER = logging.getLogger(__name__)


class GlobalToggles:
    """Holds, persists, and broadcasts the two global master flags."""

    def __init__(self, hass: HomeAssistant, state_storage: StateStorage) -> None:
        self._hass = hass
        self._state_storage = state_storage
        self._motion_lights_enabled = True
        self._occupancy_timeout_enabled = True
        self._listeners: list[Callable[[], None]] = []

    @property
    def motion_lights_enabled(self) -> bool:
        return self._motion_lights_enabled

    @property
    def occupancy_timeout_enabled(self) -> bool:
        return self._occupancy_timeout_enabled

    # ── listener plumbing (mirrors controller.add_state_listener) ──
    def add_state_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def remove_state_listener(self, cb: Callable[[], None]) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def _schedule_save(self) -> None:
        self._hass.async_create_task(
            self._state_storage.async_save_global_state(self.state_dict())
        )

    # ── persistence ──
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

    # ── setters (called by the switch entities) ──
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
                ctrl.enforce_occupancy_timer()
            else:
                ctrl.cancel_occupancy_timer()
        self._notify()
        self._schedule_save()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_global_toggles.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add custom_components/area_lighting/global_state.py \
        custom_components/area_lighting/tests/test_global_toggles.py
git commit -m "(Patch) area_lighting: add GlobalToggles master-flag holder"
```

---

### Task 3: Wire GlobalToggles into setup

**Files:**
- Modify: `custom_components/area_lighting/__init__.py:102-119`
- Test: `custom_components/area_lighting/tests/integration/test_global_master_switches.py` (create)

**Interfaces:**
- Consumes: `GlobalToggles` (Task 2), `StateStorage.get_global_state()` (Task 1).
- Produces: `hass.data[DOMAIN]["global"]` is a `GlobalToggles`, created
  before controllers and before entity registration.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_global_master_switches.py`:

```python
"""Global master switches: motion-on and occupancy-timeout kill switches."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


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
                    "occupancy_light_timer_durations": {"off": "00:30:00"},
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.media_room_presence",
                    ],
                    "motion_light_timer_durations": {"off": "00:08:00"},
                }
            ]
        }
    }


@pytest.mark.integration
async def test_holder_created_and_defaults_on(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    toggles = _toggles(hass)
    assert isinstance(toggles, GlobalToggles)
    assert toggles.motion_lights_enabled is True
    assert toggles.occupancy_timeout_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_global_master_switches.py::test_holder_created_and_defaults_on -v`
Expected: FAIL — `KeyError: 'global'`.

- [ ] **Step 3: Implement**

In `__init__.py`, replace the storage-setup block (lines 101-111) so the
holder is created and exposed:

```python
    # Initialize state storage (per-area runtime state, persisted across reboots)
    state_storage = StateStorage(hass)
    await state_storage.async_load()

    # Global (all-area) master toggles, restored from persisted state
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

Add the import near the other local imports at the top of `__init__.py`
(alongside `from .state_storage import StateStorage`):

```python
from .global_state import GlobalToggles
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_global_master_switches.py::test_holder_created_and_defaults_on -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/area_lighting/__init__.py \
        custom_components/area_lighting/tests/integration/test_global_master_switches.py
git commit -m "(Patch) area_lighting: create GlobalToggles holder at setup"
```

---

### Task 4: Controller occupancy global gate + public wrappers

**Files:**
- Modify: `custom_components/area_lighting/controller.py` — `_start_occupancy_timer` (1768-1780), `_on_occupancy_timer` (1935-1939), add `cancel_occupancy_timer` / `enforce_occupancy_timer` near line 1806
- Test: `custom_components/area_lighting/tests/integration/test_global_master_switches.py` (extend)

**Interfaces:**
- Consumes: `hass.data[DOMAIN]["global"].occupancy_timeout_enabled` (Task 3).
- Produces: `controller.cancel_occupancy_timer() -> None`,
  `controller.enforce_occupancy_timer() -> None` (called by `GlobalToggles`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_global_master_switches.py`:

```python
@pytest.mark.integration
async def test_global_occupancy_off_suppresses_arm(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())
    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]

    await _toggles(hass).async_set_occupancy_timeout_enabled(False)
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_global_occupancy_off_cancels_running_timer(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())
    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]

    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active

    await _toggles(hass).async_set_occupancy_timeout_enabled(False)
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active
    assert ctrl._state.is_on  # lights stayed on; no lights-off callback fired
    assert ctrl._state.scene_slug == "circadian"


@pytest.mark.integration
async def test_global_occupancy_off_expiry_is_noop(hass: HomeAssistant, helper_entities) -> None:
    """A restored/past-due timer firing while globally disabled is a no-op."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())
    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]

    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._state.is_on

    # Disable the flag WITHOUT going through the setter (so no cancel happens),
    # simulating a timer that outlived the flag flip / was restored at startup.
    _toggles(hass)._occupancy_timeout_enabled = False
    await ctrl._on_occupancy_timer()
    await hass.async_block_till_done()

    assert ctrl._state.is_on
    assert ctrl._state.scene_slug == "circadian"


@pytest.mark.integration
async def test_global_occupancy_off_then_on_rearms(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())
    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]

    await _toggles(hass).async_set_occupancy_timeout_enabled(False)
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert not ctrl._occupancy_timer.is_active

    await _toggles(hass).async_set_occupancy_timeout_enabled(True)
    await hass.async_block_till_done()

    assert ctrl._occupancy_timer.is_active
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_global_master_switches.py -v -k "occupancy"`
Expected: FAIL — `AttributeError: 'AreaLightingController' object has no attribute 'cancel_occupancy_timer'`
(raised from `GlobalToggles.async_set_occupancy_timeout_enabled`), and the
suppress/expiry tests fail because the global gate is not yet applied.

- [ ] **Step 3: Implement**

In `controller.py`, extend `_start_occupancy_timer` (lines 1768-1780) with
the global gate after the existing per-area check:

```python
    def _start_occupancy_timer(self) -> None:
        """Arm the occupancy timer, respecting the enable flags.

        Single choke-point for every start so the per-area and global
        `occupancy_timeout_enabled` gates live in one place. Cancels
        remain independent of the flags.
        """
        if not self._occupancy_timeout_enabled:
            _LOGGER.debug(
                "Area %s: occupancy timer start suppressed (timeout disabled)",
                self.area.id,
            )
            return
        toggles = self.hass.data.get(DOMAIN, {}).get("global")
        if toggles is not None and not toggles.occupancy_timeout_enabled:
            _LOGGER.debug(
                "Area %s: occupancy timer start suppressed (global timeout disabled)",
                self.area.id,
            )
            return
        self._occupancy_timer.start(duration=self._occupancy_off_duration())
```

Extend `_on_occupancy_timer` (lines 1935-1939):

```python
    async def _on_occupancy_timer(self) -> None:
        _LOGGER.debug("Area %s: occupancy timer expired", self.area.id)
        if self._state.is_off or self._state.is_ambient_like:
            return
        toggles = self.hass.data.get(DOMAIN, {}).get("global")
        if toggles is not None and not toggles.occupancy_timeout_enabled:
            _LOGGER.debug(
                "Area %s: occupancy timer expiry suppressed (global timeout disabled)",
                self.area.id,
            )
            return
        await self.lighting_off_fade(source=ActivationSource.OCCUPANCY)
```

Add two public wrappers next to the other occupancy handlers (after
`handle_occupancy_lights_off`, i.e. after line 1805):

```python
    def cancel_occupancy_timer(self) -> None:
        """Cancel any running occupancy timer without firing lights-off.

        Used by the global occupancy master switch on disable.
        """
        self._occupancy_timer.cancel()

    def enforce_occupancy_timer(self) -> None:
        """Public wrapper: re-evaluate whether the occupancy timer should arm.

        Used by the global occupancy master switch on enable.
        """
        self._enforce_occupancy_timer()
```

(`DOMAIN` is already imported at `controller.py:24`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_global_master_switches.py -v -k "occupancy"`
Expected: PASS (4 passed).

Run the existing per-area occupancy suite to confirm no regression:
Run: `uv run pytest tests/integration/test_occupancy_timeout_switch.py tests/integration/test_occupancy_enforcement.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/area_lighting/controller.py \
        custom_components/area_lighting/tests/integration/test_global_master_switches.py
git commit -m "(Minor) area_lighting: add global occupancy-timeout master gate"
```

---

### Task 5: Motion global gate + remove external helper

**Files:**
- Modify: `custom_components/area_lighting/event_handlers.py` — `_check_conditions` (605-610), remove import (21), remove bootstrap block (127-130), remove validation entry (191-194)
- Modify: `custom_components/area_lighting/const.py:97-98`
- Modify: `custom_components/area_lighting/tests/integration/conftest.py:61-64`
- Modify: `custom_components/area_lighting/tests/integration/test_validation.py` (58, 112, 180)
- Test: `custom_components/area_lighting/tests/integration/test_global_master_switches.py` (extend)

**Interfaces:**
- Consumes: `hass.data[DOMAIN]["global"].motion_lights_enabled` (Task 3).
- Removes: `GLOBAL_MOTION_LIGHT_ENABLED_ENTITY` and every reference to the
  external `input_boolean.motion_light_enabled`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_global_master_switches.py`:

```python
_MOTION_SENSOR = "binary_sensor.network_room_motion_sensor_motion"


@pytest.mark.integration
async def test_global_motion_on_allows_motion_activation(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.motion_light_enabled = True

    hass.states.async_set(_MOTION_SENSOR, "off")
    await hass.async_block_till_done()
    hass.states.async_set(_MOTION_SENSOR, "on")
    await hass.async_block_till_done()

    assert ctrl._state.source == ActivationSource.MOTION
    assert not ctrl._state.is_off


@pytest.mark.integration
async def test_global_motion_off_blocks_motion_activation(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.motion_light_enabled = True  # per-area on: only the GLOBAL gate blocks

    await _toggles(hass).async_set_motion_lights_enabled(False)

    hass.states.async_set(_MOTION_SENSOR, "off")
    await hass.async_block_till_done()
    hass.states.async_set(_MOTION_SENSOR, "on")
    await hass.async_block_till_done()

    assert ctrl._state.is_off  # motion did not turn lights on
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_global_master_switches.py -v -k "motion"`
Expected: `test_global_motion_off_blocks_motion_activation` FAILS
(`ctrl._state.is_off` is False) because `_check_conditions` still gates on
the external helper, not the global flag.

- [ ] **Step 3: Implement — motion gate**

In `event_handlers.py`, replace the external-helper check in
`_check_conditions` (lines 607-610) with the global-flag read:

```python
    def _check_conditions() -> bool:
        """Check global + area motion conditions."""
        toggles = hass.data.get(DOMAIN, {}).get("global")
        if toggles is not None and not toggles.motion_lights_enabled:
            return False

        # Area motion enabled
        if not ctrl.motion_light_enabled:
            return False
```

(`DOMAIN` is already imported at `event_handlers.py:20`.)

- [ ] **Step 4: Implement — remove the external helper**

`event_handlers.py`: remove the import of `GLOBAL_MOTION_LIGHT_ENABLED_ENTITY`
from the `from .const import (` block (line 21).

`event_handlers.py`: delete the bootstrap-suggestion block (lines 127-130):

```python
    if "input_boolean.motion_light_enabled" in missing:
        input_boolean_lines.append(
            "  motion_light_enabled:\n    name: Motion Lighting (Global)\n    initial: true"
        )
```

`event_handlers.py`: delete the validation-required entry (lines 191-194):

```python
        (
            "input_boolean.motion_light_enabled",
            "global motion lighting kill-switch (input_boolean)",
        ),
```

`const.py`: delete the constant and its comment (lines 97-98):

```python
# Global motion lighting enabled entity
GLOBAL_MOTION_LIGHT_ENABLED_ENTITY = "input_boolean.motion_light_enabled"
```

- [ ] **Step 5: Fix tests broken by the removal**

`tests/integration/conftest.py`: remove the `motion_light_enabled`
input_boolean fixture entry (lines 61-64):

```python
                "motion_light_enabled": {
                    "name": "Motion light enabled (global)",
                    "initial": True,
                },
```

`tests/integration/test_validation.py`: remove the setup entry at line 58
(`"motion_light_enabled": {"initial": True},`) and the two assertions that
expect the validator to flag / bootstrap the external helper (lines 112
`assert "motion_light_enabled" in msg` and 180
`assert "motion_light_enabled:" in bootstrap`). Read the surrounding test
bodies first; if a whole test exists solely to assert the external helper
is required, delete that test. Otherwise remove only the two assertion
lines, leaving the rest of each test intact.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_global_master_switches.py -v -k "motion"`
Expected: PASS (2 passed).

Run the motion + validation suites to confirm the removal did not break them:
Run: `uv run pytest tests/integration/test_motion_retrigger.py tests/integration/test_illuminance_gating.py tests/integration/test_validation.py -v`
Expected: PASS. (If `test_validation.py` still fails, a residual
external-helper assertion remains — remove it.)

- [ ] **Step 7: Commit**

> Confirm the prefix per the Global Constraints versioning callout.
> `(Major)` → 1.0.0; `(Minor)` → 0.15.0. This plan uses `(Major)` per the
> approved spec.

```bash
git add custom_components/area_lighting/event_handlers.py \
        custom_components/area_lighting/const.py \
        custom_components/area_lighting/tests/integration/conftest.py \
        custom_components/area_lighting/tests/integration/test_validation.py \
        custom_components/area_lighting/tests/integration/test_global_master_switches.py
git commit -m "$(printf '%s\n' \
  '(Major) area_lighting: replace external motion helper with global master switch' \
  '' \
  'BREAKING: input_boolean.motion_light_enabled is no longer consulted.' \
  'The global motion kill-switch is now switch.area_lighting_motion_lights_enabled.')"
```

---

### Task 6: Global switch entities + registration

**Files:**
- Modify: `custom_components/area_lighting/switch.py` — add `GLOBAL_SWITCH_DEFS` + `AreaLightingGlobalSwitch`
- Modify: `custom_components/area_lighting/__init__.py` — `_register_helper_entities` (append globals before `async_add_entities`); import the new names
- Test: `custom_components/area_lighting/tests/integration/test_global_master_switches.py` (extend)

**Interfaces:**
- Consumes: `hass.data[DOMAIN]["global"]` (Task 3),
  `GlobalToggles.async_set_*` (Task 2).
- Produces entities `switch.area_lighting_motion_lights_enabled`,
  `switch.area_lighting_occupancy_timeout_enabled`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_global_master_switches.py`:

```python
@pytest.mark.integration
async def test_global_switches_registered_default_on_with_icons(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)

    motion = hass.states.get("switch.area_lighting_motion_lights_enabled")
    occ = hass.states.get("switch.area_lighting_occupancy_timeout_enabled")
    assert motion is not None and motion.state == "on"
    assert occ is not None and occ.state == "on"
    assert motion.attributes["friendly_name"] == "Area Lighting Motion Lights (Global)"
    assert occ.attributes["friendly_name"] == "Area Lighting Occupancy Timeout (Global)"
    assert motion.attributes["icon"] == "mdi:motion-sensor"
    assert occ.attributes["icon"] == "mdi:timer-cog-outline"


@pytest.mark.integration
async def test_global_motion_switch_service_call_blocks_and_restores(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.motion_light_enabled = True

    await hass.services.async_call(
        "switch", "turn_off",
        {"entity_id": "switch.area_lighting_motion_lights_enabled"},
        blocking=True,
    )
    assert _toggles(hass).motion_lights_enabled is False
    hass.states.async_set(_MOTION_SENSOR, "off")
    await hass.async_block_till_done()
    hass.states.async_set(_MOTION_SENSOR, "on")
    await hass.async_block_till_done()
    assert ctrl._state.is_off

    await hass.services.async_call(
        "switch", "turn_on",
        {"entity_id": "switch.area_lighting_motion_lights_enabled"},
        blocking=True,
    )
    hass.states.async_set(_MOTION_SENSOR, "off")
    await hass.async_block_till_done()
    hass.states.async_set(_MOTION_SENSOR, "on")
    await hass.async_block_till_done()
    assert ctrl._state.source == ActivationSource.MOTION


@pytest.mark.integration
async def test_global_occupancy_switch_service_call(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())
    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]

    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active

    await hass.services.async_call(
        "switch", "turn_off",
        {"entity_id": "switch.area_lighting_occupancy_timeout_enabled"},
        blocking=True,
    )
    assert _toggles(hass).occupancy_timeout_enabled is False
    assert not ctrl._occupancy_timer.is_active
    assert ctrl._state.is_on

    await hass.services.async_call(
        "switch", "turn_on",
        {"entity_id": "switch.area_lighting_occupancy_timeout_enabled"},
        blocking=True,
    )
    assert _toggles(hass).occupancy_timeout_enabled is True
    assert ctrl._occupancy_timer.is_active
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_global_master_switches.py -v -k "switch"`
Expected: FAIL — the global switch entities do not exist yet
(`hass.states.get(...)` returns `None`).

- [ ] **Step 3: Implement — switch class + defs**

In `switch.py`, add after `SWITCH_DEFS` (after line 27):

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
```

Add the entity class at the end of `switch.py`:

```python
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

- [ ] **Step 4: Implement — registration**

In `__init__.py`, update the switch import to include the new names
(find the existing `from .switch import ...`):

```python
from .switch import (
    SWITCH_DEFS,
    AreaLightingSwitch,
    GLOBAL_SWITCH_DEFS,
    AreaLightingGlobalSwitch,
)
```

(If the existing import is a single-line form, expand it to include
`GLOBAL_SWITCH_DEFS` and `AreaLightingGlobalSwitch`. Keep whatever names
were already imported.)

In `_register_helper_entities`, append the global switches to the
`switches` list after the per-controller loop and before the
`async_add_entities` call (after line 327, the end of the `for ctrl` loop):

```python
    toggles = hass.data.get(DOMAIN, {}).get("global")
    if toggles is not None:
        for flag, name, icon, uid, eid in GLOBAL_SWITCH_DEFS:
            switches.append(
                AreaLightingGlobalSwitch(toggles, flag, name, icon, uid, eid)
            )
```

The global switches have no `_controller` attribute, so
`_assign_entities_to_ha_areas` already skips them (`__init__.py:400-402`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_global_master_switches.py -v`
Expected: PASS (all tests in the file).

Run the entity-naming suite (its switch-naming test filters to
`switch.network_room_*`, so the globals are correctly excluded):
Run: `uv run pytest tests/integration/test_entity_naming.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/area_lighting/switch.py \
        custom_components/area_lighting/__init__.py \
        custom_components/area_lighting/tests/integration/test_global_master_switches.py
git commit -m "(Minor) area_lighting: expose global motion & occupancy master switches"
```

---

### Task 7: Documentation + full-suite verification

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Update CHANGELOG.md**

Add an entry at the top of the changelog's unreleased/pending section
(match the file's existing heading style; do not add a version number —
`tag:auto` owns versions):

```markdown
### Added
- Two global master switches: `switch.area_lighting_motion_lights_enabled`
  and `switch.area_lighting_occupancy_timeout_enabled`. Each is a global
  kill-switch, ANDed with the matching per-area switch. Both default on.

### Changed / BREAKING
- Removed the external `input_boolean.motion_light_enabled` helper. The
  global motion kill-switch is now the owned
  `switch.area_lighting_motion_lights_enabled`. Automations or dashboards
  that toggled the old helper must target the new switch. On upgrade,
  motion lighting is re-enabled by default (the owned switch defaults on),
  so anyone who left the old helper off to suppress motion must turn the
  new switch off. The old helper is now unused and can be deleted.
```

- [ ] **Step 2: Update README.md**

Find the section documenting the global motion helper / required
`input_boolean` entities and replace it with the two owned switches.
Document: names, that both default on, that each is ANDed with the per-area
switch, and the migration note (old `input_boolean.motion_light_enabled` no
longer consulted). If the README enumerates required external helpers,
remove `input_boolean.motion_light_enabled` from that list.

- [ ] **Step 3: Full verification**

Run (from `custom_components/area_lighting/`):
```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto
```
Expected: ruff clean (no lint errors, format check passes) and **all**
tests pass. If `ruff format --check` reports diffs, run
`uv run ruff format .`, re-run the check, and include the reformat in the
commit.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md README.md
git commit -m "(Patch) docs: document global motion & occupancy master switches"
```

- [ ] **Step 5: Final gate**

Confirm the whole suite is green and the working tree is clean:
```bash
git status --porcelain   # expect empty
uv run pytest -n auto     # expect all pass
```

---

## Self-Review (author checklist — completed)

**Spec coverage:**
- Two owned global switches → Tasks 2, 6. ✓
- Motion gate in `_check_conditions` → Task 5. ✓
- Occupancy gates in `_start_occupancy_timer` + `_on_occupancy_timer` → Task 4. ✓
- Fan-out cancel/re-enforce on occupancy toggle → Tasks 2 (logic) + 4 (wrappers) + 6 (via switch). ✓
- Persistence via reserved key → Tasks 1 (storage) + 2 (holder) + 3 (wiring). ✓
- Registration as singleton entities → Task 6. ✓
- Nuke external helper (const, import, bootstrap, validation, gate) → Task 5. ✓
- Test updates (conftest, test_validation) → Task 5. ✓
- Migration / CHANGELOG / README → Task 7. ✓
- Diagnostics block → intentionally deferred (spec-optional), noted in File Structure. ✓

**Placeholder scan:** No `TBD`/`TODO`/"add error handling"/"write tests for
the above". Every code and test step shows complete code. ✓

**Type/name consistency:** `GlobalToggles`, `motion_lights_enabled`,
`occupancy_timeout_enabled`, `async_set_motion_lights_enabled`,
`async_set_occupancy_timeout_enabled`, `cancel_occupancy_timer`,
`enforce_occupancy_timer`, `GLOBAL_SWITCH_DEFS`, `AreaLightingGlobalSwitch`,
`GLOBAL_STATE_KEY`, and the two entity ids are used identically across
Tasks 1-6. ✓
