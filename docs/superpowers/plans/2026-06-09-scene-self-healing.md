# Scene Self-Healing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `area_lighting` auto-heal out-of-band Hue glitches (re-assert the active scene target) instead of latching the area to `manual` and freezing a mismatched state, while still respecting genuine manual changes.

**Architecture:** Extend the existing event-driven manual-detection handler so an out-of-grace divergence is classified as *settling* (ignore), *glitch* (recovery from `unavailable`, or within `settle + 60s` of our own command → instant single-bulb re-assert), or *manual* (latch, today's behavior). Add a one-shot post-settle self-check per scene command to catch glitches that land during a fade, a loop cap that gives up after repeated failures (latch `manual` + raise a Repairs issue), and a global `scene_self_heal` kill-switch.

**Tech Stack:** Python 3.13, Home Assistant custom component, `voluptuous` config schema, `pytest` + `pytest-homeassistant-custom-component`, Dagger/`uv` for CI. Spec: `docs/superpowers/specs/2026-06-09-scene-self-healing-design.md`.

---

## Setup (run once in the worktree)

This plan is implemented in the worktree `/home/aaron/git/aaron/home-assistant-area-lighting-scene-self-healing` on branch `scene-self-healing`. The repo's `.venv` is gitignored and not shared into the worktree, so create one:

```sh
cd /home/aaron/git/aaron/home-assistant-area-lighting-scene-self-healing
uv sync
```

Fast local test/lint commands used throughout (run from the worktree root):

- Single test file: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py -v`
- Lint: `uv run ruff check custom_components/area_lighting && uv run ruff format --check custom_components/area_lighting`
- Types: `uv run mypy custom_components/area_lighting`
- CI-equivalent before final commit: `dagger call all`

**Commit subjects must start with `(Major)`/`(Minor)`/`(Patch)`** (the `commit-msg` hook + auto-versioning read these). This feature is a new capability → use `(Minor)`; test/docs-only commits → `(Patch)`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `custom_components/area_lighting/const.py` | constants | add heal window/cap constants + issue id |
| `custom_components/area_lighting/models.py` | config dataclasses | add `scene_self_heal` field to `AreaLightingConfig` |
| `custom_components/area_lighting/__init__.py` | root `CONFIG_SCHEMA` | accept `scene_self_heal` key |
| `custom_components/area_lighting/config_schema.py` | `parse_config` | thread `scene_self_heal` into the dataclass |
| `custom_components/area_lighting/controller.py` | per-area state machine | cache flag + property; `handle_scene_drift_reassert`; loop cap; Repairs raise/clear; post-settle self-check; diagnostics |
| `custom_components/area_lighting/event_handlers.py` | state-change routing | classify divergence → heal / manual |
| `custom_components/area_lighting/translations/en.json` + `strings.json` | UI strings | Repairs issue title/description |
| `custom_components/area_lighting/tests/integration/test_scene_self_healing.py` | tests | all new behavior + incident replay |
| `CHANGELOG.md`, `README.md`, `CONFIGURATION.md` | docs | document the feature + config key |

---

## Task 1: Constants + `scene_self_heal` config flag

**Files:**
- Modify: `custom_components/area_lighting/const.py` (after line 76)
- Modify: `custom_components/area_lighting/models.py:290-295`
- Modify: `custom_components/area_lighting/__init__.py:49-64`
- Modify: `custom_components/area_lighting/config_schema.py:470`
- Modify: `custom_components/area_lighting/controller.py` (`__init__` ~line 58; add property near the other gate properties)
- Test: `custom_components/area_lighting/tests/integration/test_scene_self_healing.py`

- [ ] **Step 1: Write the failing test**

Create `custom_components/area_lighting/tests/integration/test_scene_self_healing.py`:

```python
"""Glitch-aware scene self-healing.

Out-of-band Hue changes (power-on default, RF dropout, unavailable→on
recovery) should be auto-healed back to the active scene rather than
latching the area to `manual`. Genuine manual changes (long after our
last command, bulb reachable throughout) still latch `manual`.
"""

from __future__ import annotations

import time

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_scene_self_heal_enabled_default_true(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The flag defaults to on and is exposed on the controller."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl.scene_self_heal_enabled is True


@pytest.mark.integration
async def test_scene_self_heal_flag_false_disables(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Setting scene_self_heal: false in config disables the feature."""
    network_room_config["area_lighting"]["scene_self_heal"] = False
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl.scene_self_heal_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py -v`
Expected: FAIL — `AttributeError: 'AreaLightingController' object has no attribute 'scene_self_heal_enabled'` (and the `false` case rejected by `PREVENT_EXTRA` schema).

- [ ] **Step 3: Add constants**

In `const.py`, after the `MANUAL_DETECTION_GRACE_SECONDS = 4` line (line 76):

```python
# Scene self-healing (glitch-aware divergence handling)
SCENE_HEAL_WINDOW_SECONDS = 60           # tier-2 glitch window after settle
SCENE_HEAL_MAX_ATTEMPTS = 3              # heals per bulb per attempt window
SCENE_HEAL_ATTEMPT_WINDOW_SECONDS = 300  # rolling window for the loop cap
SCENE_DRIFT_ISSUE_ID = "scene_drift_unhealable"
```

- [ ] **Step 4: Add the dataclass field**

In `models.py`, `AreaLightingConfig` (line 290-295):

```python
@dataclass
class AreaLightingConfig:
    """Top-level configuration for the area_lighting integration."""

    areas: list[AreaConfig] = field(default_factory=list)
    alert_patterns: dict[str, AlertPattern] = field(default_factory=dict)
    scene_self_heal: bool = True
```

- [ ] **Step 5: Accept the key in the root schema**

In `__init__.py`, inside the inner `DOMAIN` schema (lines 53-58), add the optional key:

```python
                vol.Required("areas"): vol.All(cv.ensure_list, [AREA_SCHEMA]),
                vol.Optional("alert_patterns", default={}): {
                    cv.string: ALERT_PATTERN_SCHEMA,
                },
                vol.Optional("scene_self_heal", default=True): cv.boolean,
                # Ignored fields from templater.yaml kept for config compat
                vol.Optional("base_url"): str,
```

- [ ] **Step 6: Thread it through `parse_config`**

In `config_schema.py`, the return at line 470:

```python
    return AreaLightingConfig(
        areas=areas,
        alert_patterns=alert_patterns,
        scene_self_heal=raw.get("scene_self_heal", True),
    )
```

- [ ] **Step 7: Cache + expose on the controller**

In `controller.py` `__init__`, after `self._global_config = global_config` (line 58):

```python
        self._scene_self_heal: bool = global_config.scene_self_heal
```

Add a property next to the other gate properties (e.g. near the `motion_light_enabled` property; search for `def motion_light_enabled`):

```python
    @property
    def scene_self_heal_enabled(self) -> bool:
        return self._scene_self_heal
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py -v`
Expected: PASS (both tests).

- [ ] **Step 9: Commit**

```bash
git add custom_components/area_lighting/const.py custom_components/area_lighting/models.py \
        custom_components/area_lighting/__init__.py custom_components/area_lighting/config_schema.py \
        custom_components/area_lighting/controller.py \
        custom_components/area_lighting/tests/integration/test_scene_self_healing.py
git commit -m "(Minor) area_lighting: add scene_self_heal config flag"
```

---

## Task 2: `handle_scene_drift_reassert` + loop cap + Repairs issue

**Files:**
- Modify: `custom_components/area_lighting/controller.py` (`__init__` ~line 133; new methods; const import)
- Test: `custom_components/area_lighting/tests/integration/test_scene_self_healing.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_scene_self_healing.py`:

```python
def _on_target(commanded_offset: float = 0.0) -> dict:
    """A simple on-state scene target stamped `commanded_offset` seconds ago."""
    return {
        "state": "on",
        "brightness": 10,
        "color_temp_kelvin": 2700,
        "commanded_at": time.monotonic() - commanded_offset,
        "transition": 0.0,
    }


def _light_turn_on_calls(service_calls, entity_id: str) -> list:
    return [
        c for c in service_calls
        if c.domain == "light" and c.service == "turn_on"
        and c.data.get("entity_id") == entity_id
    ]


@pytest.mark.integration
async def test_reassert_reapplies_target_and_restamps(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """handle_scene_drift_reassert issues a turn_on with the target attrs."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    eid = "light.network_room_overhead_1"
    ctrl._active_scene_targets = {eid: _on_target(commanded_offset=200.0)}

    service_calls.clear()
    await ctrl.handle_scene_drift_reassert(eid, "glitch_window")
    await hass.async_block_till_done()

    calls = _light_turn_on_calls(service_calls, eid)
    assert len(calls) == 1
    assert calls[0].data.get("brightness") == 10
    assert calls[0].data.get("color_temp_kelvin") == 2700
    # commanded_at re-stamped to ~now so the heal doesn't self-trigger.
    assert time.monotonic() - ctrl._active_scene_targets[eid]["commanded_at"] < 1.0
    assert not ctrl._state.is_manual


@pytest.mark.integration
async def test_reassert_loop_cap_gives_up_and_raises_issue(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """After SCENE_HEAL_MAX_ATTEMPTS heals, give up: latch manual + Repairs issue."""
    from homeassistant.helpers import issue_registry as ir
    from custom_components.area_lighting.const import (
        DOMAIN,
        SCENE_DRIFT_ISSUE_ID,
        SCENE_HEAL_MAX_ATTEMPTS,
    )

    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    eid = "light.network_room_overhead_1"

    for _ in range(SCENE_HEAL_MAX_ATTEMPTS):
        ctrl._active_scene_targets = {eid: _on_target(commanded_offset=200.0)}
        await ctrl.handle_scene_drift_reassert(eid, "glitch_window")
        await hass.async_block_till_done()
    assert not ctrl._state.is_manual  # healed each time so far

    # One more → over the cap → give up.
    ctrl._active_scene_targets = {eid: _on_target(commanded_offset=200.0)}
    await ctrl.handle_scene_drift_reassert(eid, "glitch_window")
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, f"{SCENE_DRIFT_ISSUE_ID}_network_room") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py -k reassert -v`
Expected: FAIL — `AttributeError: ... has no attribute 'handle_scene_drift_reassert'`.

- [ ] **Step 3: Extend the const import in controller.py**

`controller.py` already imports from `.const` (e.g. `DOMAIN`). Add these three names to that existing `from .const import (` block, keeping it alphabetically ordered as the file does:

```python
    SCENE_DRIFT_ISSUE_ID,
    SCENE_HEAL_ATTEMPT_WINDOW_SECONDS,
    SCENE_HEAL_MAX_ATTEMPTS,
```

- [ ] **Step 4: Add heal state to `__init__`**

In `controller.py` `__init__`, right after `self._active_scene_targets: dict[str, dict] = {}` (line 133):

```python
        # Scene self-healing: monotonic timestamps of recent re-asserts per
        # entity (loop-cap window), and the pending post-settle self-check.
        self._heal_attempts: dict[str, list[float]] = {}
        self._heal_selfcheck_unsub = None
```

- [ ] **Step 5: Implement the heal method + Repairs helpers**

Add to `AreaLightingController` (place near `handle_manual_light_change`, ~line 1448):

```python
    async def handle_scene_drift_reassert(self, entity_id: str, reason: str) -> None:
        """Re-assert a single bulb's active scene target after a glitch.

        Subject to the loop cap: after SCENE_HEAL_MAX_ATTEMPTS heals within
        SCENE_HEAL_ATTEMPT_WINDOW_SECONDS, give up — latch manual and raise a
        Repairs issue instead of re-asserting again.
        """
        target = self._active_scene_targets.get(entity_id)
        if target is None or target.get("state") != "on":
            return

        now = time.monotonic()
        stamps = [
            t
            for t in self._heal_attempts.get(entity_id, [])
            if now - t < SCENE_HEAL_ATTEMPT_WINDOW_SECONDS
        ]
        if len(stamps) >= SCENE_HEAL_MAX_ATTEMPTS:
            _LOGGER.warning(
                "Area %s: scene-heal gave up on %s after %d attempts; latching manual",
                self.area.id,
                entity_id,
                len(stamps),
            )
            self._raise_scene_drift_issue(entity_id)
            await self.handle_manual_light_change()
            return

        stamps.append(now)
        self._heal_attempts[entity_id] = stamps
        _LOGGER.info(
            "Area %s: healed scene drift entity=%s reason=%s",
            self.area.id,
            entity_id,
            reason,
        )
        # Instant re-assert; re-stamp commanded_at so the heal command's own
        # state report falls inside the grace window and doesn't self-trigger.
        await self._apply_light_state(entity_id, target, transition=None)
        self._active_scene_targets[entity_id] = {**target, "commanded_at": now}

    def _raise_scene_drift_issue(self, entity_id: str) -> None:
        from homeassistant.helpers import issue_registry as ir

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{SCENE_DRIFT_ISSUE_ID}_{self.area.id}",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=SCENE_DRIFT_ISSUE_ID,
            translation_placeholders={"area": self.area.name, "entity": entity_id},
        )

    def _clear_scene_drift_issue(self) -> None:
        from homeassistant.helpers import issue_registry as ir

        ir.async_delete_issue(
            self.hass, DOMAIN, f"{SCENE_DRIFT_ISSUE_ID}_{self.area.id}"
        )
        self._heal_attempts.clear()
```

`_apply_light_state` (line 861) already builds the `light.turn_on` from the target's allowlisted attributes and skips the `transition` key when it's `None`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py -k reassert -v`
Expected: PASS (both).

- [ ] **Step 7: Commit**

```bash
git add custom_components/area_lighting/controller.py \
        custom_components/area_lighting/tests/integration/test_scene_self_healing.py
git commit -m "(Minor) area_lighting: re-assert scene target on detected glitch"
```

---

## Task 3: Classify divergence (heal vs manual) in the detection handler

**Files:**
- Modify: `custom_components/area_lighting/event_handlers.py:12` (imports), `:19-24` (const import), `:559-572` (handler tail)
- Test: `custom_components/area_lighting/tests/integration/test_scene_self_healing.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_scene_self_healing.py`:

```python
async def _activate_with_target(hass, ctrl, eid, commanded_offset, transition):
    """Put the area in the ambient scene with one on-target for `eid`,
    seeded as 'on' so a later async_set fires a state-change event."""
    hass.states.async_set(eid, "on", {"brightness": 10, "color_temp_kelvin": 2700})
    await hass.async_block_till_done()
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 120.0  # area grace gone
    ctrl._active_scene_targets = {
        eid: {
            "state": "on",
            "brightness": 10,
            "color_temp_kelvin": 2700,
            "commanded_at": time.monotonic() - commanded_offset,
            "transition": transition,
        }
    }


@pytest.mark.integration
async def test_divergence_inside_glitch_window_heals(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """A jump 110s after a 60s-fade command (settle+60=124s) → heal, not manual."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    eid = "light.network_room_overhead_1"
    await _activate_with_target(hass, ctrl, eid, commanded_offset=110.0, transition=60.0)

    service_calls.clear()
    hass.states.async_set(eid, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()

    assert not ctrl._state.is_manual
    assert _light_turn_on_calls(service_calls, eid), "expected a heal re-assert"


@pytest.mark.integration
async def test_divergence_after_glitch_window_marks_manual(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """A jump 200s after a 60s-fade command (> settle+60=124s) → manual latch."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    eid = "light.network_room_overhead_1"
    await _activate_with_target(hass, ctrl, eid, commanded_offset=200.0, transition=60.0)

    service_calls.clear()
    hass.states.async_set(eid, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert not _light_turn_on_calls(service_calls, eid), "must not heal a real manual change"


@pytest.mark.integration
async def test_recovery_from_unavailable_heals_regardless_of_window(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """unavailable→on at a divergent value, long after command → heal (tier 1)."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    eid = "light.network_room_overhead_1"
    await _activate_with_target(hass, ctrl, eid, commanded_offset=3600.0, transition=0.0)

    hass.states.async_set(eid, "unavailable", {})
    await hass.async_block_till_done()
    service_calls.clear()
    hass.states.async_set(eid, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()

    assert not ctrl._state.is_manual
    assert _light_turn_on_calls(service_calls, eid), "expected recovery heal"


@pytest.mark.integration
async def test_kill_switch_off_falls_back_to_manual(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """With scene_self_heal disabled, an in-window glitch latches manual."""
    network_room_config["area_lighting"]["scene_self_heal"] = False
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    eid = "light.network_room_overhead_1"
    await _activate_with_target(hass, ctrl, eid, commanded_offset=110.0, transition=60.0)

    service_calls.clear()
    hass.states.async_set(eid, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert not _light_turn_on_calls(service_calls, eid)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py -k "glitch_window or recovery or kill_switch" -v`
Expected: FAIL — currently every divergence latches `manual`, so `test_divergence_inside_glitch_window_heals` and `test_recovery_...` fail (`is_manual` True, no heal call).

- [ ] **Step 3: Extend imports in event_handlers.py**

Line 12 — add the two states:

```python
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
```

The `from .const import (` block (lines 19-24) — add the window constant:

```python
from .const import (
    DOMAIN,
    GLOBAL_MOTION_LIGHT_ENABLED_ENTITY,
    HOLIDAY_MODE_ENTITY,
    MANUAL_DETECTION_GRACE_SECONDS,
    SCENE_HEAL_WINDOW_SECONDS,
)
```

- [ ] **Step 4: Insert the classifier before the manual latch**

In `_make_manual_detection_handler`, the tail currently reads (lines 560-572):

```python
        # Compare against scene targets: if the light's current state
        # matches what the scene instructed, this is a late bridge
        # report or convergence, not a manual override.
        if ctrl.state_matches_scene_target(entity_id, new_state):
            _skip("matches scene target", entity_id)
            return

        _LOGGER.info(
            "Area %s: manual detection fired entity=%s",
            area_id,
            entity_id,
        )
        hass.async_create_task(ctrl.handle_manual_light_change())
```

Insert the classifier between the `matches scene target` block and the `_LOGGER.info(... manual detection fired ...)`:

```python
        # Past grace + transition windows AND diverges from the target.
        # Self-healing: classify as a glitch (auto-heal) vs a genuine manual
        # change. A glitch is either a recovery from unavailable/unknown, or a
        # sudden jump within SCENE_HEAL_WINDOW_SECONDS after this bulb's own
        # commanded settle point. Everything else is a real manual override.
        if ctrl.scene_self_heal_enabled:
            target = ctrl._active_scene_targets.get(entity_id)
            if target is not None:
                is_recovery = old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                in_glitch_window = False
                commanded_at = target.get("commanded_at")
                if commanded_at is not None:
                    transition = target.get("transition") or 0.0
                    age = _time.monotonic() - commanded_at
                    settle = transition + MANUAL_DETECTION_GRACE_SECONDS
                    in_glitch_window = age <= settle + SCENE_HEAL_WINDOW_SECONDS
                if is_recovery or in_glitch_window:
                    reason = "recovery" if is_recovery else "glitch_window"
                    hass.async_create_task(
                        ctrl.handle_scene_drift_reassert(entity_id, reason)
                    )
                    return
```

`old_state` is already in scope (captured at the top of the handler, line 498), and `import time as _time` is already at the top of the factory (line 483).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py -v`
Expected: PASS (all tests so far).

- [ ] **Step 6: Guard against regressions in the existing grace tests**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_per_entity_transition_grace.py -v`
Expected: PASS — those tests use `network_room` with `scene_self_heal` defaulting on; `test_divergence_after_long_fade_marks_manual` commands 80s ago with a 60s transition (settle=64s, window end=124s; age 80s is *inside* the window). It would now heal instead of latch manual. **Update that test** to push the divergence past the heal window so it still asserts the manual path: change its `commanded_at` / `last_scene_change_monotonic` offsets from `80.0` to `200.0`:

```python
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 200.0
    ctrl._active_scene_targets = {
        "light.network_room_overhead_1": {
            "state": "on",
            "brightness": 100,
            "commanded_at": time.monotonic() - 200.0,
            "transition": 60.0,
        }
    }
```

Re-run both files. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add custom_components/area_lighting/event_handlers.py \
        custom_components/area_lighting/tests/integration/test_scene_self_healing.py \
        custom_components/area_lighting/tests/integration/test_per_entity_transition_grace.py
git commit -m "(Minor) area_lighting: classify scene divergence as glitch or manual"
```

---

## Task 4: Post-settle self-check (catch during-fade glitches)

**Files:**
- Modify: `custom_components/area_lighting/controller.py` (imports; `_activate_scene` visual branch ~lines 743-752; new callback)
- Test: `custom_components/area_lighting/tests/integration/test_scene_self_healing.py`

- [ ] **Step 1: Write the failing test**

Append to `test_scene_self_healing.py`:

```python
@pytest.mark.integration
async def test_post_settle_selfcheck_heals_during_fade_glitch(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """A glitch that lands during the fade (ignored by the event path as
    'settling') is healed by the one-shot post-settle self-check."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    eid = "light.network_room_overhead_1"
    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    # Target commanded "now"; bulb currently sits at a divergent value.
    ctrl._active_scene_targets = {eid: _on_target(commanded_offset=0.0)}
    hass.states.async_set(eid, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()

    service_calls.clear()
    # Fire the self-check callback directly (the scheduling is asserted below).
    ctrl._run_post_settle_selfcheck(None)
    await hass.async_block_till_done()

    assert _light_turn_on_calls(service_calls, eid), "self-check should heal the drift"


@pytest.mark.integration
async def test_activating_scene_schedules_selfcheck(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """A visual-scene activation schedules exactly one pending self-check."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("daylight", ActivationSource.USER, transition=5.0)
    assert ctrl._heal_selfcheck_unsub is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py -k selfcheck -v`
Expected: FAIL — `AttributeError: ... has no attribute '_run_post_settle_selfcheck'` / `_heal_selfcheck_unsub` stays `None`.

- [ ] **Step 3: Add imports**

In `controller.py`, with the other `homeassistant.helpers.event` / core imports near the top, add:

```python
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
```

(`callback` may already be imported — if so, leave it. `STATE_ON` is needed in the callback; import it from `homeassistant.const` if not already present.)

- [ ] **Step 4: Schedule the self-check in `_activate_scene`**

In `_activate_scene`, the visual-scene branch (after `await self._apply_scene_data(scene_slug, transition)` at line 748, before `self._state.transition_to_scene(...)`):

```python
        await self._apply_scene_data(scene_slug, transition)
        self._schedule_post_settle_selfcheck(transition)
        self._clear_scene_drift_issue()
        self._state.transition_to_scene(scene_slug, source)
```

Add the scheduler + callback as methods:

```python
    def _schedule_post_settle_selfcheck(self, transition: float | None) -> None:
        """Schedule one verification at the scene's settle point to catch a
        glitch that landed *during* the fade (which the event path ignores
        as 'still settling'). Superseded by the next scene command."""
        if self._heal_selfcheck_unsub is not None:
            self._heal_selfcheck_unsub()
            self._heal_selfcheck_unsub = None
        if not self._scene_self_heal or not self._active_scene_targets:
            return
        delay = (transition or 0.0) + MANUAL_DETECTION_GRACE_SECONDS + 1.0
        self._heal_selfcheck_unsub = async_call_later(
            self.hass, delay, self._run_post_settle_selfcheck
        )

    @callback
    def _run_post_settle_selfcheck(self, _now) -> None:
        self._heal_selfcheck_unsub = None
        if self._state.is_off or self.current_scene == "manual":
            return
        for entity_id, target in list(self._active_scene_targets.items()):
            if target.get("state") != "on":
                continue
            st = self.hass.states.get(entity_id)
            if st is None or st.state != STATE_ON:
                continue
            if not self.state_matches_scene_target(entity_id, st):
                self.hass.async_create_task(
                    self.handle_scene_drift_reassert(entity_id, "post_settle")
                )
```

Import `MANUAL_DETECTION_GRACE_SECONDS` into `controller.py` if not already imported (add to the `from .const import (` block).

- [ ] **Step 5: Cancel the pending check on unload**

In `controller.py`, find the teardown/unload method (search for `def async_unload` or where `_state_listeners` / timers are cancelled). Add:

```python
        if self._heal_selfcheck_unsub is not None:
            self._heal_selfcheck_unsub()
            self._heal_selfcheck_unsub = None
```

If no controller-level unload exists, add the same cancellation to `handle_lights_all_off` (line 1432, after the timer cancels) so a clean off also drops the pending check.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py -k selfcheck -v`
Expected: PASS (both).

- [ ] **Step 7: Commit**

```bash
git add custom_components/area_lighting/controller.py \
        custom_components/area_lighting/tests/integration/test_scene_self_healing.py
git commit -m "(Minor) area_lighting: post-settle self-check for during-fade glitches"
```

---

## Task 5: Diagnostics + clear-on-clean-transition wiring

**Files:**
- Modify: `custom_components/area_lighting/controller.py` (`diagnostic_snapshot` lines 327-359; `handle_lights_all_off` line 1432)
- Test: `custom_components/area_lighting/tests/integration/test_scene_self_healing.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_scene_self_healing.py`:

```python
@pytest.mark.integration
async def test_diagnostic_snapshot_exposes_heal_fields(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    snap = ctrl.diagnostic_snapshot()
    assert snap["scene_self_heal_enabled"] is True
    assert snap["scene_heal_attempts"] == {}


@pytest.mark.integration
async def test_all_off_clears_heal_state_and_issue(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    from homeassistant.helpers import issue_registry as ir
    from custom_components.area_lighting.const import DOMAIN, SCENE_DRIFT_ISSUE_ID

    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._heal_attempts = {"light.network_room_overhead_1": [time.monotonic()]}
    ctrl._raise_scene_drift_issue("light.network_room_overhead_1")

    await ctrl.handle_lights_all_off()
    await hass.async_block_till_done()

    assert ctrl._heal_attempts == {}
    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, f"{SCENE_DRIFT_ISSUE_ID}_network_room") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py -k "diagnostic_snapshot_exposes or all_off_clears" -v`
Expected: FAIL — snapshot lacks the keys; `handle_lights_all_off` doesn't clear the issue.

- [ ] **Step 3: Extend `diagnostic_snapshot`**

In `diagnostic_snapshot` (line 327), add two entries (e.g. after `"alert_active": self._alert_active,`):

```python
            "scene_self_heal_enabled": self._scene_self_heal,
            "scene_heal_attempts": {k: len(v) for k, v in self._heal_attempts.items()},
```

- [ ] **Step 4: Clear on all-off**

In `handle_lights_all_off` (line 1432), after `self._active_scene_targets = {}` (line 1439):

```python
        self._active_scene_targets = {}
        self._clear_scene_drift_issue()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py -k "diagnostic_snapshot_exposes or all_off_clears" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/area_lighting/controller.py \
        custom_components/area_lighting/tests/integration/test_scene_self_healing.py
git commit -m "(Patch) area_lighting: expose heal diagnostics; clear on all-off"
```

---

## Task 6: Repairs issue translations

**Files:**
- Modify: `custom_components/area_lighting/translations/en.json` (`issues` object)
- Modify: `custom_components/area_lighting/strings.json` (`issues` object)
- Test: `custom_components/area_lighting/tests/test_translations.py` (existing parity test)

- [ ] **Step 1: Add the issue block to `en.json`**

Inside the top-level `"issues": { ... }` object, alongside `missing_external_entities`, add:

```json
    "scene_drift_unhealable": {
      "title": "{area}: a light keeps drifting from its scene",
      "description": "Home Assistant repeatedly tried to restore `{entity}` in {area} to its active scene, but it keeps changing back on its own. This usually means a Philips Hue power-on default, a Zigbee/RF dropout, or a Hue-app automation is driving the bulb independently of Home Assistant. Check the bulb's power-on behavior (set it to 'last state') and disable any competing Hue motion/automation for this room. The area has been left in `manual` until the next motion or remote event."
    }
```

- [ ] **Step 2: Mirror it in `strings.json`**

Add the identical `scene_drift_unhealable` block to the `issues` object in `strings.json`.

- [ ] **Step 3: Run the translations parity test**

Run: `uv run pytest custom_components/area_lighting/tests/test_translations.py -v`
Expected: PASS (both files now contain the same issue key).

- [ ] **Step 4: Commit**

```bash
git add custom_components/area_lighting/translations/en.json custom_components/area_lighting/strings.json
git commit -m "(Patch) area_lighting: add scene_drift_unhealable repairs strings"
```

---

## Task 7: Incident replay (capstone integration test)

**Files:**
- Test: `custom_components/area_lighting/tests/integration/test_scene_self_healing.py`

- [ ] **Step 1: Write the replay test**

Append to `test_scene_self_healing.py`:

```python
@pytest.mark.integration
async def test_incident_replay_right_w_glitch_and_left_w_recovery(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """Reproduce the upstairs_bathroom incident with the two fixture lights.

    ambient fade applied; ~110s later one bulb jumps to 228/3086 (Hue glitch),
    the other goes unavailable then recovers. With self-healing on, the area
    stays in 'ambient' and never latches 'manual'.
    """
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    right = "light.network_room_overhead_1"
    left = "light.network_room_overhead_2"

    for eid in (right, left):
        hass.states.async_set(eid, "on", {"brightness": 10, "color_temp_kelvin": 2700})
    await hass.async_block_till_done()

    ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 120.0
    commanded = time.monotonic() - 110.0
    ctrl._active_scene_targets = {
        right: {"state": "on", "brightness": 10, "color_temp_kelvin": 2700,
                "commanded_at": commanded, "transition": 60.0},
        left: {"state": "on", "brightness": 10, "color_temp_kelvin": 2700,
               "commanded_at": commanded, "transition": 60.0},
    }

    # right_w glitches to a foreign value inside the heal window → healed.
    hass.states.async_set(right, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()
    assert not ctrl._state.is_manual
    assert _light_turn_on_calls(service_calls, right)

    # left_w drops out, then recovers to a divergent value → healed.
    hass.states.async_set(left, "unavailable", {})
    await hass.async_block_till_done()
    hass.states.async_set(left, "on", {"brightness": 228, "color_temp_kelvin": 3086})
    await hass.async_block_till_done()
    assert not ctrl._state.is_manual
    assert _light_turn_on_calls(service_calls, left)

    assert ctrl.current_scene == "ambient"
```

- [ ] **Step 2: Run the replay test**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py::test_incident_replay_right_w_glitch_and_left_w_recovery -v`
Expected: PASS.

- [ ] **Step 3: Run the full new test file + the grace test**

Run: `uv run pytest custom_components/area_lighting/tests/integration/test_scene_self_healing.py custom_components/area_lighting/tests/integration/test_per_entity_transition_grace.py -v`
Expected: PASS (all).

- [ ] **Step 4: Commit**

```bash
git add custom_components/area_lighting/tests/integration/test_scene_self_healing.py
git commit -m "(Patch) area_lighting: incident-replay test for scene self-healing"
```

---

## Task 8: Documentation

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `CONFIGURATION.md`

- [ ] **Step 1: CHANGELOG entry**

Add a top entry (match the file's existing style/heading):

```markdown
- Scene self-healing: out-of-band Hue glitches (power-on defaults, RF
  dropouts, recovery from `unavailable`) are now auto-corrected back to the
  active scene instead of latching the area to `manual`. A bulb that keeps
  diverging is left in `manual` and raises a Repairs issue. Disable globally
  with `scene_self_heal: false`.
```

- [ ] **Step 2: README — document behavior + the config key**

In the manual-detection / scene section of `README.md`, add a short subsection describing the three-way classification (settling/glitch/manual), the `settle + 60s` heal window, recovery healing, the loop cap + Repairs issue, and the `scene_self_heal` top-level flag (default `true`).

- [ ] **Step 3: CONFIGURATION.md — the new key**

In `CONFIGURATION.md`, document the top-level `scene_self_heal` boolean (default `true`) under the root-level options, with a one-line description and example.

- [ ] **Step 4: Verify docs reference real names**

Run: `grep -n "scene_self_heal\|scene_drift_unhealable" CHANGELOG.md README.md CONFIGURATION.md`
Expected: matches in all three; key name matches the schema (`scene_self_heal`).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md CONFIGURATION.md
git commit -m "(Patch) docs: document scene self-healing + scene_self_heal flag"
```

---

## Final verification

- [ ] **Run the full check suite (CI-equivalent):**

Run: `dagger call all`
Expected: lint, typecheck, and the full pytest suite all pass.

- [ ] **If Dagger is unavailable, run locally:**

```bash
uv run ruff check custom_components/area_lighting
uv run ruff format --check custom_components/area_lighting
uv run mypy custom_components/area_lighting
uv run pytest custom_components/area_lighting/tests -q
```

Expected: all green.

---

## Self-Review notes (for the implementer)

- **Spec coverage:** Tier-1 recovery (Task 3), tier-2 window (Task 3), manual fall-through (Task 3), instant heal + loop cap + Repairs (Task 2), post-settle self-check (Task 4), kill-switch (Task 1 + Task 3), Repairs+logs alert surface (Tasks 2/6), diagnostics + clear-on-clean (Task 5), config flag (Task 1), tests incl. incident replay (Task 7), docs (Task 8). All spec sections map to a task.
- **Known interaction:** Task 3 Step 6 updates `test_per_entity_transition_grace.py`'s `test_divergence_after_long_fade_marks_manual`, whose 80s offset now falls inside the new heal window — it is moved to 200s so it still exercises the manual path. Do not skip that step.
- **Property/method names are consistent across tasks:** `scene_self_heal_enabled` (property), `_scene_self_heal` (field), `handle_scene_drift_reassert`, `_raise_scene_drift_issue`, `_clear_scene_drift_issue`, `_schedule_post_settle_selfcheck`, `_run_post_settle_selfcheck`, `_heal_attempts`, `_heal_selfcheck_unsub`, `SCENE_HEAL_WINDOW_SECONDS`, `SCENE_HEAL_MAX_ATTEMPTS`, `SCENE_HEAL_ATTEMPT_WINDOW_SECONDS`, `SCENE_DRIFT_ISSUE_ID`.
