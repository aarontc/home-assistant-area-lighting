# Demand Response Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a global "demand response" (DR) master switch that, while on, sheds a per-activation fraction of the bulbs each area would turn on (50% for up to 5 on-bulbs, 80% for 6+), leaving off-commands, alerts, and manual areas untouched, and restoring non-manual areas when it clears.

**Architecture:** A single pure policy function (`demand_response.py`) computes the shed set from an activation's on-set. Every light-on emitter (controller scene fan-out, circadian, raise/lower dark bring-up, the kelvin router, and the HA Scene entity) consults that one policy. Shed bulbs are recorded as off-targets so the existing manual-detection / self-heal machinery treats them as intended-off. A third `GlobalToggles` flag drives an owned switch; flipping it reconciles every non-manual area idempotently.

**Tech Stack:** Python 3.13, Home Assistant custom component, `voluptuous` config, `pytest` + `pytest-homeassistant-custom-component`, `ruff`, `uv`.

**Design spec:** `docs/superpowers/specs/2026-07-22-demand-response-design.md` (read it first).

## Global Constraints

- Ratios are hardcoded for v1: `ratio = 0.50 if n <= 5 else 0.80`, where `n` is the count of bulbs the activation would turn ON. `keep = ceil(n * (1 - ratio))`. No config knobs.
- Shed universe is `AreaConfig.lights` (individual bulbs) in config-declaration order. `light_clusters` (Hue Zones) are excluded — they address the same physical bulbs.
- Bulbs are shed from the config-order **tail** of the on-set (first-declared bulbs survive).
- Owned switch entity_id: `switch.area_lighting_demand_response_active`; unique_id: `area_lighting_global_demand_response_active`; friendly name: `Area Lighting Demand Response (Global)`; icon: `mdi:transmission-tower`; default **off**.
- Persisted flag key: `demand_response_active` (stored under the reserved global key).
- Alerts bypass DR entirely (they already use their own apply path and set `_alert_active`).
- Commit subjects MUST start with `(Major)`, `(Minor)`, or `(Patch)`. No em dashes. Never write `skip ci`. Do NOT edit `pyproject.toml` / `manifest.json` / `uv.lock` version fields (the `tag:auto` CI job bumps them). Never mention any AI assistant in any artifact.
- Verification gate (from the worktree root, per `CLAUDE.md`):
  `cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto`

## Setup (once, before Task 1)

- [ ] From the worktree root, install dev deps and confirm a green baseline:

```bash
uv sync --extra dev
uv run pytest -q
```

Expected: all existing tests pass. If any fail before you change anything, stop and report.

---

### Task 1: Pure demand-response policy

**Files:**
- Create: `custom_components/area_lighting/demand_response.py`
- Test: `custom_components/area_lighting/tests/test_demand_response.py`

**Interfaces:**
- Produces:
  - `keep_count(n: int) -> int`
  - `demand_response_shed_ids(ordered_light_ids: list[str], on_ids: list[str]) -> list[str]`
  - `apply_demand_response(targets: dict[str, dict], ordered_light_ids: list[str]) -> dict[str, dict]`

- [ ] **Step 1: Write the failing test**

Create `custom_components/area_lighting/tests/test_demand_response.py`:

```python
"""Pure-unit tests for the demand-response shedding policy."""

from __future__ import annotations

from custom_components.area_lighting.demand_response import (
    apply_demand_response,
    demand_response_shed_ids,
    keep_count,
)


def test_keep_count_boundaries():
    assert keep_count(0) == 0
    assert keep_count(1) == 1  # ceil(1 * 0.5)
    assert keep_count(2) == 1  # ceil(2 * 0.5)
    assert keep_count(5) == 3  # ceil(5 * 0.5)
    assert keep_count(6) == 2  # ceil(6 * 0.2)
    assert keep_count(10) == 2  # ceil(10 * 0.2)
    assert keep_count(25) == 5  # ceil(25 * 0.2)


def test_shed_ids_tail_config_order():
    ordered = ["l1", "l2", "l3", "l4", "l5", "l6"]
    assert demand_response_shed_ids(ordered, ordered) == ["l3", "l4", "l5", "l6"]


def test_shed_ids_uses_only_on_subset():
    ordered = ["l1", "l2", "l3", "l4", "l5"]
    on = ["l2", "l4"]  # n=2 -> 50% -> keep 1 -> shed tail of the on-set
    assert demand_response_shed_ids(ordered, on) == ["l4"]


def test_shed_ids_empty_on_set():
    assert demand_response_shed_ids(["l1", "l2"], []) == []


def test_shed_ids_ignores_ids_not_in_order():
    assert demand_response_shed_ids(["l1", "l2"], ["l1", "l2", "lX"]) == ["l2"]


def test_apply_forces_tail_off():
    ordered = ["l1", "l2", "l3", "l4", "l5", "l6"]
    targets = {eid: {"state": "on", "brightness": 200} for eid in ordered}
    out = apply_demand_response(targets, ordered)
    assert out["l1"] == {"state": "on", "brightness": 200}
    assert out["l2"] == {"state": "on", "brightness": 200}
    for eid in ["l3", "l4", "l5", "l6"]:
        assert out[eid] == {"state": "off"}


def test_apply_does_not_mutate_input():
    ordered = ["l1", "l2"]
    targets = {"l1": {"state": "on"}, "l2": {"state": "on"}}
    original_l2 = targets["l2"]
    out = apply_demand_response(targets, ordered)
    assert targets["l2"] is original_l2
    assert targets["l2"] == {"state": "on"}
    assert out["l2"] == {"state": "off"}


def test_apply_counts_only_on_targets():
    ordered = ["l1", "l2", "l3"]
    targets = {"l1": {"state": "on"}, "l2": {"state": "off"}, "l3": {"state": "on"}}
    out = apply_demand_response(targets, ordered)  # on-set [l1, l3] -> shed [l3]
    assert out["l1"] == {"state": "on"}
    assert out["l3"] == {"state": "off"}
    assert out["l2"] == {"state": "off"}


def test_apply_no_shed_when_single_light():
    out = apply_demand_response({"l1": {"state": "on"}}, ["l1"])
    assert out["l1"] == {"state": "on"}  # keep_count(1) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest custom_components/area_lighting/tests/test_demand_response.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.area_lighting.demand_response'`.

- [ ] **Step 3: Write minimal implementation**

Create `custom_components/area_lighting/demand_response.py`:

```python
"""Pure demand-response shedding policy (HA-free, unit-testable).

While the global demand-response flag is active, each lighting activation
sheds a fraction of the bulbs it would turn ON:

  - n <= 5 on-bulbs  -> shed 50%
  - n >= 6 on-bulbs  -> shed 80%
  - keep = ceil(n * (1 - ratio)); at least one bulb survives when n >= 1.

Bulbs are shed from the config-order TAIL of the on-set (first-declared
bulbs survive). The shed universe is an area's individual lights, in config
order; Hue-Zone clusters are excluded (they address the same physical bulbs).

Imports nothing from homeassistant.* so it can be unit-tested against many
input shapes quickly (mirrors cluster_dispatch.py).
"""

from __future__ import annotations

from math import ceil


def keep_count(n: int) -> int:
    """Number of on-bulbs to keep for an on-set of size n."""
    if n <= 0:
        return 0
    ratio = 0.50 if n <= 5 else 0.80
    return ceil(n * (1 - ratio))


def demand_response_shed_ids(
    ordered_light_ids: list[str],
    on_ids: list[str],
) -> list[str]:
    """Return the entity_ids to shed: the config-order tail of the on-set.

    ordered_light_ids: the area's individual lights in config order.
    on_ids: the entity_ids the activation would turn ON.
    """
    on_set = set(on_ids)
    ordered_on = [eid for eid in ordered_light_ids if eid in on_set]
    return ordered_on[keep_count(len(ordered_on)) :]


def apply_demand_response(
    targets: dict[str, dict],
    ordered_light_ids: list[str],
) -> dict[str, dict]:
    """Return a copy of `targets` with the shed tail forced to off.

    Only entities present in `ordered_light_ids` are eligible to shed. Shed
    entries are replaced with a fresh {"state": "off"} dict, so the caller's
    original per-light state dicts are never mutated.
    """
    ordered_on = [
        eid for eid in ordered_light_ids if targets.get(eid, {}).get("state") == "on"
    ]
    shed = ordered_on[keep_count(len(ordered_on)) :]
    if not shed:
        return targets
    out = dict(targets)
    for eid in shed:
        out[eid] = {"state": "off"}
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest custom_components/area_lighting/tests/test_demand_response.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add custom_components/area_lighting/demand_response.py custom_components/area_lighting/tests/test_demand_response.py
git commit -m "(Patch) area_lighting: add pure demand-response shedding policy"
```

---

### Task 2: Persist the global demand-response flag

**Files:**
- Modify: `custom_components/area_lighting/global_state.py`
- Test: `custom_components/area_lighting/tests/test_global_toggles.py` (append + update one existing test)

**Interfaces:**
- Produces on `GlobalToggles`:
  - property `demand_response_active -> bool` (default `False`)
  - `state_dict()` gains key `"demand_response_active"`
  - `load_persisted_state()` reads `"demand_response_active"`
- (The setter with side-effects is added in Task 6.)

- [ ] **Step 1: Write the failing tests**

Append to `custom_components/area_lighting/tests/test_global_toggles.py`:

```python
def test_demand_response_defaults_off():
    t = GlobalToggles(_FakeHass(), _FakeStorage())
    assert t.demand_response_active is False


def test_demand_response_load_persisted():
    t = GlobalToggles(_FakeHass(), _FakeStorage())
    t.load_persisted_state({"demand_response_active": True})
    assert t.demand_response_active is True
```

Then UPDATE the existing `test_state_dict_shape` (it asserts an exact dict and will break) to include the new key:

```python
def test_state_dict_shape():
    t = GlobalToggles(_FakeHass(), _FakeStorage())
    t.load_persisted_state({"motion_lights_enabled": False})
    assert t.state_dict() == {
        "motion_lights_enabled": False,
        "occupancy_timeout_enabled": True,
        "demand_response_active": False,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest custom_components/area_lighting/tests/test_global_toggles.py -v`
Expected: FAIL — `test_demand_response_defaults_off` errors (`AttributeError: ... demand_response_active`), and `test_state_dict_shape` fails on the dict mismatch.

- [ ] **Step 3: Write minimal implementation**

In `global_state.py`, add the field in `__init__` (after `self._occupancy_timeout_enabled = True`):

```python
        self._demand_response_active = False
```

Add the property (after the `occupancy_timeout_enabled` property):

```python
    @property
    def demand_response_active(self) -> bool:
        return self._demand_response_active
```

In `state_dict()`, add the key:

```python
    def state_dict(self) -> dict:
        return {
            "motion_lights_enabled": self._motion_lights_enabled,
            "occupancy_timeout_enabled": self._occupancy_timeout_enabled,
            "demand_response_active": self._demand_response_active,
        }
```

In `load_persisted_state()`, add (after the `occupancy_timeout_enabled` block):

```python
        if "demand_response_active" in data:
            self._demand_response_active = bool(data["demand_response_active"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest custom_components/area_lighting/tests/test_global_toggles.py -v`
Expected: PASS (all, including the updated `test_state_dict_shape`).

- [ ] **Step 5: Commit**

```bash
git add custom_components/area_lighting/global_state.py custom_components/area_lighting/tests/test_global_toggles.py
git commit -m "(Patch) area_lighting: persist global demand-response flag"
```

---

### Task 3: Shed scene activations under demand response

**Files:**
- Modify: `custom_components/area_lighting/controller.py`
- Test: `custom_components/area_lighting/tests/integration/test_demand_response.py` (new)

**Interfaces:**
- Consumes: `apply_demand_response`, `demand_response_shed_ids` (Task 1); `GlobalToggles.demand_response_active` (Task 2).
- Produces on `AreaLightingController`:
  - `_demand_response_active() -> bool`
  - `_resolve_raw_scene_targets(scene_slug) -> dict[str, dict]` (renamed from `_resolve_scene_targets`)
  - `_effective_scene_targets(scene_slug) -> dict[str, dict]`
  - `_compute_scene_shed_ids(scene_slug) -> frozenset[str]`
  - `_dr_shed_ids: frozenset[str]` field + `dr_shed_ids -> frozenset[str]` property (used by Tasks 4, 5, 7)

- [ ] **Step 1: Write the failing tests**

Create `custom_components/area_lighting/tests/integration/test_demand_response.py`:

```python
"""Demand-response shedding: scene activations."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _bright_entities(n: int) -> dict:
    return {f"light.bright_room_{i}": {"state": "on", "brightness": 200} for i in range(1, n + 1)}


def _config(n_lights: int, scene_on: int) -> dict:
    """Area with `n_lights` bulbs and a 'bright' scene that turns `scene_on` on."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "bright_room",
                    "name": "Bright Room",
                    "event_handlers": True,
                    "lights": [
                        {"id": f"light.bright_room_{i}", "roles": ["dimming"]}
                        for i in range(1, n_lights + 1)
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "bright", "name": "Bright", "entities": _bright_entities(scene_on)},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict, n_lights: int) -> None:
    for i in range(1, n_lights + 1):
        hass.states.async_set(f"light.bright_room_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _on_off(service_calls):
    on = {c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"}
    off = {c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_off"}
    return on, off


@pytest.mark.integration
async def test_dr_sheds_six_light_scene_to_keep_two(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert on == {"light.bright_room_1", "light.bright_room_2"}
    assert {f"light.bright_room_{i}" for i in (3, 4, 5, 6)} <= off
    # Tracking marks shed bulbs off so self-heal / manual-detection leave them.
    assert ctrl._active_scene_targets["light.bright_room_4"]["state"] == "off"
    assert ctrl.dr_shed_ids == frozenset(
        {f"light.bright_room_{i}" for i in (3, 4, 5, 6)}
    )


@pytest.mark.integration
async def test_dr_sheds_two_light_scene_to_keep_one(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    # 25-bulb area, scene only turns on 2 -> n=2 -> keep 1.
    await _setup(hass, _config(25, 2), 25)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, off = _on_off(service_calls)
    assert on == {"light.bright_room_1"}
    assert "light.bright_room_2" in off


@pytest.mark.integration
async def test_no_shed_when_dr_inactive(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]

    service_calls.clear()
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    on, _ = _on_off(service_calls)
    assert on == {f"light.bright_room_{i}" for i in range(1, 7)}
    assert ctrl.dr_shed_ids == frozenset()


@pytest.mark.integration
async def test_external_scene_tracking_is_filtered(
    hass: HomeAssistant, helper_entities
) -> None:
    # handle_scene_activated tracks external scene.turn_on; under DR the
    # tracked targets must mark shed bulbs off (Task 8 filters the apply).
    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True

    await ctrl.handle_scene_activated("bright")
    await hass.async_block_till_done()

    assert ctrl._active_scene_targets["light.bright_room_1"]["state"] == "on"
    assert ctrl._active_scene_targets["light.bright_room_6"]["state"] == "off"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_demand_response.py -v`
Expected: FAIL — `AttributeError: 'AreaLightingController' object has no attribute 'dr_shed_ids'`, and shed counts wrong.

- [ ] **Step 3: Implement**

In `controller.py`:

3a. Add the import near the other local imports at the top of the module:

```python
from .demand_response import apply_demand_response, demand_response_shed_ids
```

3b. In `__init__`, next to `self._active_scene_targets: dict[str, dict] = {}` add:

```python
        # Entity ids of individual lights the current activation shed for
        # demand response (empty when DR is inactive). Read by the kelvin
        # router and the DR reconcile; surfaced in diagnostics.
        self._dr_shed_ids: frozenset[str] = frozenset()
```

3c. Add these methods (near `_resolve_scene_targets`):

```python
    def _demand_response_active(self) -> bool:
        toggles = self.hass.data.get(DOMAIN, {}).get("global")
        return toggles is not None and toggles.demand_response_active

    @property
    def dr_shed_ids(self) -> frozenset[str]:
        """Individual lights the current activation shed for demand response."""
        return self._dr_shed_ids

    def _effective_scene_targets(self, scene_slug: str) -> dict[str, dict]:
        """Raw scene targets, with the demand-response shed filter applied
        when the global DR flag is active. Shed bulbs carry an off-target so
        manual detection and self-heal treat them as intended-off."""
        targets = self._resolve_raw_scene_targets(scene_slug)
        if self._demand_response_active():
            targets = apply_demand_response(targets, [light.id for light in self.area.lights])
        return targets

    def _compute_scene_shed_ids(self, scene_slug: str) -> frozenset[str]:
        if not self._demand_response_active():
            return frozenset()
        ordered = [light.id for light in self.area.lights]
        raw = self._resolve_raw_scene_targets(scene_slug)
        on_ids = [eid for eid in ordered if raw.get(eid, {}).get("state") == "on"]
        return frozenset(demand_response_shed_ids(ordered, on_ids))
```

3d. Rename the existing method `def _resolve_scene_targets(` to `def _resolve_raw_scene_targets(` (body unchanged).

3e. In `_activate_scene`, the visual-scene branch, replace the target-resolution line and add the shed record. Change:

```python
        await self._disable_circadian_switches()
        self._active_scene_targets = self._resolve_scene_targets(scene_slug)
        self._stamp_targets_with_command_metadata(transition)
```

to:

```python
        await self._disable_circadian_switches()
        self._active_scene_targets = self._effective_scene_targets(scene_slug)
        self._dr_shed_ids = self._compute_scene_shed_ids(scene_slug)
        self._stamp_targets_with_command_metadata(transition)
```

3f. In `_activate_scene`, the `SCENE_OFF_INTERNAL` branch, next to `self._active_scene_targets = {}` add:

```python
            self._dr_shed_ids = frozenset()
```

3g. In `handle_scene_activated`, replace `self._resolve_scene_targets(scene_slug)` with `self._effective_scene_targets(scene_slug)` (the single occurrence in the `else` branch).

3h. In `handle_lights_all_off`, next to `self._active_scene_targets = {}` add:

```python
        self._dr_shed_ids = frozenset()
```

3i. In `_apply_scene_data`, inject the filter into both branches. In the snapshot branch, right after `light_entities = { ... }`, add:

```python
            if self._demand_response_active():
                light_entities = apply_demand_response(
                    light_entities, [light.id for light in self.area.lights]
                )
```

Replace the `else:` (skeleton) branch body with:

```python
        else:
            # No snapshot -> role-based on/off. Under DR, shed the on-set tail
            # and skip cluster entities (a cluster command would turn on shed
            # members through the zone).
            dr = self._demand_response_active()
            shed: set[str] = set()
            if dr:
                ordered = [light.id for light in self.area.lights]
                on_ids = [light.id for light in self.area.lights if light.in_scene(scene_slug)]
                shed = set(demand_response_shed_ids(ordered, on_ids))
            tasks = []
            for light in self.area.all_lights:
                if dr and light.is_cluster:
                    continue
                svc_data: dict[str, Any] = {"entity_id": light.id}
                if transition is not None:
                    svc_data["transition"] = int(transition)
                if light.in_scene(scene_slug) and light.id not in shed:
                    tasks.append(self._call_service("light.turn_on", **svc_data))
                else:
                    tasks.append(self._call_service("light.turn_off", **svc_data))
            if tasks:
                await asyncio.gather(*tasks)
```

3j. Update the docstring reference in `_apply_scene_data` (and the `_stamp_targets_with_command_metadata` docstring) that mentions `_resolve_scene_targets` to say `_resolve_raw_scene_targets`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_demand_response.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Full gate + commit**

```bash
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto; cd -
git add custom_components/area_lighting/controller.py custom_components/area_lighting/tests/integration/test_demand_response.py
git commit -m "(Minor) area_lighting: shed scene activations under demand response"
```

Expected: full suite green (no regressions from the `_resolve_scene_targets` rename or skeleton change).

---

### Task 4: Shed circadian and dark bring-up

**Files:**
- Modify: `custom_components/area_lighting/controller.py`
- Test: `custom_components/area_lighting/tests/integration/test_demand_response_circadian.py` (new)

**Interfaces:**
- Consumes: `demand_response_shed_ids`, `_demand_response_active`, `_dr_shed_ids` (Task 3).
- Produces on `AreaLightingController`:
  - `_circadian_on_ids() -> list[str]`
  - `_compute_circadian_shed_ids() -> frozenset[str]`
  - `_activate_circadian` and `_set_all_lights_to_pct` now honor the shed set.

- [ ] **Step 1: Write the failing tests**

Create `custom_components/area_lighting/tests/integration/test_demand_response_circadian.py`:

```python
"""Demand-response shedding: circadian activation and dark bring-up."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _config() -> dict:
    # 6 lights, all bound to one circadian switch -> circadian on-set = 6.
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "study",
                    "name": "Study",
                    "event_handlers": True,
                    "circadian_switches": [
                        {"name": "Main", "max_brightness": 100, "min_brightness": 40},
                    ],
                    "lights": [
                        {
                            "id": f"light.study_{i}",
                            "circadian_switch": "Main",
                            "circadian_type": "ct",
                            "roles": ["dimming"],
                        }
                        for i in range(1, 7)
                    ],
                    "scenes": [{"id": "circadian", "name": "Circadian"}, {"id": "off", "name": "Off"}],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    for i in range(1, 7):
        hass.states.async_set(f"light.study_{i}", "off", {})
    hass.states.async_set(
        "switch.circadian_lighting_study_main_circadian", "on", {"brightness": 80.0, "colortemp": 3500}
    )
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_circadian_sheds_to_keep_two(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    on = {c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"}
    off = {c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_off"}
    assert on == {"light.study_1", "light.study_2"}
    assert {f"light.study_{i}" for i in (3, 4, 5, 6)} <= off
    assert ctrl.dr_shed_ids == frozenset({f"light.study_{i}" for i in (3, 4, 5, 6)})


@pytest.mark.integration
async def test_dark_bring_up_sheds(hass: HomeAssistant, helper_entities, service_calls) -> None:
    # raise from a fully-dark area brings all lights to min; under DR only the
    # kept lights come up.
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._set_all_lights_to_pct(12)
    await hass.async_block_till_done()

    on = {c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"}
    assert on == {"light.study_1", "light.study_2"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_demand_response_circadian.py -v`
Expected: FAIL — all 6 lights turned on (no shedding yet).

- [ ] **Step 3: Implement**

In `controller.py`:

3a. Add helpers (near `_activate_circadian`):

```python
    def _circadian_on_ids(self) -> list[str]:
        """Individual lights (config order) that circadian activation turns on:
        route lights (driven by the kelvin router) plus lights bound to a
        circadian switch."""
        routes = self.area.circadian_kelvin_routes
        route_lights = routes.all_route_lights if routes else set()
        return [
            light.id
            for light in self.area.lights
            if light.id in route_lights or light.circadian_switch
        ]

    def _compute_circadian_shed_ids(self) -> frozenset[str]:
        if not self._demand_response_active():
            return frozenset()
        ordered = [light.id for light in self.area.lights]
        return frozenset(demand_response_shed_ids(ordered, self._circadian_on_ids()))
```

3b. In `_activate_circadian`, record the shed set right after `self._state.transition_to_circadian(source)`:

```python
        self._state.transition_to_circadian(source)
        self._dr_shed_ids = self._compute_circadian_shed_ids()
```

Then in the light loop, after the route-lights `continue` block and before the `if not light.circadian_switch:` check, add a shed branch that turns the bulb off:

```python
            if light.id in self._dr_shed_ids:
                # Shed for demand response: force off (route-light shedding is
                # handled by the kelvin router, which reads dr_shed_ids).
                tasks.append(self._call_service("light.turn_off", entity_id=light.id))
                continue
```

3c. In `_set_all_lights_to_pct`, restrict the target list under DR:

```python
    async def _set_all_lights_to_pct(self, pct: int) -> None:
        """Turn on EVERY light in the area at an absolute brightness percentage.

        Under demand response, only the kept individual lights come up (cluster
        entities are skipped so shed members are not lit through a zone).
        """
        brightness = max(1, min(255, round(255 * pct / 100)))
        if self._demand_response_active():
            ordered = [light.id for light in self.area.lights]
            shed = set(demand_response_shed_ids(ordered, ordered))
            entity_ids = [eid for eid in ordered if eid not in shed]
        else:
            entity_ids = [light.id for light in self.area.all_lights]
        if entity_ids:
            await asyncio.gather(
                *[
                    self._call_service("light.turn_on", entity_id=eid, brightness=brightness)
                    for eid in entity_ids
                ]
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_demand_response_circadian.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Full gate + commit**

```bash
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto; cd -
git add custom_components/area_lighting/controller.py custom_components/area_lighting/tests/integration/test_demand_response_circadian.py
git commit -m "(Patch) area_lighting: shed circadian and dark bring-up under demand response"
```

---

### Task 5: Reconcile areas when the flag flips

**Files:**
- Modify: `custom_components/area_lighting/controller.py`
- Test: `custom_components/area_lighting/tests/integration/test_demand_response_reconcile.py` (new)

**Interfaces:**
- Consumes: `_effective_scene_targets`, `_resolve_raw_scene_targets`, `_compute_scene_shed_ids`, `_activate_circadian`, `_apply_light_state`, `_stamp_targets_with_command_metadata` (Tasks 3, 4).
- Produces: `async_reconcile_demand_response() -> None` (called by the Task 6 setter).

- [ ] **Step 1: Write the failing tests**

Create `custom_components/area_lighting/tests/integration/test_demand_response_reconcile.py`:

```python
"""Demand-response edge reconcile: already-lit areas on flag flip."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _config() -> dict:
    entities = {f"light.den_{i}": {"state": "on", "brightness": 200} for i in range(1, 7)}
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "den",
                    "name": "Den",
                    "event_handlers": True,
                    "lights": [{"id": f"light.den_{i}", "roles": ["dimming"]} for i in range(1, 7)],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "bright", "name": "Bright", "entities": entities},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_reconcile_sheds_already_lit_kept_untouched(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    # Lights are physically on (simulate the scene being active pre-DR).
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    _toggles(hass)._demand_response_active = True
    service_calls.clear()
    await ctrl.async_reconcile_demand_response()
    await hass.async_block_till_done()

    off = {c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_off"}
    on = {c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"}
    assert off == {f"light.den_{i}" for i in (3, 4, 5, 6)}  # shed tail turned off
    assert on == set()  # kept bulbs already on -> untouched


@pytest.mark.integration
async def test_reconcile_restores_on_clear(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    _toggles(hass)._demand_response_active = True
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()
    # Kept on, shed off (as DR produced).
    for i in (1, 2):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    for i in (3, 4, 5, 6):
        hass.states.async_set(f"light.den_{i}", "off", {})

    _toggles(hass)._demand_response_active = False
    service_calls.clear()
    await ctrl.async_reconcile_demand_response()
    await hass.async_block_till_done()

    on = {c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"}
    assert on == {f"light.den_{i}" for i in (3, 4, 5, 6)}  # shed bulbs restored


@pytest.mark.integration
async def test_reconcile_skips_manual_and_off(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    ctrl._state.transition_to_manual()
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl.async_reconcile_demand_response()
    await hass.async_block_till_done()

    assert len(service_calls) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_demand_response_reconcile.py -v`
Expected: FAIL — `AttributeError: ... async_reconcile_demand_response`.

- [ ] **Step 3: Implement**

Add to `controller.py` (near the other demand-response methods):

```python
    async def async_reconcile_demand_response(self) -> None:
        """Re-evaluate shed bulbs after the global demand-response flag flipped.

        Idempotent ON/OFF-only converge (mirrors the kelvin router's diff loop):
        shed bulbs are turned off, previously shed bulbs are turned back on to
        their scene target, and bulbs already at the correct polarity (kept
        bulbs that are on) are left untouched so a manual dim level survives the
        flip. Manual and off areas are skipped. Called for every controller by
        the global demand-response setter.
        """
        if self._state.is_off or self._state.is_manual:
            return
        if self._state.is_circadian:
            # Circadian values are computed; re-running recomputes the same kept
            # values (no visible change) and applies/removes the shed filter.
            await self._activate_circadian(self._state.source)
            return
        if not self._state.is_scene:
            return

        scene_slug = self._state.scene_slug
        raw = self._resolve_raw_scene_targets(scene_slug)
        effective = self._effective_scene_targets(scene_slug)
        self._active_scene_targets = effective
        self._dr_shed_ids = self._compute_scene_shed_ids(scene_slug)
        self._stamp_targets_with_command_metadata(None)

        tasks: list = []
        for entity_id, target in effective.items():
            st = self.hass.states.get(entity_id)
            is_on = st is not None and st.state == STATE_ON
            want_on = target.get("state") == "on"
            if want_on and not is_on:
                tasks.append(self._apply_light_state(entity_id, raw[entity_id]))
            elif not want_on and is_on:
                tasks.append(self._apply_light_state(entity_id, {"state": "off"}))
        if tasks:
            await asyncio.gather(*tasks)
        self._notify_state_change()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_demand_response_reconcile.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Full gate + commit**

```bash
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto; cd -
git add custom_components/area_lighting/controller.py custom_components/area_lighting/tests/integration/test_demand_response_reconcile.py
git commit -m "(Patch) area_lighting: reconcile areas on demand-response flag change"
```

---

### Task 6: Global master switch (setter + entity)

**Files:**
- Modify: `custom_components/area_lighting/global_state.py`, `custom_components/area_lighting/switch.py`
- Test: `custom_components/area_lighting/tests/test_global_toggles.py` (append), `custom_components/area_lighting/tests/integration/test_demand_response_switch.py` (new)

**Interfaces:**
- Consumes: `AreaLightingController.async_reconcile_demand_response` (Task 5).
- Produces: `GlobalToggles.async_set_demand_response_active(bool)`; owned entity `switch.area_lighting_demand_response_active`.

- [ ] **Step 1: Write the failing tests**

Append to `custom_components/area_lighting/tests/test_global_toggles.py` — first extend `_FakeController` (defined near the top) with an async reconcile counter, then add the fan-out test:

```python
# In _FakeController.__init__, add:  self.reconciled = 0
# and the method:
#     async def async_reconcile_demand_response(self) -> None:
#         self.reconciled += 1


@pytest.mark.asyncio
async def test_set_demand_response_fans_out_and_persists():
    hass, storage = _FakeHass(), _FakeStorage()
    c1, c2 = _FakeController(), _FakeController()
    hass.data[DOMAIN] = {"controllers": {"a": c1, "b": c2}}
    t = GlobalToggles(hass, storage)

    await t.async_set_demand_response_active(True)
    await hass.drain()

    assert t.demand_response_active is True
    assert (c1.reconciled, c2.reconciled) == (1, 1)
    assert storage.saved[-1]["demand_response_active"] is True


@pytest.mark.asyncio
async def test_set_demand_response_idempotent():
    hass, storage = _FakeHass(), _FakeStorage()
    c1 = _FakeController()
    hass.data[DOMAIN] = {"controllers": {"a": c1}}
    t = GlobalToggles(hass, storage)

    await t.async_set_demand_response_active(False)  # already False
    await hass.drain()

    assert c1.reconciled == 0
    assert storage.saved == []
```

Update `_FakeController` at the top of the file:

```python
class _FakeController:
    def __init__(self) -> None:
        self.enforced = 0
        self.cancelled = 0
        self.reconciled = 0

    def enforce_occupancy_timer(self) -> None:
        self.enforced += 1

    def cancel_occupancy_timer(self) -> None:
        self.cancelled += 1

    async def async_reconcile_demand_response(self) -> None:
        self.reconciled += 1
```

Create `custom_components/area_lighting/tests/integration/test_demand_response_switch.py`:

```python
"""Demand-response owned master switch: registration and end-to-end."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


def _config() -> dict:
    entities = {f"light.loft_{i}": {"state": "on", "brightness": 200} for i in range(1, 7)}
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "loft",
                    "name": "Loft",
                    "event_handlers": True,
                    "lights": [{"id": f"light.loft_{i}", "roles": ["dimming"]} for i in range(1, 7)],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "bright", "name": "Bright", "entities": entities},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    for i in range(1, 7):
        hass.states.async_set(f"light.loft_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_switch_registered_default_off_with_icon(
    hass: HomeAssistant, helper_entities
) -> None:
    await _setup(hass, _config())
    st = hass.states.get("switch.area_lighting_demand_response_active")
    assert st is not None
    assert st.state == "off"
    assert st.attributes["friendly_name"] == "Area Lighting Demand Response (Global)"
    assert st.attributes["icon"] == "mdi:transmission-tower"


@pytest.mark.integration
async def test_switch_service_call_sheds_then_restores(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    from custom_components.area_lighting.area_state import ActivationSource

    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["loft"]
    for i in range(1, 7):
        hass.states.async_set(f"light.loft_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    service_calls.clear()
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.area_lighting_demand_response_active"}, blocking=True
    )
    await hass.async_block_till_done()
    off = {c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_off"}
    assert off == {f"light.loft_{i}" for i in (3, 4, 5, 6)}

    # Simulate the shed bulbs now being physically off, then clear DR.
    for i in (3, 4, 5, 6):
        hass.states.async_set(f"light.loft_{i}", "off", {})
    service_calls.clear()
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.area_lighting_demand_response_active"}, blocking=True
    )
    await hass.async_block_till_done()
    on = {c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"}
    assert on == {f"light.loft_{i}" for i in (3, 4, 5, 6)}


@pytest.mark.integration
async def test_demand_response_flag_persists_across_reload(
    hass: HomeAssistant, helper_entities
) -> None:
    await _setup(hass, _config())
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.area_lighting_demand_response_active"}, blocking=True
    )
    await hass.async_block_till_done()
    persisted = hass.data["area_lighting"]["state_storage"].get_global_state()
    assert persisted["demand_response_active"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest custom_components/area_lighting/tests/test_global_toggles.py custom_components/area_lighting/tests/integration/test_demand_response_switch.py -v`
Expected: FAIL — `async_set_demand_response_active` missing; switch entity not found.

- [ ] **Step 3: Implement**

3a. In `global_state.py`, add the setter (after `async_set_occupancy_timeout_enabled`):

```python
    async def async_set_demand_response_active(self, enabled: bool) -> None:
        if self._demand_response_active == enabled:
            return
        self._demand_response_active = enabled
        controllers = self._hass.data.get(DOMAIN, {}).get("controllers", {})
        for ctrl in controllers.values():
            self._hass.async_create_task(ctrl.async_reconcile_demand_response())
        self._notify()
        self._schedule_save()
```

3b. In `switch.py`, add a row to `GLOBAL_SWITCH_DEFS`:

```python
    (
        "demand_response_active",
        "Area Lighting Demand Response (Global)",
        "mdi:transmission-tower",
        "area_lighting_global_demand_response_active",
        "switch.area_lighting_demand_response_active",
    ),
```

3c. In `switch.py`, `AreaLightingGlobalSwitch._set`, route the new flag:

```python
    async def _set(self, value: bool) -> None:
        if self._flag == "motion_lights_enabled":
            await self._toggles.async_set_motion_lights_enabled(value)
        elif self._flag == "demand_response_active":
            await self._toggles.async_set_demand_response_active(value)
        else:
            await self._toggles.async_set_occupancy_timeout_enabled(value)
```

3d. No further wiring is needed: `__init__.py:337-338` already loops over `GLOBAL_SWITCH_DEFS` (`for flag, name, icon, uid, eid in GLOBAL_SWITCH_DEFS: switches.append(AreaLightingGlobalSwitch(toggles, flag, name, icon, uid, eid))`), so the new row is registered automatically.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest custom_components/area_lighting/tests/test_global_toggles.py custom_components/area_lighting/tests/integration/test_demand_response_switch.py -v`
Expected: PASS.

- [ ] **Step 5: Full gate + commit**

```bash
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto; cd -
git add custom_components/area_lighting/global_state.py custom_components/area_lighting/switch.py custom_components/area_lighting/tests/
git commit -m "(Minor) area_lighting: add demand response global master switch"
```

---

### Task 7: Kelvin router honors the shed set

**Files:**
- Modify: `custom_components/area_lighting/circadian_kelvin_router.py`
- Test: `custom_components/area_lighting/tests/integration/test_demand_response_kelvin.py` (new)

**Interfaces:**
- Consumes: `AreaLightingController.dr_shed_ids` (Task 3).

- [ ] **Step 1: Write the failing test**

The kitchen routes fixture below mirrors `tests/integration/test_circadian_kelvin_routes.py`: four route lights (one fluorescent banded `[4500, 5500]`, three strips as the fallback route), all bound to one circadian switch. The router's colortemp source defaults to that circadian switch, read from its `colortemp` attribute. At colortemp 3000 the fallback route (all three strips) is active. The circadian on-set is the 4 route lights, so under DR `n=4 -> keep 2 -> shed [strip_2, strip_3]`.

Create `custom_components/area_lighting/tests/integration/test_demand_response_kelvin.py`:

```python
"""Demand response: the kelvin router never lights a shed route bulb."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.global_state import GlobalToggles

_SWITCH = "switch.circadian_lighting_kitchen_kitchen_circadian"


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _kitchen_routes_config() -> dict:
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "event_handlers": True,
                    "circadian_switches": [
                        {"name": "Kitchen", "max_brightness": 100, "min_brightness": 20},
                    ],
                    "lights": [
                        {"id": "light.kitchen_fluorescent", "circadian_switch": "Kitchen", "circadian_type": "ct"},
                        {"id": "light.kitchen_strip_1", "circadian_switch": "Kitchen", "circadian_type": "ct"},
                        {"id": "light.kitchen_strip_2", "circadian_switch": "Kitchen", "circadian_type": "ct"},
                        {"id": "light.kitchen_strip_3", "circadian_switch": "Kitchen", "circadian_type": "ct"},
                    ],
                    "scenes": [{"id": "circadian", "name": "Circadian"}, {"id": "off", "name": "Off"}],
                    "circadian_kelvin_routes": {
                        "crossfade_seconds": 1.0,
                        "routes": [
                            {"kelvin_range": [4500, 5500], "lights": ["light.kitchen_fluorescent"]},
                            {
                                "lights": [
                                    "light.kitchen_strip_1",
                                    "light.kitchen_strip_2",
                                    "light.kitchen_strip_3",
                                ]
                            },
                        ],
                    },
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, colortemp: int) -> None:
    for eid in (
        "light.kitchen_fluorescent",
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    ):
        hass.states.async_set(eid, "off", {})
    hass.states.async_set(_SWITCH, "on", {"brightness": 75.0, "colortemp": colortemp})
    assert await async_setup_component(hass, "area_lighting", _kitchen_routes_config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_router_never_lights_shed_route_bulbs(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, colortemp=3000)  # fallback route (all 3 strips) active
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    assert ctrl.dr_shed_ids == frozenset(
        {"light.kitchen_strip_2", "light.kitchen_strip_3"}
    )
    on = {
        c.data.get("entity_id")
        for c in service_calls
        if c.domain == "light" and c.service == "turn_on"
    }
    assert "light.kitchen_strip_1" in on
    assert on.isdisjoint({"light.kitchen_strip_2", "light.kitchen_strip_3"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_demand_response_kelvin.py -v`
Expected: FAIL — the router lights a shed route bulb.

- [ ] **Step 3: Implement**

In `circadian_kelvin_router.py`:

3a. Add the import at the top:

```python
from .const import DOMAIN
```

3b. Add a helper on `CircadianKelvinRouter`:

```python
    def _demand_response_shed_ids(self) -> set[str]:
        controllers = self._hass.data.get(DOMAIN, {}).get("controllers", {})
        ctrl = controllers.get(self._area_id)
        return set(ctrl.dr_shed_ids) if ctrl is not None else set()
```

3c. In `_reconcile`, subtract the shed set from the active lights so shed route-bulbs are treated as inactive (turned off, never on). Replace:

```python
            active = self._config.routes[new_index]
            inactive_lights = self._config.all_route_lights - set(active.lights)
```

with:

```python
            active = self._config.routes[new_index]
            shed = self._demand_response_shed_ids()
            active_lights = set(active.lights) - shed
            inactive_lights = self._config.all_route_lights - active_lights
```

and update the two comprehensions to use `active_lights` instead of `active.lights`:

```python
            on_calls_to_issue = [
                eid
                for eid in sorted(active_lights)
                if (s := self._hass.states.get(eid)) is None or s.state != "on"
            ]
```

(the `off_calls_to_issue` comprehension already reads `inactive_lights`, which now includes the shed bulbs).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_demand_response_kelvin.py -v`
Expected: PASS.

- [ ] **Step 5: Full gate + commit**

```bash
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto; cd -
git add custom_components/area_lighting/circadian_kelvin_router.py custom_components/area_lighting/tests/integration/test_demand_response_kelvin.py
git commit -m "(Patch) area_lighting: make kelvin router honor demand-response shedding"
```

---

### Task 8: Filter the external HA Scene entity

**Files:**
- Modify: `custom_components/area_lighting/scene.py`
- Test: `custom_components/area_lighting/tests/integration/test_demand_response_scene_entity.py` (new)

**Interfaces:**
- Consumes: `apply_demand_response`, `demand_response_shed_ids` (Task 1); `GlobalToggles.demand_response_active` (Task 2).

- [ ] **Step 1: Write the failing test**

Create `custom_components/area_lighting/tests/integration/test_demand_response_scene_entity.py`:

```python
"""Demand response filters the HA Scene entity (external scene.turn_on)."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _config() -> dict:
    entities = {f"light.gallery_{i}": {"state": "on", "brightness": 200} for i in range(1, 7)}
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "gallery",
                    "name": "Gallery",
                    "event_handlers": True,
                    "lights": [{"id": f"light.gallery_{i}", "roles": ["dimming"]} for i in range(1, 7)],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "bright", "name": "Bright", "entities": entities},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    for i in range(1, 7):
        hass.states.async_set(f"light.gallery_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_scene_turn_on_is_filtered_under_dr(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.gallery_bright"}, blocking=True
    )
    await hass.async_block_till_done()

    on = {c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"}
    assert on == {"light.gallery_1", "light.gallery_2"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_demand_response_scene_entity.py -v`
Expected: FAIL — all 6 lights turned on.

- [ ] **Step 3: Implement**

In `scene.py`, add a helper on `AreaLightingScene` and call it from both apply paths.

3a. Add the helper method:

```python
    def _demand_response_active(self) -> bool:
        toggles = self.hass.data.get(DOMAIN, {}).get("global")
        return toggles is not None and toggles.demand_response_active
```

3b. In `_apply_stored`, filter the target dict at the top of the method (before the loop):

```python
    async def _apply_stored(self, stored, transition):
        if self._demand_response_active():
            from .demand_response import apply_demand_response

            stored = apply_demand_response(stored, [light.id for light in self._area.lights])
        # ... existing loop over stored.items() ...
```

3c. In `_apply_skeleton`, shed the on-set tail and skip clusters. Replace the loop preamble and body:

```python
    async def _apply_skeleton(self, transition):
        scene_slug = self._scene_cfg.slug
        excluded = set(self._scene_cfg.group_exclude)
        dr = self._demand_response_active()
        shed: set[str] = set()
        if dr:
            from .demand_response import demand_response_shed_ids

            ordered = [light.id for light in self._area.lights]
            on_ids = [
                light.id
                for light in self._area.lights
                if light.in_scene(scene_slug) and light.id not in excluded
            ]
            shed = set(demand_response_shed_ids(ordered, on_ids))

        tasks: list = []
        for light in self._area.all_lights:
            if light.id in excluded:
                continue
            if dr and light.is_cluster:
                continue
            service_data: dict[str, Any] = {"entity_id": light.id}
            if transition is not None:
                service_data["transition"] = transition
            if light.in_scene(scene_slug) and light.id not in shed:
                tasks.append(
                    self.hass.services.async_call("light", "turn_on", service_data, blocking=True)
                )
            else:
                tasks.append(
                    self.hass.services.async_call("light", "turn_off", service_data, blocking=True)
                )
        if tasks:
            await asyncio.gather(*tasks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_demand_response_scene_entity.py -v`
Expected: PASS.

- [ ] **Step 5: Full gate + commit**

```bash
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto; cd -
git add custom_components/area_lighting/scene.py custom_components/area_lighting/tests/integration/test_demand_response_scene_entity.py
git commit -m "(Patch) area_lighting: filter external scene activations under demand response"
```

---

### Task 9: Diagnostics, alert-bypass guard, and docs

**Files:**
- Modify: `custom_components/area_lighting/controller.py` (diagnostics)
- Modify: `README.md`, `CHANGELOG.md` (if present)
- Test: `custom_components/area_lighting/tests/integration/test_demand_response.py` (append)

**Interfaces:**
- Consumes: `_demand_response_active`, `dr_shed_ids` (Task 3).

- [ ] **Step 1: Write the failing tests**

Append to `custom_components/area_lighting/tests/integration/test_demand_response.py`:

```python
@pytest.mark.integration
async def test_diagnostics_expose_demand_response(
    hass: HomeAssistant, helper_entities
) -> None:
    await _setup(hass, _config(6, 6), 6)
    ctrl = hass.data["area_lighting"]["controllers"]["bright_room"]
    _toggles(hass)._demand_response_active = True
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    snap = ctrl.diagnostic_snapshot()
    assert snap["demand_response_active"] is True
    assert set(snap["demand_response_shed"]) == {f"light.bright_room_{i}" for i in (3, 4, 5, 6)}


@pytest.mark.integration
async def test_alert_bypasses_demand_response(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    cfg = _config(6, 6)
    cfg["area_lighting"]["alert_patterns"] = {
        "flash": {"steps": [{"target": "all", "state": "on", "brightness": 255}], "restore": False}
    }
    await _setup(hass, cfg, 6)
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await hass.services.async_call(
        "area_lighting", "alert", {"area_id": "bright_room", "pattern": "flash"}, blocking=True
    )
    await hass.async_block_till_done()

    on = {c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"}
    # Alerts bypass DR: every bulb flashes, none are shed.
    assert on == {f"light.bright_room_{i}" for i in range(1, 7)}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_demand_response.py -v -k "diagnostics or alert"`
Expected: FAIL — `KeyError: 'demand_response_active'` in the snapshot; the alert test should already pass if alerts truly bypass (if it fails, that is a real defect to fix, not a test bug).

- [ ] **Step 3: Implement**

3a. In `controller.py` `diagnostic_snapshot`, add two keys to the returned dict (next to `"alert_active"`):

```python
            "demand_response_active": self._demand_response_active(),
            "demand_response_shed": sorted(self._dr_shed_ids),
```

3b. In `README.md`, add a row to the "Global master switches" table:

```markdown
| `switch.area_lighting_demand_response_active` | Master switch for demand-response load shedding |
```

3c. In `README.md`, add a new section after "Global master switches":

```markdown
## Demand response

When `switch.area_lighting_demand_response_active` is on (default off, persisted),
each area sheds a fraction of the bulbs any activation would turn on, reducing
load during a utility demand-response event:

- Up to 5 on-bulbs: shed 50%. 6 or more: shed 80%. At least one bulb always
  survives (`keep = ceil(n * (1 - ratio))`), where `n` is how many bulbs that
  specific activation would light.
- Bulbs are shed from the config-order tail of the on-set, so the first-declared
  lights in each area survive. Order your `lights` most-important-first.
- Off commands, alerts, and areas in `manual` are never affected. Flipping the
  switch immediately sheds already-lit non-manual areas; clearing it restores
  them.

Drive the switch from any utility integration or automation with
`switch.turn_on` / `switch.turn_off`.
```

3d. In `CHANGELOG.md`, add a bullet under the existing `## Unreleased` -> `### Added` section (do NOT add version numbers):

```markdown
- **Demand response** — a global master switch
  (`switch.area_lighting_demand_response_active`, default off) that, while on,
  sheds a per-activation fraction of each area's on-bulbs (50% for up to 5,
  80% for 6 or more), keeping the first-declared lights. Off commands, alerts,
  and `manual` areas are unaffected; non-manual areas restore when it clears.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_demand_response.py -v`
Expected: PASS (all, including the two new tests).

- [ ] **Step 5: Full gate + commit**

```bash
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto; cd -
git add custom_components/area_lighting/controller.py custom_components/area_lighting/tests/integration/test_demand_response.py README.md CHANGELOG.md
git commit -m "(Patch) area_lighting: expose demand-response diagnostics and docs"
```

---

## Final verification

- [ ] From the worktree root, run the full CI-equivalent gate and confirm zero failures:

```bash
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto
```

- [ ] Confirm no version files were touched:

```bash
git diff --name-only "$(git merge-base HEAD origin/main)"..HEAD | grep -E 'pyproject.toml|manifest.json|uv.lock' && echo "VERSION FILES CHANGED - REVERT" || echo "OK: no version files touched"
```
