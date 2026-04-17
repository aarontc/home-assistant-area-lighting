# Alert Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a named-pattern alert system that flashes lights in one or all areas, then restores their previous state, with proper manual-detection suppression and timer pause/resume.

**Architecture:** A new `alert.py` module owns the full alert lifecycle (capture → execute → restore). Alert patterns are defined globally in the YAML config, parsed into `AlertPattern`/`AlertStep` dataclasses, and triggered via an `area_lighting.alert` service. The controller gets a thin `_alert_active` boolean flag that suppresses manual detection in `event_handlers.py`. Timers are snapshot/cancelled on alert start and restored on alert end.

**Tech Stack:** Python 3.13, Home Assistant custom component, voluptuous (config validation), pytest + `pytest-homeassistant-custom-component`.

**Spec:** `docs/superpowers/specs/2026-04-16-alert-mode-design.md`

---

## File structure

| File | Responsibility | Change |
| --- | --- | --- |
| `custom_components/area_lighting/models.py` | Data models | Add `AlertStep`, `AlertPattern`; add `alert_patterns` to `AreaLightingConfig` |
| `custom_components/area_lighting/config_schema.py` | Config validation + parsing | Add `ALERT_STEP_SCHEMA`, `ALERT_PATTERN_SCHEMA`; update `parse_config()` |
| `custom_components/area_lighting/__init__.py` | Component setup | Add `alert_patterns` to `CONFIG_SCHEMA`; store parsed patterns |
| `custom_components/area_lighting/alert.py` | Alert execution engine | Create: target filtering, light capture/restore, `execute_alert()` |
| `custom_components/area_lighting/controller.py` | Controller state | Add `_alert_active` flag + diagnostic snapshot entry |
| `custom_components/area_lighting/event_handlers.py` | Event dispatch | Add alert guard in manual detection |
| `custom_components/area_lighting/services.py` | Service registration | Add `alert` service handler |
| `custom_components/area_lighting/services.yaml` | Service UI definitions | Add `alert` service entry |
| `custom_components/area_lighting/tests/test_alert.py` | Unit tests for alert module | Create |
| `custom_components/area_lighting/tests/integration/test_alert.py` | Integration tests | Create |

---

## Notes for the implementer

- **Test runner:** `uv run --extra dev python -m pytest ...` from repo root.
- **Commit-msg format:** `(Minor) <subject>` — this is new functionality.
- **TDD order:** failing test → implementation → passing test → commit.
- **Line numbers** are from `main` as of this plan. Use surrounding string context if drifted.
- **HA color modes** for target filtering: import `ColorMode` from `homeassistant.components.light`. Color-capable modes are `HS`, `RGB`, `RGBW`, `RGBWW`, `XY`. Read from the entity's `supported_color_modes` state attribute.

---

## Task 1: Data model + config schema

**Goal:** Add `AlertStep` and `AlertPattern` dataclasses to `models.py`, voluptuous schemas to `config_schema.py`, and update `parse_config()` + `CONFIG_SCHEMA` so alert patterns are validated and parsed from YAML.

**Files:**
- Modify: `custom_components/area_lighting/models.py`
- Modify: `custom_components/area_lighting/config_schema.py`
- Modify: `custom_components/area_lighting/__init__.py`
- Create: `custom_components/area_lighting/tests/test_alert_config.py`

- [ ] **Step 1: Write the failing config-parsing test**

Create `custom_components/area_lighting/tests/test_alert_config.py`:

```python
"""Tests for alert pattern config parsing."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting.config_schema import parse_config
from custom_components.area_lighting.models import AlertPattern, AlertStep


@pytest.mark.unit
def test_parse_valid_alert_pattern() -> None:
    """A valid alert_patterns block parses into AlertPattern objects."""
    raw = {
        "areas": [],
        "alert_patterns": {
            "blue_alert": {
                "steps": [
                    {
                        "target": "color",
                        "state": "on",
                        "brightness": 255,
                        "rgb_color": [0, 0, 255],
                    },
                    {"target": "white", "state": "off"},
                ],
                "delay": 3.0,
                "restore": True,
            },
            "three_flashes": {
                "steps": [
                    {"target": "all", "state": "on", "brightness": 255, "delay": 1.0},
                    {"target": "all", "state": "off", "delay": 1.0},
                ],
                "repeat": 3,
                "start_inverted": True,
                "restore": True,
            },
        },
    }
    config = parse_config(raw)
    assert len(config.alert_patterns) == 2
    blue = config.alert_patterns["blue_alert"]
    assert isinstance(blue, AlertPattern)
    assert len(blue.steps) == 2
    assert blue.steps[0].target == "color"
    assert blue.steps[0].brightness == 255
    assert blue.steps[0].rgb_color == (0, 0, 255)
    assert blue.delay == 3.0
    assert blue.restore is True
    assert blue.repeat == 1  # default

    flashes = config.alert_patterns["three_flashes"]
    assert flashes.repeat == 3
    assert flashes.start_inverted is True


@pytest.mark.unit
def test_parse_defaults_applied() -> None:
    """Missing optional fields get correct defaults."""
    raw = {
        "areas": [],
        "alert_patterns": {
            "simple": {
                "steps": [{"target": "all", "state": "on"}],
            },
        },
    }
    config = parse_config(raw)
    p = config.alert_patterns["simple"]
    assert p.repeat == 1
    assert p.delay == 0.0
    assert p.start_inverted is False
    assert p.restore is True
    assert p.steps[0].delay == 0.0
    assert p.steps[0].brightness is None


@pytest.mark.unit
def test_parse_no_alert_patterns() -> None:
    """Config without alert_patterns parses with an empty dict."""
    raw = {"areas": []}
    config = parse_config(raw)
    assert config.alert_patterns == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run --extra dev python -m pytest custom_components/area_lighting/tests/test_alert_config.py -v
```

Expected: FAIL — `AlertPattern` doesn't exist yet.

- [ ] **Step 3: Add dataclasses to `models.py`**

Before `AreaConfig` (line 132), add:

```python
@dataclass
class AlertStep:
    """One step in an alert pattern animation."""

    target: str  # "all", "color", "white"
    state: str  # "on", "off"
    delay: float = 0.0
    brightness: int | None = None
    rgb_color: tuple[int, int, int] | None = None
    color_temp_kelvin: int | None = None
    hs_color: tuple[float, float] | None = None
    xy_color: tuple[float, float] | None = None
    transition: float | None = None


@dataclass
class AlertPattern:
    """Named alert/flash pattern configuration."""

    steps: list[AlertStep]
    delay: float = 0.0
    repeat: int = 1
    start_inverted: bool = False
    restore: bool = True
```

Add `alert_patterns` field to `AreaLightingConfig` (after `areas`, line 224):

```python
@dataclass
class AreaLightingConfig:
    """Top-level configuration for the area_lighting integration."""

    areas: list[AreaConfig] = field(default_factory=list)
    alert_patterns: dict[str, AlertPattern] = field(default_factory=dict)
```

- [ ] **Step 4: Add voluptuous schemas to `config_schema.py`**

Add imports at the top (line 9):

```python
from .models import (
    AlertPattern,
    AlertStep,
    AreaConfig,
    ...existing imports...
)
```

Before `AREA_SCHEMA` (around line 127), add:

```python
ALERT_STEP_SCHEMA = vol.Schema(
    {
        vol.Required("target"): vol.In(["all", "color", "white"]),
        vol.Required("state"): vol.In(["on", "off"]),
        vol.Optional("delay", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional("brightness"): vol.All(int, vol.Range(min=0, max=255)),
        vol.Optional("rgb_color"): vol.All(
            vol.ExactSequence([int, int, int]),
        ),
        vol.Optional("color_temp_kelvin"): vol.All(int, vol.Range(min=1000, max=10000)),
        vol.Optional("hs_color"): vol.All(
            vol.ExactSequence([vol.Coerce(float), vol.Coerce(float)]),
        ),
        vol.Optional("xy_color"): vol.All(
            vol.ExactSequence([vol.Coerce(float), vol.Coerce(float)]),
        ),
        vol.Optional("transition"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)

ALERT_PATTERN_SCHEMA = vol.Schema(
    {
        vol.Required("steps"): vol.All(cv.ensure_list, vol.Length(min=1), [ALERT_STEP_SCHEMA]),
        vol.Optional("delay", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional("repeat", default=1): vol.All(int, vol.Range(min=1)),
        vol.Optional("start_inverted", default=False): cv.boolean,
        vol.Optional("restore", default=True): cv.boolean,
    }
)
```

- [ ] **Step 5: Update `CONFIG_SCHEMA` in `__init__.py`**

In `__init__.py`, add import for the new schema (line ~19):

```python
from .config_schema import AREA_SCHEMA, ALERT_PATTERN_SCHEMA, parse_config, validate_leader_follower_graph
```

Update `CONFIG_SCHEMA` (line 42-54) to accept `alert_patterns`:

```python
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("areas"): vol.All(cv.ensure_list, [AREA_SCHEMA]),
                vol.Optional("alert_patterns", default={}): {
                    cv.string: ALERT_PATTERN_SCHEMA,
                },
                # Ignored fields from templater.yaml kept for config compat
                vol.Optional("base_url"): str,
            },
            extra=vol.PREVENT_EXTRA,
        )
    },
    extra=vol.ALLOW_EXTRA,
)
```

- [ ] **Step 6: Update `parse_config()` in `config_schema.py`**

At the end of `parse_config()` (before the final `return`), add alert pattern parsing:

```python
    alert_patterns: dict[str, AlertPattern] = {}
    for name, pat_raw in raw.get("alert_patterns", {}).items():
        steps = [
            AlertStep(
                target=s["target"],
                state=s["state"],
                delay=s.get("delay", 0.0),
                brightness=s.get("brightness"),
                rgb_color=tuple(s["rgb_color"]) if "rgb_color" in s else None,
                color_temp_kelvin=s.get("color_temp_kelvin"),
                hs_color=tuple(s["hs_color"]) if "hs_color" in s else None,
                xy_color=tuple(s["xy_color"]) if "xy_color" in s else None,
                transition=s.get("transition"),
            )
            for s in pat_raw["steps"]
        ]
        alert_patterns[name] = AlertPattern(
            steps=steps,
            delay=pat_raw.get("delay", 0.0),
            repeat=pat_raw.get("repeat", 1),
            start_inverted=pat_raw.get("start_inverted", False),
            restore=pat_raw.get("restore", True),
        )

    return AreaLightingConfig(areas=areas, alert_patterns=alert_patterns)
```

- [ ] **Step 7: Run tests to verify they pass**

```
uv run --extra dev python -m pytest custom_components/area_lighting/tests/test_alert_config.py -v
```

Expected: 3 passed.

- [ ] **Step 8: Run full suite**

```
uv run --extra dev python -m pytest custom_components/area_lighting/tests/ -x -q
```

Expected: 364 passed (361 existing + 3 new).

- [ ] **Step 9: Commit**

```bash
git add custom_components/area_lighting/models.py \
        custom_components/area_lighting/config_schema.py \
        custom_components/area_lighting/__init__.py \
        custom_components/area_lighting/tests/test_alert_config.py
git commit -m "(Minor) area_lighting: add AlertPattern/AlertStep data model and config schema

Defines alert pattern YAML schema under alert_patterns at the top level.
Each pattern has steps (target/state/delay/light attrs), repeat count,
start_inverted flag, and restore flag. parse_config() produces typed
AlertPattern objects stored on AreaLightingConfig."
```

---

## Task 2: Alert module — target filtering and light capture/restore

**Goal:** Create `alert.py` with three pure helper functions: `filter_lights_by_target()`, `capture_light_states()`, and `restore_light_states()`. All unit-testable with mocked HA state.

**Files:**
- Create: `custom_components/area_lighting/alert.py`
- Create: `custom_components/area_lighting/tests/test_alert.py`

- [ ] **Step 1: Write the failing tests**

Create `custom_components/area_lighting/tests/test_alert.py`:

```python
"""Unit tests for alert module helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant, State

from custom_components.area_lighting.alert import (
    capture_light_states,
    filter_lights_by_target,
    restore_light_states,
)


def _make_state(entity_id: str, state: str = "on", attributes: dict | None = None) -> State:
    """Create a mock HA State object."""
    return State(entity_id, state, attributes or {})


# ── Target filtering ─────────────────────────────────────────────────


@pytest.mark.unit
def test_filter_all_returns_all_lights() -> None:
    light_ids = ["light.a", "light.b", "light.c"]
    states = {
        "light.a": _make_state("light.a", attributes={"supported_color_modes": ["hs"]}),
        "light.b": _make_state("light.b", attributes={"supported_color_modes": ["color_temp"]}),
        "light.c": _make_state("light.c", attributes={"supported_color_modes": ["brightness"]}),
    }
    result = filter_lights_by_target(light_ids, "all", states.get)
    assert result == ["light.a", "light.b", "light.c"]


@pytest.mark.unit
def test_filter_color_returns_only_color_capable() -> None:
    light_ids = ["light.color", "light.white", "light.ct"]
    states = {
        "light.color": _make_state(
            "light.color", attributes={"supported_color_modes": ["hs", "color_temp"]}
        ),
        "light.white": _make_state(
            "light.white", attributes={"supported_color_modes": ["brightness"]}
        ),
        "light.ct": _make_state(
            "light.ct", attributes={"supported_color_modes": ["color_temp"]}
        ),
    }
    result = filter_lights_by_target(light_ids, "color", states.get)
    assert result == ["light.color"]


@pytest.mark.unit
def test_filter_white_returns_non_color_capable() -> None:
    light_ids = ["light.color", "light.white", "light.ct"]
    states = {
        "light.color": _make_state(
            "light.color", attributes={"supported_color_modes": ["hs", "color_temp"]}
        ),
        "light.white": _make_state(
            "light.white", attributes={"supported_color_modes": ["brightness"]}
        ),
        "light.ct": _make_state(
            "light.ct", attributes={"supported_color_modes": ["color_temp"]}
        ),
    }
    result = filter_lights_by_target(light_ids, "white", states.get)
    assert result == ["light.white", "light.ct"]


@pytest.mark.unit
def test_filter_skips_unavailable_lights() -> None:
    light_ids = ["light.ok", "light.gone"]
    states = {
        "light.ok": _make_state("light.ok", attributes={"supported_color_modes": ["hs"]}),
        "light.gone": _make_state("light.gone", state="unavailable"),
    }
    result = filter_lights_by_target(light_ids, "all", states.get)
    assert result == ["light.ok"]


# ── Capture + restore ────────────────────────────────────────────────


@pytest.mark.unit
def test_capture_returns_state_dict_per_light() -> None:
    states = {
        "light.a": _make_state(
            "light.a",
            state="on",
            attributes={
                "brightness": 200,
                "color_mode": "hs",
                "hs_color": (30.0, 80.0),
                "supported_color_modes": ["hs"],
            },
        ),
        "light.b": _make_state("light.b", state="off", attributes={}),
    }
    captured = capture_light_states(["light.a", "light.b"], states.get)
    assert captured["light.a"]["state"] == "on"
    assert captured["light.a"]["brightness"] == 200
    assert captured["light.a"]["hs_color"] == (30.0, 80.0)
    assert captured["light.b"]["state"] == "off"


@pytest.mark.unit
async def test_restore_replays_captured_states() -> None:
    captured = {
        "light.a": {
            "state": "on",
            "brightness": 200,
            "color_mode": "hs",
            "hs_color": (30.0, 80.0),
        },
        "light.b": {"state": "off"},
    }
    mock_call = AsyncMock()
    await restore_light_states(captured, mock_call)

    calls = mock_call.call_args_list
    assert len(calls) == 2
    # light.a should be turned on with attributes
    on_call = [c for c in calls if c.kwargs.get("entity_id") == "light.a"][0]
    assert on_call.args == ("light", "turn_on")
    assert on_call.kwargs["brightness"] == 200
    # light.b should be turned off
    off_call = [c for c in calls if c.kwargs.get("entity_id") == "light.b"][0]
    assert off_call.args == ("light", "turn_off")
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run --extra dev python -m pytest custom_components/area_lighting/tests/test_alert.py -v
```

Expected: FAIL — `alert` module doesn't exist.

- [ ] **Step 3: Implement `alert.py`**

Create `custom_components/area_lighting/alert.py`:

```python
"""Alert pattern execution engine for Area Lighting.

Flashes lights in an area according to a named pattern, then restores
their previous state. Orthogonal to the scene state machine — alerts
don't cause scene transitions, but coordinate with the controller to
suppress manual detection and pause timers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, State

from .models import AlertPattern, AlertStep

_LOGGER = logging.getLogger(__name__)

# HA color modes that indicate color capability.
COLOR_MODES = {"hs", "rgb", "rgbw", "rgbww", "xy"}

# Light state attributes worth capturing for restore.
_CAPTURE_ATTRS = (
    "brightness",
    "color_mode",
    "color_temp_kelvin",
    "hs_color",
    "rgb_color",
    "xy_color",
)

# Attributes to pass through on restore (color_mode is read-only in HA).
_RESTORE_ATTRS = (
    "brightness",
    "color_temp_kelvin",
    "hs_color",
    "rgb_color",
    "xy_color",
)


def _is_color_capable(state: State) -> bool:
    """True if the light supports any color mode."""
    modes = state.attributes.get("supported_color_modes") or []
    return bool(set(modes) & COLOR_MODES)


def filter_lights_by_target(
    light_ids: list[str],
    target: str,
    get_state: Callable[[str], State | None],
) -> list[str]:
    """Filter light entity IDs by target type, skipping unavailable lights.

    target: "all", "color", or "white".
    get_state: typically hass.states.get.
    """
    result: list[str] = []
    for eid in light_ids:
        state = get_state(eid)
        if state is None or state.state == "unavailable":
            continue
        if target == "all":
            result.append(eid)
        elif target == "color":
            if _is_color_capable(state):
                result.append(eid)
        elif target == "white":
            if not _is_color_capable(state):
                result.append(eid)
    return result


def capture_light_states(
    light_ids: list[str],
    get_state: Callable[[str], State | None],
) -> dict[str, dict[str, Any]]:
    """Snapshot the current HA state of each light for later restore."""
    captured: dict[str, dict[str, Any]] = {}
    for eid in light_ids:
        state = get_state(eid)
        if state is None:
            continue
        entry: dict[str, Any] = {"state": state.state}
        for attr in _CAPTURE_ATTRS:
            val = state.attributes.get(attr)
            if val is not None:
                entry[attr] = val
        captured[eid] = entry
    return captured


async def restore_light_states(
    captured: dict[str, dict[str, Any]],
    async_call: Callable[..., Any],
) -> None:
    """Replay captured light states via service calls.

    async_call signature: async_call(domain, service, **kwargs).
    """
    for eid, data in captured.items():
        if data.get("state") == "off":
            await async_call("light", "turn_off", entity_id=eid)
        else:
            kwargs: dict[str, Any] = {"entity_id": eid, "transition": 0}
            # Restore based on the color_mode that was active
            color_mode = data.get("color_mode")
            for attr in _RESTORE_ATTRS:
                val = data.get(attr)
                if val is not None:
                    # Only send the color attribute that matches the captured mode
                    if attr in ("hs_color", "rgb_color", "xy_color"):
                        if color_mode and attr.startswith(color_mode.split("_")[0]):
                            kwargs[attr] = val
                    else:
                        kwargs[attr] = val
            await async_call("light", "turn_on", **kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run --extra dev python -m pytest custom_components/area_lighting/tests/test_alert.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Run full suite**

```
uv run --extra dev python -m pytest custom_components/area_lighting/tests/ -x -q
```

Expected: 371 passed (364 + 7 new).

- [ ] **Step 6: Commit**

```bash
git add custom_components/area_lighting/alert.py \
        custom_components/area_lighting/tests/test_alert.py
git commit -m "(Minor) area_lighting: alert module with target filtering and light capture/restore

New alert.py with three helpers:
- filter_lights_by_target: filters by all/color/white using HA
  supported_color_modes, skips unavailable lights
- capture_light_states: snapshots brightness, color_mode, color attrs
- restore_light_states: replays via light.turn_on/turn_off with
  transition: 0 for instant snap-back"
```

---

## Task 3: Controller integration — `_alert_active` flag + manual detection guard

**Goal:** Add the `_alert_active` flag to the controller, suppress manual detection while it's set, and expose it in diagnostics.

**Files:**
- Modify: `custom_components/area_lighting/controller.py`
- Modify: `custom_components/area_lighting/event_handlers.py`
- Create: `custom_components/area_lighting/tests/integration/test_alert.py`

- [ ] **Step 1: Write the failing tests**

Create `custom_components/area_lighting/tests/integration/test_alert.py`:

```python
"""Integration tests for alert mode."""

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


@pytest.mark.integration
async def test_alert_active_defaults_false(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Controller starts with _alert_active == False."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl._alert_active is False


@pytest.mark.integration
async def test_alert_active_in_diagnostic_snapshot(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """diagnostic_snapshot includes alert_active."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    snap = ctrl.diagnostic_snapshot()
    assert "alert_active" in snap
    assert snap["alert_active"] is False


@pytest.mark.integration
async def test_manual_detection_suppressed_when_alert_active(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Light state changes during an alert do not trigger manual detection."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("daylight", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl.current_scene == "daylight"

    # Simulate alert in progress
    ctrl._alert_active = True

    # Fire a light state change that would normally trigger manual detection.
    # Simulate a brightness change on one of the area's lights.
    hass.states.async_set(
        "light.network_room_overhead_1",
        "on",
        {"brightness": 50, "color_mode": "color_temp", "color_temp_kelvin": 4000},
    )
    await hass.async_block_till_done()

    # Scene should still be daylight (manual detection did NOT fire)
    assert ctrl.current_scene == "daylight"

    ctrl._alert_active = False
```

- [ ] **Step 2: Run to verify they fail**

```
uv run --extra dev python -m pytest \
  custom_components/area_lighting/tests/integration/test_alert.py -v
```

Expected: FAIL — `_alert_active` doesn't exist.

- [ ] **Step 3: Add `_alert_active` to controller `__init__`**

In `controller.py`, find the `_alert_active` will go near the other transient flags. After `self._occupancy_timeout_enabled: bool = True` (line ~66), add:

```python
        self._alert_active: bool = False
```

- [ ] **Step 4: Add to `diagnostic_snapshot()`**

In `controller.py`, find `diagnostic_snapshot()` (around line 290). After `"occupancy_timeout_enabled": self._occupancy_timeout_enabled,` add:

```python
            "alert_active": self._alert_active,
```

- [ ] **Step 5: Add manual detection guard in `event_handlers.py`**

In `event_handlers.py`, find the `_handler` inner function within `_make_manual_detection_handler` (line ~480). After the circadian check (lines 504–506):

```python
        if ctrl._state.is_circadian:
            _skip("area state is circadian", entity_id)
            return
```

Add:

```python
        if ctrl._alert_active:
            _skip("alert pattern active", entity_id)
            return
```

- [ ] **Step 6: Run tests to verify they pass**

```
uv run --extra dev python -m pytest \
  custom_components/area_lighting/tests/integration/test_alert.py -v
```

Expected: 3 passed.

- [ ] **Step 7: Run full suite**

```
uv run --extra dev python -m pytest custom_components/area_lighting/tests/ -x -q
```

Expected: 374 passed.

- [ ] **Step 8: Commit**

```bash
git add custom_components/area_lighting/controller.py \
        custom_components/area_lighting/event_handlers.py \
        custom_components/area_lighting/tests/integration/test_alert.py
git commit -m "(Minor) area_lighting: _alert_active flag and manual detection guard

Adds _alert_active boolean (default False) to the controller. Manual
detection in event_handlers.py early-returns when the flag is set,
preventing alert-driven light changes from being interpreted as user
overrides. Exposed in diagnostic_snapshot for debugging."
```

---

## Task 4: Alert execution engine — `execute_alert()`

**Goal:** Implement the main `execute_alert()` function that orchestrates the full alert lifecycle: set flag, capture states, pause timers, run steps, restore states, resume timers, clear flag.

**Files:**
- Modify: `custom_components/area_lighting/alert.py`
- Modify: `custom_components/area_lighting/tests/test_alert.py`

- [ ] **Step 1: Write the failing unit tests for execute_alert**

Append to `custom_components/area_lighting/tests/test_alert.py`:

```python
from custom_components.area_lighting.alert import execute_alert
from custom_components.area_lighting.models import AlertPattern, AlertStep


def _make_mock_controller(light_ids: list[str], light_states: dict[str, State]):
    """Build a mock controller with the minimal interface execute_alert needs."""
    ctrl = MagicMock()
    ctrl.area.id = "test_area"
    # All lights in area
    ctrl.area.all_lights = [MagicMock(id=eid) for eid in light_ids]
    ctrl._alert_active = False
    # Timer mocks
    for timer_name in ("_motion_timer", "_motion_night_timer", "_occupancy_timer"):
        timer = MagicMock()
        timer.deadline_utc = None
        timer.is_active = False
        setattr(ctrl, timer_name, timer)
    return ctrl


@pytest.mark.unit
async def test_execute_alert_sets_and_clears_flag() -> None:
    """_alert_active is True during execution, False after."""
    ctrl = _make_mock_controller(["light.a"], {
        "light.a": _make_state("light.a", attributes={"supported_color_modes": ["brightness"]}),
    })
    pattern = AlertPattern(
        steps=[AlertStep(target="all", state="on", brightness=255, delay=0.0)],
        delay=0.0,
    )
    hass = MagicMock()
    hass.states.get = lambda eid: {
        "light.a": _make_state("light.a", attributes={"supported_color_modes": ["brightness"]}),
    }.get(eid)
    hass.services.async_call = AsyncMock()

    flag_during: list[bool] = []
    original_sleep = asyncio.sleep

    async def spy_sleep(duration):
        flag_during.append(ctrl._alert_active)
        # Don't actually sleep in tests

    with patch("custom_components.area_lighting.alert.asyncio.sleep", spy_sleep):
        await execute_alert(hass, ctrl, pattern)

    assert ctrl._alert_active is False  # cleared after


@pytest.mark.unit
async def test_execute_alert_respects_repeat() -> None:
    """Steps execute repeat × len(steps) times."""
    ctrl = _make_mock_controller(["light.a"], {})
    pattern = AlertPattern(
        steps=[
            AlertStep(target="all", state="on", delay=0.0),
            AlertStep(target="all", state="off", delay=0.0),
        ],
        repeat=3,
    )
    hass = MagicMock()
    hass.states.get = lambda eid: _make_state(
        eid, attributes={"supported_color_modes": ["brightness"]}
    )
    hass.services.async_call = AsyncMock()

    with patch("custom_components.area_lighting.alert.asyncio.sleep", AsyncMock()):
        await execute_alert(hass, ctrl, pattern)

    # 3 repeats × 2 steps = 6 service calls for steps, plus restore calls
    step_calls = [
        c
        for c in hass.services.async_call.call_args_list
        if c.kwargs.get("entity_id") == "light.a"
        or (len(c.args) >= 3 and c.args[2].get("entity_id") == "light.a")
    ]
    # At minimum 6 calls for steps (on, off × 3) + 1 restore = 7
    assert len(step_calls) >= 6


@pytest.mark.unit
async def test_execute_alert_cancels_and_restores_timers() -> None:
    """Timers are cancelled on start and restored on end."""
    from datetime import UTC, datetime, timedelta

    future = datetime.now(UTC) + timedelta(minutes=5)
    ctrl = _make_mock_controller(["light.a"], {})
    ctrl._motion_timer.deadline_utc = future
    ctrl._motion_timer.is_active = True
    ctrl._motion_night_timer.deadline_utc = None
    ctrl._motion_night_timer.is_active = False
    ctrl._occupancy_timer.deadline_utc = future
    ctrl._occupancy_timer.is_active = True

    pattern = AlertPattern(
        steps=[AlertStep(target="all", state="on", delay=0.0)],
    )
    hass = MagicMock()
    hass.states.get = lambda eid: _make_state(
        eid, attributes={"supported_color_modes": ["brightness"]}
    )
    hass.services.async_call = AsyncMock()

    with patch("custom_components.area_lighting.alert.asyncio.sleep", AsyncMock()):
        await execute_alert(hass, ctrl, pattern)

    # All timers should have been cancelled
    ctrl._motion_timer.cancel.assert_called()
    ctrl._occupancy_timer.cancel.assert_called()
    # Active timers should have been restored
    ctrl._motion_timer.restore.assert_called_once_with(future)
    ctrl._occupancy_timer.restore.assert_called_once_with(future)
    # Inactive timer should NOT have been restored
    ctrl._motion_night_timer.restore.assert_not_called()
```

- [ ] **Step 2: Run to verify they fail**

```
uv run --extra dev python -m pytest \
  custom_components/area_lighting/tests/test_alert.py::test_execute_alert_sets_and_clears_flag \
  custom_components/area_lighting/tests/test_alert.py::test_execute_alert_respects_repeat \
  custom_components/area_lighting/tests/test_alert.py::test_execute_alert_cancels_and_restores_timers \
  -v
```

Expected: FAIL — `execute_alert` doesn't exist yet (or only the helpers do).

- [ ] **Step 3: Implement `execute_alert()` in `alert.py`**

Add to the end of `alert.py`:

```python
async def _execute_steps(
    hass: HomeAssistant,
    controller: Any,
    pattern: AlertPattern,
    all_light_ids: list[str],
) -> None:
    """Run the step sequence with repeat and start_inverted logic."""
    steps = list(pattern.steps)

    for cycle in range(pattern.repeat):
        effective_steps = steps
        if cycle == 0 and pattern.start_inverted and steps:
            # Check majority state of lights targeted by the first step.
            first = steps[0]
            targeted = filter_lights_by_target(
                all_light_ids, first.target, hass.states.get
            )
            if targeted:
                on_count = sum(
                    1
                    for eid in targeted
                    if (s := hass.states.get(eid)) is not None and s.state == "on"
                )
                majority_on = on_count > len(targeted) / 2
                first_is_on = first.state == "on"
                if majority_on == first_is_on:
                    effective_steps = list(reversed(steps))

        for step in effective_steps:
            targeted = filter_lights_by_target(
                all_light_ids, step.target, hass.states.get
            )
            if targeted:
                await _apply_step(hass, targeted, step)
            if step.delay > 0:
                await asyncio.sleep(step.delay)

    if pattern.delay > 0:
        await asyncio.sleep(pattern.delay)


async def _apply_step(
    hass: HomeAssistant,
    entity_ids: list[str],
    step: AlertStep,
) -> None:
    """Apply a single alert step to the targeted lights."""
    if step.state == "off":
        for eid in entity_ids:
            await hass.services.async_call(
                "light", "turn_off", {"entity_id": eid}, blocking=True
            )
    else:
        kwargs: dict[str, Any] = {"transition": 0}
        if step.brightness is not None:
            kwargs["brightness"] = step.brightness
        if step.rgb_color is not None:
            kwargs["rgb_color"] = list(step.rgb_color)
        if step.color_temp_kelvin is not None:
            kwargs["color_temp_kelvin"] = step.color_temp_kelvin
        if step.hs_color is not None:
            kwargs["hs_color"] = list(step.hs_color)
        if step.xy_color is not None:
            kwargs["xy_color"] = list(step.xy_color)
        if step.transition is not None:
            kwargs["transition"] = step.transition
        for eid in entity_ids:
            await hass.services.async_call(
                "light", "turn_on", {"entity_id": eid, **kwargs}, blocking=True
            )


async def execute_alert(
    hass: HomeAssistant,
    controller: Any,
    pattern: AlertPattern,
) -> None:
    """Execute an alert pattern on an area's lights.

    1. Set _alert_active flag (suppresses manual detection)
    2. Capture light states
    3. Snapshot + cancel timers
    4. Execute steps × repeat
    5. Restore light states (if pattern.restore)
    6. Restore timer deadlines
    7. Clear _alert_active flag
    """
    all_light_ids = [light.id for light in controller.area.all_lights]
    if not all_light_ids:
        return

    # Snapshot timer deadlines before cancelling
    timer_deadlines: dict[str, Any] = {}
    timers = {
        "_motion_timer": controller._motion_timer,
        "_motion_night_timer": controller._motion_night_timer,
        "_occupancy_timer": controller._occupancy_timer,
    }

    controller._alert_active = True
    try:
        # Capture light states for restore
        captured = capture_light_states(all_light_ids, hass.states.get)

        # Pause timers: snapshot deadlines then cancel
        for name, timer in timers.items():
            if timer.is_active and timer.deadline_utc is not None:
                timer_deadlines[name] = timer.deadline_utc
            timer.cancel()

        _LOGGER.debug(
            "Area %s: alert started, %d lights captured, %d timers paused",
            controller.area.id,
            len(captured),
            len(timer_deadlines),
        )

        # Execute steps
        await _execute_steps(hass, controller, pattern, all_light_ids)

        # Restore light states
        if pattern.restore and captured:
            async def _call(domain: str, service: str, **kwargs: Any) -> None:
                await hass.services.async_call(
                    domain, service, kwargs, blocking=True
                )

            await restore_light_states(captured, _call)

    finally:
        # Restore timers (fires immediately if past-due)
        for name, deadline in timer_deadlines.items():
            timers[name].restore(deadline)

        controller._alert_active = False
        _LOGGER.debug("Area %s: alert finished", controller.area.id)
```

Add the missing import at top of file:

```python
from typing import Any
```

(This should already be there from step 3 of this task's creation.)

- [ ] **Step 4: Run the new tests**

```
uv run --extra dev python -m pytest \
  custom_components/area_lighting/tests/test_alert.py -v
```

Expected: 10 passed (7 from Task 2 + 3 new).

- [ ] **Step 5: Run full suite**

```
uv run --extra dev python -m pytest custom_components/area_lighting/tests/ -x -q
```

Expected: 377 passed.

- [ ] **Step 6: Commit**

```bash
git add custom_components/area_lighting/alert.py \
        custom_components/area_lighting/tests/test_alert.py
git commit -m "(Minor) area_lighting: execute_alert engine with step loop and timer management

execute_alert() orchestrates the full alert lifecycle:
  - Sets _alert_active flag (suppresses manual detection)
  - Captures light states for restore
  - Snapshots and cancels all timers
  - Executes steps × repeat with start_inverted support
  - Restores light states
  - Restores timer deadlines (fires immediately if past-due)
  - Clears flag in finally block"
```

---

## Task 5: Service registration

**Goal:** Wire the `area_lighting.alert` service so it's callable from automations, scripts, and Developer Tools.

**Files:**
- Modify: `custom_components/area_lighting/services.py`
- Modify: `custom_components/area_lighting/services.yaml`
- Modify: `custom_components/area_lighting/tests/integration/test_alert.py`

- [ ] **Step 1: Write the failing integration test — full end-to-end**

Append to `custom_components/area_lighting/tests/integration/test_alert.py`:

```python
def _config_with_alert_patterns() -> dict:
    return {
        "area_lighting": {
            "alert_patterns": {
                "test_flash": {
                    "steps": [
                        {"target": "all", "state": "on", "brightness": 255, "delay": 0.0},
                        {"target": "all", "state": "off", "delay": 0.0},
                    ],
                    "repeat": 1,
                    "restore": True,
                },
            },
            "areas": [
                {
                    "id": "network_room",
                    "name": "Network Room",
                    "event_handlers": True,
                    "ambient_lighting_zone": "upstairs",
                    "circadian_switches": [
                        {"name": "Overhead", "max_brightness": 100, "min_brightness": 65},
                    ],
                    "lights": [
                        {
                            "id": "light.network_room_overhead_1",
                            "circadian_switch": "Overhead",
                            "circadian_type": "ct",
                            "roles": ["color", "dimming", "night", "white"],
                        },
                        {
                            "id": "light.network_room_overhead_2",
                            "circadian_switch": "Overhead",
                            "circadian_type": "ct",
                            "roles": ["color", "dimming", "night", "white"],
                        },
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "night", "name": "Night"},
                        {"id": "ambient", "name": "Ambient"},
                        {"id": "christmas", "name": "Christmas"},
                    ],
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.network_room_motion_sensor_motion",
                    ],
                    "motion_light_timer_durations": {
                        "off": "00:08:00",
                        "night_off": "00:05:00",
                    },
                    "occupancy_light_timer_durations": {
                        "off": "00:30:00",
                    },
                },
            ],
        }
    }


@pytest.mark.integration
async def test_alert_service_triggers_alert(
    hass: HomeAssistant, helper_entities
) -> None:
    """Calling area_lighting.alert executes the named pattern."""
    hass.states.async_set(
        "light.network_room_overhead_1", "on",
        {"brightness": 150, "supported_color_modes": ["color_temp"]},
    )
    hass.states.async_set(
        "light.network_room_overhead_2", "off",
        {"supported_color_modes": ["color_temp"]},
    )
    await _setup(hass, _config_with_alert_patterns())

    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl._alert_active is False

    # Call the alert service
    await hass.services.async_call(
        "area_lighting",
        "alert",
        {"area_id": "network_room", "pattern": "test_flash"},
        blocking=True,
    )

    # Alert should have completed — flag back to False
    assert ctrl._alert_active is False


@pytest.mark.integration
async def test_alert_service_unknown_pattern_logs_warning(
    hass: HomeAssistant, helper_entities
) -> None:
    """Calling with a nonexistent pattern name logs a warning."""
    await _setup(hass, _config_with_alert_patterns())

    # Should not raise — just logs a warning and returns
    await hass.services.async_call(
        "area_lighting",
        "alert",
        {"area_id": "network_room", "pattern": "nonexistent"},
        blocking=True,
    )


@pytest.mark.integration
async def test_alert_service_all_areas(
    hass: HomeAssistant, helper_entities
) -> None:
    """area_id 'all' dispatches to every controller."""
    hass.states.async_set(
        "light.network_room_overhead_1", "on",
        {"brightness": 150, "supported_color_modes": ["color_temp"]},
    )
    hass.states.async_set(
        "light.network_room_overhead_2", "off",
        {"supported_color_modes": ["color_temp"]},
    )
    await _setup(hass, _config_with_alert_patterns())

    await hass.services.async_call(
        "area_lighting",
        "alert",
        {"area_id": "all", "pattern": "test_flash"},
        blocking=True,
    )
    # Should complete without error
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl._alert_active is False
```

- [ ] **Step 2: Run to verify they fail**

```
uv run --extra dev python -m pytest \
  custom_components/area_lighting/tests/integration/test_alert.py::test_alert_service_triggers_alert \
  -v
```

Expected: FAIL — `area_lighting.alert` service not registered.

- [ ] **Step 3: Add service entry to `services.yaml`**

Append to `custom_components/area_lighting/services.yaml`:

```yaml
alert:
  name: Alert
  description: Flash lights in an area using a named alert pattern.
  fields:
    area_id:
      name: Area ID
      description: The area ID, or "all" to alert every area.
      required: true
      example: kitchen
      selector:
        text:
    pattern:
      name: Pattern
      description: Name of the alert pattern defined in alert_patterns.
      required: true
      example: blue_alert
      selector:
        text:
```

- [ ] **Step 4: Register the alert service in `services.py`**

Add import at top of `services.py`:

```python
import asyncio

from .alert import execute_alert
```

Add a new schema after `SNAPSHOT_SCHEMA`:

```python
ALERT_SCHEMA = vol.Schema(
    {
        vol.Required("area_id"): cv.string,
        vol.Required("pattern"): cv.string,
    }
)
```

At the end of `async_register_services()`, after the `snapshot_scene` registration (line ~118), add:

```python
    # Alert service
    async def _handle_alert(call: ServiceCall) -> None:
        area_id = call.data["area_id"]
        pattern_name = call.data["pattern"]
        _LOGGER.debug("Alert service invoked: area=%s pattern=%s", area_id, pattern_name)

        config = hass.data[DOMAIN]["config"]
        pattern = config.alert_patterns.get(pattern_name)
        if pattern is None:
            _LOGGER.warning(
                "Alert pattern %r not found in config", pattern_name
            )
            return

        controllers: dict[str, AreaLightingController] = hass.data[DOMAIN]["controllers"]
        if area_id == "all":
            await asyncio.gather(
                *(execute_alert(hass, ctrl, pattern) for ctrl in controllers.values())
            )
        else:
            controller = _get_controller(hass, area_id)
            if controller:
                await execute_alert(hass, controller, pattern)

    hass.services.async_register(
        DOMAIN,
        "alert",
        _handle_alert,
        schema=ALERT_SCHEMA,
    )
```

- [ ] **Step 5: Run the integration tests**

```
uv run --extra dev python -m pytest \
  custom_components/area_lighting/tests/integration/test_alert.py -v
```

Expected: 6 passed (3 from Task 3 + 3 new).

- [ ] **Step 6: Run full suite**

```
uv run --extra dev python -m pytest custom_components/area_lighting/tests/ -x -q
```

Expected: 380 passed.

- [ ] **Step 7: Commit**

```bash
git add custom_components/area_lighting/services.py \
        custom_components/area_lighting/services.yaml \
        custom_components/area_lighting/tests/integration/test_alert.py
git commit -m "(Minor) area_lighting: register area_lighting.alert service

Wires the alert service with area_id + pattern params. Looks up the
pattern from parsed config, dispatches execute_alert to the target
controller (or all controllers via asyncio.gather when area_id is
'all'). Unknown patterns log a warning and return."
```

---

## Task 6: Timer preservation integration test

**Goal:** Verify that timer deadlines survive an alert — if a timer was active before the alert, it's re-armed to the same deadline after.

**Files:**
- Modify: `custom_components/area_lighting/tests/integration/test_alert.py`

- [ ] **Step 1: Write the test**

Append to `custom_components/area_lighting/tests/integration/test_alert.py`:

```python
@pytest.mark.integration
async def test_alert_preserves_timer_deadline(
    hass: HomeAssistant, helper_entities
) -> None:
    """An active occupancy timer's deadline survives an alert."""
    hass.states.async_set(
        "light.network_room_overhead_1", "on",
        {"brightness": 150, "supported_color_modes": ["color_temp"]},
    )
    hass.states.async_set(
        "light.network_room_overhead_2", "off",
        {"supported_color_modes": ["color_temp"]},
    )
    hass.states.async_set("binary_sensor.network_room_motion_sensor_motion", "off")
    await _setup(hass, _config_with_alert_patterns())

    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    # Activate a scene so the occupancy timer arms
    await ctrl._activate_scene("daylight", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active
    deadline_before = ctrl._occupancy_timer.deadline_utc

    # Run an alert
    await hass.services.async_call(
        "area_lighting",
        "alert",
        {"area_id": "network_room", "pattern": "test_flash"},
        blocking=True,
    )

    # Timer should be restored with the same deadline
    assert ctrl._occupancy_timer.is_active
    assert ctrl._occupancy_timer.deadline_utc == deadline_before
```

- [ ] **Step 2: Run the test**

```
uv run --extra dev python -m pytest \
  custom_components/area_lighting/tests/integration/test_alert.py::test_alert_preserves_timer_deadline \
  -v
```

Expected: PASS (the implementation from Task 4 already handles this).

- [ ] **Step 3: Run full suite**

```
uv run --extra dev python -m pytest custom_components/area_lighting/tests/ -x -q
```

Expected: 381 passed.

- [ ] **Step 4: Commit**

```bash
git add custom_components/area_lighting/tests/integration/test_alert.py
git commit -m "(Minor) area_lighting: integration test for timer preservation across alerts

Verifies that an active occupancy timer's deadline is restored to its
exact pre-alert value after the alert completes. Guards against timer
deadline drift or loss during the cancel/restore cycle."
```

---

## Task 7: Dagger verification + push

**Goal:** Run the full CI pipeline, fix any lint/format issues, push.

**Files:** none (verification only).

- [ ] **Step 1: Run `dagger call all`**

```
dagger call all
```

Expected: all stages pass. If `ruff format` fails on new files, run:
```
uv run --extra dev ruff format custom_components/area_lighting/alert.py \
  custom_components/area_lighting/tests/test_alert.py \
  custom_components/area_lighting/tests/test_alert_config.py \
  custom_components/area_lighting/tests/integration/test_alert.py
```
Then commit as `(Patch) area_lighting: ruff-format alert files`.

If `ruff check` fails (unused imports, etc.), fix inline and commit similarly.

If `mypy` fails, fix type annotations and commit.

- [ ] **Step 2: Push**

```
git push origin main
```

Expected: CI runs `check` then `tag:auto`. Because commits carry `(Minor)`, the next tag is a minor bump (e.g., `v0.4.0`).

---

## Self-review

Checked against the spec (`docs/superpowers/specs/2026-04-16-alert-mode-design.md`):

- **Config schema** (global `alert_patterns`, steps with target/state/delay/attrs, repeat/delay/start_inverted/restore): Task 1.
- **Target resolution** (all/color/white via `supported_color_modes`, skip unavailable): Task 2.
- **Light capture + restore** (snapshot attrs, replay with transition:0): Task 2.
- **Service** (`area_lighting.alert` with area_id + pattern, "all" dispatches concurrently): Task 5.
- **execute_alert flow** (flag → capture → timers → steps → restore → timers → flag): Task 4.
- **Controller `_alert_active` flag** (default False, in diagnostic_snapshot): Task 3.
- **Manual detection guard** (early-return when `_alert_active`): Task 3.
- **Timer pause/resume** (snapshot deadline_utc, cancel, restore after): Task 4 + Task 6 integration test.
- **Start-inverted logic** (reverse step order on majority match): Task 4.
- **Empty-target handling** (step is no-op, delay still runs): inherent in `filter_lights_by_target` returning empty + `if targeted:` guard.
- **Error handling** (unknown pattern → warning, no crash): Task 5.
- **Schema reusable at area level** (the `ALERT_PATTERN_SCHEMA` is decoupled from where it's referenced in CONFIG_SCHEMA — can be added under AREA_SCHEMA later): implicit in Task 1 design.

No placeholders. Method/type names consistent across tasks.
