# Circadian Kelvin Routes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-area `circadian_kelvin_routes:` config that, while the `circadian` scene is active, dispatches a configured set of lights between mutually-exclusive routes based on the per-area circadian switch's live `colortemp` attribute.

**Architecture:** A new `circadian_kelvin_router.py` module owns route-selection logic (pure function with hysteresis) plus a small stateful class that registers/deregisters a `colortemp` listener and reconciles light state via `light.turn_on` / `light.turn_off` with a configurable crossfade. The router is instantiated per controller iff the area declares routes. The controller's existing circadian-activation loop is amended to skip lights owned by routes, and four state-transition sites call `await router.sync_to_state(slug)` to (de)register the listener and trigger reconciliation.

**Tech Stack:** Python 3.13, Home Assistant custom component, voluptuous for schema validation, pytest + `pytest-homeassistant-custom-component`. Tests live under `custom_components/area_lighting/tests/` and are run with `uv run pytest -n auto`.

**Spec:** `docs/superpowers/specs/2026-05-17-circadian-kelvin-routes-design.md`

---

## File structure

| File | Responsibility | Change |
| --- | --- | --- |
| `custom_components/area_lighting/const.py` | Hysteresis constant + crossfade default | Modify |
| `custom_components/area_lighting/models.py` | `CircadianKelvinRouteConfig`, `CircadianKelvinRoutesConfig`, `AreaConfig` field | Modify |
| `custom_components/area_lighting/config_schema.py` | Voluptuous schema, parse-path wiring, cross-cutting validator function | Modify |
| `custom_components/area_lighting/__init__.py` | Call the new validator after `parse_config` | Modify |
| `custom_components/area_lighting/circadian_kelvin_router.py` | Pure route-selection function + stateful `CircadianKelvinRouter` class | Create |
| `custom_components/area_lighting/controller.py` | Construct the router, skip routed lights in the circadian loop, call `sync_to_state` from state transitions | Modify |
| `custom_components/area_lighting/tests/test_circadian_kelvin_router.py` | Pure-unit tests for `select_route` | Create |
| `custom_components/area_lighting/tests/test_circadian_kelvin_routes_schema.py` | Parse-path + cross-cutting validator tests | Create |
| `custom_components/area_lighting/tests/integration/test_circadian_kelvin_routes.py` | Integration tests for router + controller wiring | Create |
| `CONFIGURATION.md` | New section "Circadian kelvin routes" | Modify |
| `CHANGELOG.md` | Entry under next Minor release | Modify |
| `README.md` | One-line feature bullet | Modify |

---

## Notes for the implementer

- **Run the tests before you start.** From `custom_components/area_lighting/`:

  ```sh
  uv sync --extra dev
  uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto
  ```

  All tests should pass on `main`.
- **CI parity.** This repo's CI runs both `ruff check` AND `ruff format --check`. Run both locally before pushing, or `dagger call all` if Docker is available.
- **Commit-message format.** Every subject must start with `(Major)`, `(Minor)`, or `(Patch)`. This feature is **Minor**.
- **No version bumps.** Don't edit `pyproject.toml` / `manifest.json` / `uv.lock` versions in content commits — the `tag:auto` CI job handles that.
- **No `Co-Authored-By: Claude` trailers.** Project policy.
- **TDD order.** Every task writes the failing test before the production code.
- **Line-number drift.** Line numbers cited below are from `main` as of this plan. If the file has drifted, use the surrounding string context instead.

---

## Task 1: Add constants

**Goal:** Introduce `CIRCADIAN_KELVIN_HYSTERESIS` and `DEFAULT_CIRCADIAN_KELVIN_CROSSFADE_SECONDS` in `const.py`. Trivial, no test of its own — used by later tasks.

**Files:**
- Modify: `custom_components/area_lighting/const.py` (append at end of file)

- [ ] **Step 1: Add the constants**

Append to `custom_components/area_lighting/const.py`:

```python
# Circadian kelvin routing
CIRCADIAN_KELVIN_HYSTERESIS = 25
DEFAULT_CIRCADIAN_KELVIN_CROSSFADE_SECONDS = 2.0
```

- [ ] **Step 2: Verify imports still pass**

Run: `cd custom_components/area_lighting && uv run python -c "from custom_components.area_lighting import const; print(const.CIRCADIAN_KELVIN_HYSTERESIS, const.DEFAULT_CIRCADIAN_KELVIN_CROSSFADE_SECONDS)"`

Expected output: `25 2.0`

- [ ] **Step 3: Commit**

```sh
git add custom_components/area_lighting/const.py
git commit -m "(Minor) area_lighting: add CIRCADIAN_KELVIN_HYSTERESIS and crossfade default constants"
```

---

## Task 2: Add dataclasses

**Goal:** Add `CircadianKelvinRouteConfig` and `CircadianKelvinRoutesConfig` dataclasses, plus the field on `AreaConfig`.

**Files:**
- Modify: `custom_components/area_lighting/models.py` (add dataclasses after `LinkedMotionConfig` near line 130, add field on `AreaConfig` near line 192)
- Test: `custom_components/area_lighting/tests/test_circadian_kelvin_router.py` (create — used for unit tests across this task and Task 5)

- [ ] **Step 1: Write the failing model tests**

Create `custom_components/area_lighting/tests/test_circadian_kelvin_router.py`:

```python
"""Unit tests for circadian-kelvin route data models and selection logic."""

from __future__ import annotations

from custom_components.area_lighting.models import (
    CircadianKelvinRouteConfig,
    CircadianKelvinRoutesConfig,
)


def test_banded_route_is_not_fallback():
    route = CircadianKelvinRouteConfig(
        lights=["light.foo"], kelvin_range=(4500, 5500)
    )
    assert route.is_fallback is False


def test_fallback_route_has_no_range():
    route = CircadianKelvinRouteConfig(lights=["light.bar", "light.baz"])
    assert route.is_fallback is True
    assert route.kelvin_range is None


def test_routes_config_exposes_fallback_and_all_lights():
    banded = CircadianKelvinRouteConfig(
        lights=["light.fluor"], kelvin_range=(4500, 5500)
    )
    fallback = CircadianKelvinRouteConfig(
        lights=["light.strip_1", "light.strip_2"]
    )
    cfg = CircadianKelvinRoutesConfig(
        routes=[banded, fallback],
        source="switch.circadian_kitchen",
        crossfade_seconds=2.0,
    )
    assert cfg.fallback_route is fallback
    assert cfg.all_route_lights == {
        "light.fluor",
        "light.strip_1",
        "light.strip_2",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd custom_components/area_lighting && uv run pytest tests/test_circadian_kelvin_router.py -v`

Expected: FAIL with `ImportError: cannot import name 'CircadianKelvinRouteConfig'`

- [ ] **Step 3: Add the dataclasses to `models.py`**

Add this block to `custom_components/area_lighting/models.py` immediately after the `LinkedMotionConfig` class (around line 130):

```python
@dataclass
class CircadianKelvinRouteConfig:
    """A single route within a CircadianKelvinRoutesConfig.

    Banded routes have a (lo, hi) kelvin_range; the fallback route
    leaves kelvin_range as None and is selected when no banded route
    matches the current colortemp.
    """

    lights: list[str]
    kelvin_range: tuple[int, int] | None = None

    @property
    def is_fallback(self) -> bool:
        return self.kelvin_range is None


@dataclass
class CircadianKelvinRoutesConfig:
    """Per-area circadian kelvin routing config.

    While the area's active scene is `circadian`, the controller's
    router subscribes to `source` and dispatches the lights listed
    across `routes` based on the source's `colortemp` attribute.
    """

    routes: list[CircadianKelvinRouteConfig]
    source: str
    crossfade_seconds: float

    @property
    def fallback_route(self) -> CircadianKelvinRouteConfig:
        return next(r for r in self.routes if r.is_fallback)

    @property
    def all_route_lights(self) -> set[str]:
        out: set[str] = set()
        for r in self.routes:
            out.update(r.lights)
        return out
```

- [ ] **Step 4: Add field on `AreaConfig`**

In `custom_components/area_lighting/models.py`, inside the `AreaConfig` dataclass body, add a new field after `linked_motion: list[LinkedMotionConfig]` (around line 192):

```python
    circadian_kelvin_routes: CircadianKelvinRoutesConfig | None = None
```

- [ ] **Step 5: Run the test, verify pass**

Run: `cd custom_components/area_lighting && uv run pytest tests/test_circadian_kelvin_router.py -v`

Expected: 3 passed.

- [ ] **Step 6: Lint + commit**

```sh
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check .
cd ../.. && git add custom_components/area_lighting/models.py custom_components/area_lighting/tests/test_circadian_kelvin_router.py
git commit -m "(Minor) area_lighting: add CircadianKelvinRoutesConfig data model"
```

---

## Task 3: Voluptuous schema and parse path

**Goal:** Add the voluptuous schema for `circadian_kelvin_routes`, wire it into `parse_config`. No cross-cutting validation yet (that's Task 4).

**Files:**
- Modify: `custom_components/area_lighting/config_schema.py` (add schemas near line 55 next to `SCENE_SCHEMA`; extend `AREA_SCHEMA` near line 195; extend `parse_config` near line 340)
- Test: `custom_components/area_lighting/tests/test_circadian_kelvin_routes_schema.py` (create)

- [ ] **Step 1: Write the failing parse test**

Create `custom_components/area_lighting/tests/test_circadian_kelvin_routes_schema.py`:

```python
"""Schema validation tests for circadian_kelvin_routes."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting.config_schema import AREA_SCHEMA, parse_config


def _minimum_area_dict(**overrides):
    base = {
        "id": "kitchen",
        "name": "Kitchen",
        "circadian_switches": [{"name": "Kitchen"}],
        "lights": [
            {"id": "light.kitchen_fluorescent", "circadian_switch": "Kitchen"},
            {"id": "light.kitchen_strip_1", "circadian_switch": "Kitchen"},
            {"id": "light.kitchen_strip_2", "circadian_switch": "Kitchen"},
        ],
        "scenes": [{"id": "circadian", "name": "Circadian"}],
    }
    base.update(overrides)
    return base


def test_minimum_valid_routes_parses():
    area = _minimum_area_dict(
        circadian_kelvin_routes={
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1", "light.kitchen_strip_2"]},
            ]
        }
    )
    validated = AREA_SCHEMA(area)
    config = parse_config({"areas": [validated]})
    routes = config.areas[0].circadian_kelvin_routes
    assert routes is not None
    assert len(routes.routes) == 2
    assert routes.routes[0].kelvin_range == (4500, 5500)
    assert routes.routes[0].lights == ["light.kitchen_fluorescent"]
    assert routes.routes[1].kelvin_range is None
    assert routes.routes[1].lights == [
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
    ]


def test_explicit_source_and_crossfade_round_trip():
    area = _minimum_area_dict(
        circadian_kelvin_routes={
            "source": "sensor.circadian_values",
            "crossfade_seconds": 5.0,
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ],
        }
    )
    validated = AREA_SCHEMA(area)
    config = parse_config({"areas": [validated]})
    routes = config.areas[0].circadian_kelvin_routes
    assert routes.source == "sensor.circadian_values"
    assert routes.crossfade_seconds == 5.0


def test_omitting_circadian_kelvin_routes_yields_none():
    area = _minimum_area_dict()
    validated = AREA_SCHEMA(area)
    config = parse_config({"areas": [validated]})
    assert config.areas[0].circadian_kelvin_routes is None


def test_route_with_kelvin_range_below_minimum_rejected():
    area = _minimum_area_dict(
        circadian_kelvin_routes={
            "routes": [
                {
                    "kelvin_range": [999, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ]
        }
    )
    with pytest.raises(vol.Invalid):
        AREA_SCHEMA(area)


def test_route_with_negative_crossfade_rejected():
    area = _minimum_area_dict(
        circadian_kelvin_routes={
            "crossfade_seconds": -1.0,
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ],
        }
    )
    with pytest.raises(vol.Invalid):
        AREA_SCHEMA(area)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd custom_components/area_lighting && uv run pytest tests/test_circadian_kelvin_routes_schema.py -v`

Expected: errors at schema validation — `circadian_kelvin_routes` is `extra keys not allowed`.

- [ ] **Step 3: Add the schemas to `config_schema.py`**

In `custom_components/area_lighting/config_schema.py`, after the `SCENE_SCHEMA` block (around line 55), add:

```python
CIRCADIAN_KELVIN_ROUTE_SCHEMA = vol.Schema(
    {
        vol.Optional("kelvin_range"): vol.All(
            vol.ExactSequence(
                [
                    vol.All(int, vol.Range(min=1000, max=10000)),
                    vol.All(int, vol.Range(min=1000, max=10000)),
                ]
            ),
        ),
        vol.Required("lights"): vol.All(
            cv.ensure_list, vol.Length(min=1), [cv.entity_id]
        ),
    }
)

CIRCADIAN_KELVIN_ROUTES_SCHEMA = vol.Schema(
    {
        vol.Optional("source"): cv.entity_id,
        vol.Optional("crossfade_seconds"): vol.All(
            vol.Coerce(float), vol.Range(min=0)
        ),
        vol.Required("routes"): vol.All(
            cv.ensure_list,
            vol.Length(min=2),
            [CIRCADIAN_KELVIN_ROUTE_SCHEMA],
        ),
    }
)
```

- [ ] **Step 4: Extend `AREA_SCHEMA`**

In the same file, inside the `AREA_SCHEMA` definition (around line 166-200), add this entry immediately after the `linked_motion` line:

```python
        vol.Optional("circadian_kelvin_routes"): CIRCADIAN_KELVIN_ROUTES_SCHEMA,
```

- [ ] **Step 5: Add imports and parse-path branch in `parse_config`**

At the top of `config_schema.py`, extend the import from `.const` (around line 8) to include the new defaults:

```python
from .const import (
    ALL_ROLES,
    CIRCADIAN_BRIGHTNESS,
    CIRCADIAN_CT,
    CIRCADIAN_RGB,
    DEFAULT_CIRCADIAN_KELVIN_CROSSFADE_SECONDS,
)
```

Extend the `.models` import (around line 9) to include the new dataclasses:

```python
from .models import (
    AlertPattern,
    AlertStep,
    AreaConfig,
    AreaLightingConfig,
    CircadianKelvinRouteConfig,
    CircadianKelvinRoutesConfig,
    CircadianSwitchConfig,
    LightConfig,
    LinkedMotionConfig,
    LinkedMotionMapping,
    LutronRemoteConfig,
    MotionLightCondition,
    SceneConfig,
)
```

Inside `parse_config`, just above the `areas.append(...)` call (around line 340), add:

```python
        ckr_raw = area_raw.get("circadian_kelvin_routes")
        if ckr_raw is None:
            circadian_kelvin_routes = None
        else:
            parsed_routes = [
                CircadianKelvinRouteConfig(
                    lights=list(r["lights"]),
                    kelvin_range=(
                        tuple(r["kelvin_range"])
                        if "kelvin_range" in r
                        else None
                    ),
                )
                for r in ckr_raw["routes"]
            ]
            circadian_kelvin_routes = CircadianKelvinRoutesConfig(
                routes=parsed_routes,
                source=ckr_raw.get("source", ""),
                crossfade_seconds=ckr_raw.get(
                    "crossfade_seconds",
                    DEFAULT_CIRCADIAN_KELVIN_CROSSFADE_SECONDS,
                ),
            )
```

Add `circadian_kelvin_routes=circadian_kelvin_routes,` to the keyword arguments of `AreaConfig(...)` (around line 340-365), e.g. on the line after `follow_leader_deactivation=...,`.

- [ ] **Step 6: Run the test, verify pass**

Run: `cd custom_components/area_lighting && uv run pytest tests/test_circadian_kelvin_routes_schema.py -v`

Expected: 5 passed.

- [ ] **Step 7: Run the full test suite for regression**

Run: `cd custom_components/area_lighting && uv run pytest -n auto`

Expected: all previous tests still pass.

- [ ] **Step 8: Lint + commit**

```sh
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check .
cd ../.. && git add custom_components/area_lighting/config_schema.py custom_components/area_lighting/tests/test_circadian_kelvin_routes_schema.py
git commit -m "(Minor) area_lighting: voluptuous schema for circadian_kelvin_routes"
```

---

## Task 4: Cross-cutting validator

**Goal:** Add `validate_circadian_kelvin_routes(config)` that enforces fallback count, overlap, light membership, single-route ownership, and `source` resolution rules. Wire it into `__init__.py` next to `validate_leader_follower_graph`.

**Files:**
- Modify: `custom_components/area_lighting/config_schema.py` (append new validator at the end)
- Modify: `custom_components/area_lighting/__init__.py` (call the validator after `parse_config`)
- Test: `custom_components/area_lighting/tests/test_circadian_kelvin_routes_schema.py` (extend)

- [ ] **Step 1: Write the failing validator tests**

Append to `custom_components/area_lighting/tests/test_circadian_kelvin_routes_schema.py`:

```python
from custom_components.area_lighting.config_schema import (
    validate_circadian_kelvin_routes,
)


def _parsed_config(area_overrides=None):
    area = _minimum_area_dict(**(area_overrides or {}))
    validated = AREA_SCHEMA(area)
    return parse_config({"areas": [validated]})


def test_validator_accepts_minimum_valid():
    config = _parsed_config(
        circadian_kelvin_routes={
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ]
        }
    )
    validate_circadian_kelvin_routes(config)  # does not raise


def test_validator_rejects_zero_fallbacks():
    config = _parsed_config(
        circadian_kelvin_routes={
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {
                    "kelvin_range": [5500, 6500],
                    "lights": ["light.kitchen_strip_1"],
                },
            ]
        }
    )
    with pytest.raises(vol.Invalid, match="exactly one fallback"):
        validate_circadian_kelvin_routes(config)


def test_validator_rejects_two_fallbacks():
    config = _parsed_config(
        circadian_kelvin_routes={
            "routes": [
                {"lights": ["light.kitchen_fluorescent"]},
                {"lights": ["light.kitchen_strip_1"]},
            ]
        }
    )
    with pytest.raises(vol.Invalid, match="exactly one fallback"):
        validate_circadian_kelvin_routes(config)


def test_validator_rejects_inverted_kelvin_range():
    config = _parsed_config(
        circadian_kelvin_routes={
            "routes": [
                {
                    "kelvin_range": [5500, 4500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ]
        }
    )
    with pytest.raises(vol.Invalid, match="lo .* must be .*hi"):
        validate_circadian_kelvin_routes(config)


def test_validator_rejects_overlapping_ranges():
    config = _parsed_config(
        circadian_kelvin_routes={
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {
                    "kelvin_range": [5500, 6500],
                    "lights": ["light.kitchen_strip_1"],
                },
                {"lights": ["light.kitchen_strip_2"]},
            ]
        }
    )
    with pytest.raises(vol.Invalid, match="overlap"):
        validate_circadian_kelvin_routes(config)


def test_validator_rejects_light_not_in_area():
    config = _parsed_config(
        circadian_kelvin_routes={
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.does_not_exist"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ]
        }
    )
    with pytest.raises(vol.Invalid, match="not declared in area"):
        validate_circadian_kelvin_routes(config)


def test_validator_rejects_light_in_two_routes():
    config = _parsed_config(
        circadian_kelvin_routes={
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {
                    "kelvin_range": [6000, 7000],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ]
        }
    )
    with pytest.raises(vol.Invalid, match="more than one route"):
        validate_circadian_kelvin_routes(config)


def test_validator_defaults_source_to_only_circadian_switch():
    config = _parsed_config(
        circadian_kelvin_routes={
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ]
        }
    )
    validate_circadian_kelvin_routes(config)
    assert (
        config.areas[0].circadian_kelvin_routes.source
        == "switch.circadian_lighting_kitchen_kitchen_circadian"
    )


def test_validator_requires_explicit_source_when_two_switches():
    area = _minimum_area_dict(
        circadian_switches=[{"name": "A"}, {"name": "B"}],
        circadian_kelvin_routes={
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ]
        },
    )
    validated = AREA_SCHEMA(area)
    config = parse_config({"areas": [validated]})
    with pytest.raises(vol.Invalid, match="must specify 'source'"):
        validate_circadian_kelvin_routes(config)


def test_validator_requires_explicit_source_when_no_switches():
    area = {
        "id": "kitchen",
        "name": "Kitchen",
        "lights": [
            {"id": "light.kitchen_fluorescent"},
            {"id": "light.kitchen_strip_1"},
        ],
        "scenes": [{"id": "circadian", "name": "Circadian"}],
        "circadian_kelvin_routes": {
            "routes": [
                {
                    "kelvin_range": [4500, 5500],
                    "lights": ["light.kitchen_fluorescent"],
                },
                {"lights": ["light.kitchen_strip_1"]},
            ]
        },
    }
    validated = AREA_SCHEMA(area)
    config = parse_config({"areas": [validated]})
    with pytest.raises(vol.Invalid, match="must specify 'source'"):
        validate_circadian_kelvin_routes(config)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd custom_components/area_lighting && uv run pytest tests/test_circadian_kelvin_routes_schema.py -v`

Expected: `ImportError: cannot import name 'validate_circadian_kelvin_routes'`.

- [ ] **Step 3: Implement the validator**

Append to `custom_components/area_lighting/config_schema.py` (after `validate_leader_follower_graph`):

```python
def validate_circadian_kelvin_routes(config: AreaLightingConfig) -> None:
    """Enforce semantic rules on circadian_kelvin_routes and resolve `source`.

    For each area that declares `circadian_kelvin_routes`:
      - exactly one route must omit `kelvin_range` (the fallback)
      - banded ranges must satisfy lo <= hi
      - no two banded ranges may overlap (touching endpoints overlap)
      - every entity id in `routes[].lights` must appear in the area's
        `lights` or `light_clusters`
      - no entity id may appear in more than one route
      - `source` defaults to the area's sole circadian switch when one
        is configured; otherwise it must be supplied explicitly
    """
    for area in config.areas:
        ckr = area.circadian_kelvin_routes
        if ckr is None:
            continue

        fallbacks = [r for r in ckr.routes if r.is_fallback]
        if len(fallbacks) != 1:
            raise vol.Invalid(
                f"area '{area.id}': circadian_kelvin_routes must have "
                f"exactly one fallback route (got {len(fallbacks)})"
            )

        banded = [r for r in ckr.routes if not r.is_fallback]
        for r in banded:
            lo, hi = r.kelvin_range  # type: ignore[misc]
            if lo > hi:
                raise vol.Invalid(
                    f"area '{area.id}': circadian_kelvin_routes: "
                    f"kelvin_range lo ({lo}) must be <= hi ({hi})"
                )
        for i, a in enumerate(banded):
            for b in banded[i + 1 :]:
                a_lo, a_hi = a.kelvin_range  # type: ignore[misc]
                b_lo, b_hi = b.kelvin_range  # type: ignore[misc]
                if a_lo <= b_hi and b_lo <= a_hi:
                    raise vol.Invalid(
                        f"area '{area.id}': circadian_kelvin_routes: "
                        f"ranges [{a_lo}, {a_hi}] and [{b_lo}, {b_hi}] overlap"
                    )

        declared = {light.id for light in area.all_lights}
        seen: set[str] = set()
        for r in ckr.routes:
            for entity_id in r.lights:
                if entity_id not in declared:
                    raise vol.Invalid(
                        f"area '{area.id}': circadian_kelvin_routes: "
                        f"light '{entity_id}' is not declared in area's "
                        f"lights or light_clusters"
                    )
                if entity_id in seen:
                    raise vol.Invalid(
                        f"area '{area.id}': circadian_kelvin_routes: "
                        f"light '{entity_id}' appears in more than one route"
                    )
                seen.add(entity_id)

        if not ckr.source:
            if len(area.circadian_switches) == 1:
                ckr.source = area.circadian_switches[0].entity_id
            else:
                raise vol.Invalid(
                    f"area '{area.id}': circadian_kelvin_routes must "
                    f"specify 'source' when the area declares "
                    f"{len(area.circadian_switches)} circadian switches"
                )
```

- [ ] **Step 4: Wire the validator into `__init__.py`**

In `custom_components/area_lighting/__init__.py`, extend the `.config_schema` import (around line 18-19):

```python
from .config_schema import (
    parse_config,
    validate_circadian_kelvin_routes,
    validate_leader_follower_graph,
)
```

Add a second `try`/`except` block immediately after the existing `validate_leader_follower_graph` block (around line 73-77):

```python
    try:
        validate_circadian_kelvin_routes(area_config)
    except vol.Invalid as err:
        _LOGGER.error(
            "Area Lighting: invalid circadian_kelvin_routes config: %s", err
        )
        return False
```

Also add the same `try`/`except` to the reload path (around line 145) immediately after its `validate_leader_follower_graph` call.

- [ ] **Step 5: Run the tests, verify pass**

Run: `cd custom_components/area_lighting && uv run pytest tests/test_circadian_kelvin_routes_schema.py -v`

Expected: 14 passed (5 from Task 3 + 9 new).

- [ ] **Step 6: Run full suite for regression**

Run: `cd custom_components/area_lighting && uv run pytest -n auto`

Expected: all tests pass.

- [ ] **Step 7: Lint + commit**

```sh
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check .
cd ../.. && git add custom_components/area_lighting/config_schema.py custom_components/area_lighting/__init__.py custom_components/area_lighting/tests/test_circadian_kelvin_routes_schema.py
git commit -m "(Minor) area_lighting: validate circadian_kelvin_routes semantics"
```

---

## Task 5: Pure route-selection function with hysteresis

**Goal:** Add a pure function `select_route(routes, colortemp, current_route_index)` that returns the index of the route to be active. Pure Python, no HA dependency. Includes hysteresis at boundaries.

**Files:**
- Create: `custom_components/area_lighting/circadian_kelvin_router.py`
- Test: `custom_components/area_lighting/tests/test_circadian_kelvin_router.py` (extend)

- [ ] **Step 1: Write the failing selector tests**

Append to `custom_components/area_lighting/tests/test_circadian_kelvin_router.py`:

```python
from custom_components.area_lighting.circadian_kelvin_router import select_route


def _routes():
    """Returns [banded_cool, banded_warm, fallback]."""
    return [
        CircadianKelvinRouteConfig(
            lights=["light.fluor"], kelvin_range=(4500, 5500)
        ),
        CircadianKelvinRouteConfig(
            lights=["light.warm_strip"], kelvin_range=(2700, 3500)
        ),
        CircadianKelvinRouteConfig(lights=["light.fallback_strip"]),
    ]


def test_selects_banded_route_when_in_range():
    assert select_route(_routes(), colortemp=5000, current_index=None) == 0


def test_selects_other_banded_route():
    assert select_route(_routes(), colortemp=3000, current_index=None) == 1


def test_selects_fallback_when_between_bands():
    assert select_route(_routes(), colortemp=4000, current_index=None) == 2


def test_selects_fallback_when_above_all_bands():
    assert select_route(_routes(), colortemp=6500, current_index=None) == 2


def test_selects_fallback_when_below_all_bands():
    assert select_route(_routes(), colortemp=2000, current_index=None) == 2


def test_selects_fallback_when_colortemp_is_none():
    assert select_route(_routes(), colortemp=None, current_index=0) == 2


def test_hysteresis_keeps_active_route_at_upper_edge():
    # current = banded_cool [4500, 5500]; colortemp = 5520 (within +25K)
    assert select_route(_routes(), colortemp=5520, current_index=0) == 0


def test_hysteresis_keeps_active_route_at_lower_edge():
    assert select_route(_routes(), colortemp=4480, current_index=0) == 0


def test_hysteresis_releases_route_when_clearly_outside():
    # 5526 > 5500 + 25 → not banded_cool any more. 5526 not in [2700, 3500].
    # No banded match → fallback.
    assert select_route(_routes(), colortemp=5526, current_index=0) == 2


def test_no_hysteresis_when_entering_new_banded_route():
    # current = fallback. Entering banded_cool requires strict containment.
    # 5520 is outside [4500, 5500] without hysteresis grace for entry.
    assert select_route(_routes(), colortemp=5520, current_index=2) == 2


def test_fallback_only_when_one_route():
    # Degenerate case used by the validator-disallowed config, but the
    # selector should not crash.
    routes = [CircadianKelvinRouteConfig(lights=["light.x"])]
    assert select_route(routes, colortemp=5000, current_index=None) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd custom_components/area_lighting && uv run pytest tests/test_circadian_kelvin_router.py -v`

Expected: `ImportError: cannot import name 'select_route'`.

- [ ] **Step 3: Create the router module with `select_route`**

Create `custom_components/area_lighting/circadian_kelvin_router.py`:

```python
"""Circadian kelvin-routing for Area Lighting.

While the `circadian` scene is active in an area, this module's
`CircadianKelvinRouter` subscribes to a configured source entity's
`colortemp` attribute and dispatches the area's routed lights between
mutually-exclusive routes. The pure `select_route` function is split
out so it can be unit-tested without an HA harness.
"""

from __future__ import annotations

from typing import Sequence

from .const import CIRCADIAN_KELVIN_HYSTERESIS
from .models import CircadianKelvinRouteConfig


def select_route(
    routes: Sequence[CircadianKelvinRouteConfig],
    colortemp: float | None,
    current_index: int | None,
) -> int:
    """Pick the index of the route that should be active.

    Selection rules:
      - If `colortemp` is None (missing / unavailable), the fallback is
        selected.
      - The currently-active route (`current_index`) stays active while
        `colortemp` is within its declared range expanded by
        CIRCADIAN_KELVIN_HYSTERESIS on each side.
      - Otherwise the first banded route whose strict range contains
        `colortemp` is selected.
      - If no banded route matches, the fallback is selected.
      - The fallback's index is returned when no other route matches.
        If no fallback exists (degenerate input), the first route is
        returned.
    """
    fallback_index = next(
        (i for i, r in enumerate(routes) if r.is_fallback), 0
    )
    if colortemp is None:
        return fallback_index

    if (
        current_index is not None
        and 0 <= current_index < len(routes)
        and not routes[current_index].is_fallback
    ):
        lo, hi = routes[current_index].kelvin_range  # type: ignore[misc]
        if (lo - CIRCADIAN_KELVIN_HYSTERESIS) <= colortemp <= (
            hi + CIRCADIAN_KELVIN_HYSTERESIS
        ):
            return current_index

    for i, route in enumerate(routes):
        if route.is_fallback:
            continue
        lo, hi = route.kelvin_range  # type: ignore[misc]
        if lo <= colortemp <= hi:
            return i

    return fallback_index
```

- [ ] **Step 4: Run the tests, verify pass**

Run: `cd custom_components/area_lighting && uv run pytest tests/test_circadian_kelvin_router.py -v`

Expected: 14 passed (3 model + 11 selector).

- [ ] **Step 5: Lint + commit**

```sh
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check .
cd ../.. && git add custom_components/area_lighting/circadian_kelvin_router.py custom_components/area_lighting/tests/test_circadian_kelvin_router.py
git commit -m "(Minor) area_lighting: pure select_route function with hysteresis"
```

---

## Task 6: Stateful router class with HA integration

**Goal:** Add `CircadianKelvinRouter` class to the new module. Owns the source listener, current-route bookkeeping, and reconciliation via `light.turn_on/turn_off`.

**Files:**
- Modify: `custom_components/area_lighting/circadian_kelvin_router.py`
- Test: `custom_components/area_lighting/tests/integration/test_circadian_kelvin_routes.py` (create)

- [ ] **Step 1: Write the failing integration tests**

Create `custom_components/area_lighting/tests/integration/test_circadian_kelvin_routes.py`:

```python
"""Integration tests for circadian_kelvin_routes (router + controller wiring).

The kitchen fixture has one fluorescent (banded 4500-5500K) and three
lightstrips (fallback). Tests assert which entities `light.turn_on` and
`light.turn_off` get called for, in response to source state changes.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.fixture
def kitchen_with_routes_config() -> dict:
    """Kitchen with one fluorescent banded [4500, 5500] and 3 strips as fallback."""
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
                        {
                            "id": "light.kitchen_fluorescent",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_1",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_2",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_3",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                    "circadian_kelvin_routes": {
                        "crossfade_seconds": 1.0,
                        "routes": [
                            {
                                "kelvin_range": [4500, 5500],
                                "lights": ["light.kitchen_fluorescent"],
                            },
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


@pytest.fixture
def _stub_kitchen_entities(hass: HomeAssistant):
    """Pre-populate light + switch states the validator expects."""
    hass.states.async_set("light.kitchen_fluorescent", "off", {})
    hass.states.async_set("light.kitchen_strip_1", "off", {})
    hass.states.async_set("light.kitchen_strip_2", "off", {})
    hass.states.async_set("light.kitchen_strip_3", "off", {})
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "off",
        {"brightness": 75.0, "colortemp": 3000},
    )


@pytest.mark.integration
async def test_entering_circadian_activates_route_for_current_colortemp(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    _stub_kitchen_entities,
    kitchen_with_routes_config,
) -> None:
    """colortemp=5000 → fluorescent on, strips off."""
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 5000},
    )
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    on_targets = {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == "turn_on"
    }
    off_targets = {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == "turn_off"
    }
    assert "light.kitchen_fluorescent" in on_targets
    assert {
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    } <= off_targets


@pytest.mark.integration
async def test_colortemp_change_swaps_active_route(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    _stub_kitchen_entities,
    kitchen_with_routes_config,
) -> None:
    """Start at colortemp=5000 (fluorescent), then move to 3000 (strips)."""
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 5000},
    )
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    service_calls.clear()
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 3000},
    )
    await hass.async_block_till_done()

    on_targets = {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == "turn_on"
    }
    off_targets = {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == "turn_off"
    }
    assert "light.kitchen_fluorescent" in off_targets
    assert {
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    } <= on_targets


@pytest.mark.integration
async def test_hysteresis_suppresses_flap_at_boundary(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    _stub_kitchen_entities,
    kitchen_with_routes_config,
) -> None:
    """Once banded, small overshoots within hysteresis must not swap."""
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 5000},
    )
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    service_calls.clear()
    # Nudge to 5510 (within +25K) — still banded.
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 5510},
    )
    await hass.async_block_till_done()
    nudge_targets = {
        (call.domain, call.service, call.data.get("entity_id"))
        for call in service_calls
    }
    # No fluorescent off, no strip on
    assert ("light", "turn_off", "light.kitchen_fluorescent") not in nudge_targets
    assert ("light", "turn_on", "light.kitchen_strip_1") not in nudge_targets


@pytest.mark.integration
async def test_source_unavailable_selects_fallback(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    _stub_kitchen_entities,
    kitchen_with_routes_config,
) -> None:
    """If colortemp attribute is missing, fallback (strips) is selected."""
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "unavailable",
        {},
    )
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    on_targets = {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == "turn_on"
    }
    off_targets = {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == "turn_off"
    }
    assert {
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    } <= on_targets
    assert "light.kitchen_fluorescent" in off_targets


@pytest.mark.integration
async def test_listener_inactive_outside_circadian(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    _stub_kitchen_entities,
    kitchen_with_routes_config,
) -> None:
    """colortemp changes while in 'off' must not trigger turn_on/off calls."""
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 5000},
    )
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    await ctrl.lighting_off()
    await hass.async_block_till_done()

    service_calls.clear()
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 3000},
    )
    await hass.async_block_till_done()

    # No light service calls fired as a result of the colortemp change.
    routed_targets = {
        "light.kitchen_fluorescent",
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    }
    triggered = [
        call
        for call in service_calls
        if call.domain == "light"
        and call.data.get("entity_id") in routed_targets
    ]
    assert triggered == []


@pytest.mark.integration
async def test_crossfade_passed_as_transition(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    _stub_kitchen_entities,
    kitchen_with_routes_config,
) -> None:
    """The configured crossfade_seconds is passed as `transition`."""
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 5000},
    )
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    routed_calls = [
        call
        for call in service_calls
        if call.domain == "light"
        and call.data.get("entity_id")
        in {
            "light.kitchen_fluorescent",
            "light.kitchen_strip_1",
        }
    ]
    assert routed_calls
    assert all(call.data.get("transition") == 1.0 for call in routed_calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd custom_components/area_lighting && uv run pytest tests/integration/test_circadian_kelvin_routes.py -v`

Expected: failures — `CircadianKelvinRouter` does not exist yet and controller has no wiring.

- [ ] **Step 3: Add the router class to `circadian_kelvin_router.py`**

Append to `custom_components/area_lighting/circadian_kelvin_router.py`:

```python
import asyncio
import logging
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .models import CircadianKelvinRoutesConfig

_LOGGER = logging.getLogger(__name__)


class CircadianKelvinRouter:
    """Per-area router that swaps routed lights based on a source's colortemp.

    Active only while the area is in the `circadian` scene. Outside of
    that, the state-change listener is deregistered and reconciliation
    is suppressed.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        area_id: str,
        config: CircadianKelvinRoutesConfig,
    ) -> None:
        self._hass = hass
        self._area_id = area_id
        self._config = config
        self._unsub: Any = None
        self._current_index: int | None = None
        self._reconcile_lock = asyncio.Lock()

    async def sync_to_state(self, scene_slug: str | None) -> None:
        """Called after every controller state transition.

        Registers / deregisters the listener and reconciles immediately
        on first entry to circadian.
        """
        if scene_slug == "circadian":
            if self._unsub is None:
                self._unsub = async_track_state_change_event(
                    self._hass,
                    [self._config.source],
                    self._on_source_changed,
                )
            await self._reconcile()
        else:
            if self._unsub is not None:
                self._unsub()
                self._unsub = None
            self._current_index = None

    @callback
    def _on_source_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """HA fires this for every state change on `source`."""
        self._hass.async_create_task(self._reconcile())

    async def _reconcile(self) -> None:
        """Reconcile light state against the active route, idempotently."""
        async with self._reconcile_lock:
            colortemp = self._read_colortemp()
            new_index = select_route(
                self._config.routes, colortemp, self._current_index
            )

            if new_index == self._current_index:
                return

            _LOGGER.debug(
                "Area %s: kelvin-router selecting route %d "
                "(colortemp=%s, prev=%s)",
                self._area_id,
                new_index,
                colortemp,
                self._current_index,
            )
            self._current_index = new_index
            active = self._config.routes[new_index]
            inactive_lights = self._config.all_route_lights - set(
                active.lights
            )

            tasks: list = []
            for entity_id in sorted(inactive_lights):
                tasks.append(
                    self._hass.services.async_call(
                        "light",
                        "turn_off",
                        {
                            "entity_id": entity_id,
                            "transition": self._config.crossfade_seconds,
                        },
                        blocking=True,
                    )
                )
            for entity_id in sorted(active.lights):
                tasks.append(
                    self._hass.services.async_call(
                        "light",
                        "turn_on",
                        {
                            "entity_id": entity_id,
                            "transition": self._config.crossfade_seconds,
                        },
                        blocking=True,
                    )
                )
            if tasks:
                await asyncio.gather(*tasks)

    def _read_colortemp(self) -> float | None:
        state = self._hass.states.get(self._config.source)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        raw = state.attributes.get("colortemp")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
```

- [ ] **Step 4: Run tests — they will still fail until controller wiring in Task 7**

Run: `cd custom_components/area_lighting && uv run pytest tests/integration/test_circadian_kelvin_routes.py -v`

Expected: still failing because the controller doesn't yet instantiate the router. Defer the green pass to Task 7.

- [ ] **Step 5: Lint + commit**

```sh
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check .
cd ../.. && git add custom_components/area_lighting/circadian_kelvin_router.py custom_components/area_lighting/tests/integration/test_circadian_kelvin_routes.py
git commit -m "(Minor) area_lighting: CircadianKelvinRouter class with reconciliation"
```

---

## Task 7: Controller wiring

**Goal:** Construct the router in `AreaLightingController.__init__` when the area has routes, skip routed lights in `_activate_circadian`'s per-light loop, and call `await self._sync_kelvin_router()` from every state-transition site. Integration tests from Task 6 should now pass.

**Files:**
- Modify: `custom_components/area_lighting/controller.py` (constructor near line 145; `_activate_circadian` near line 725; `_activate_scene` near line 672; `handle_scene_activated` near line 1342; `handle_lights_all_off` near line 1369; `handle_manual_light_change` near line 1384)

- [ ] **Step 1: Construct the router in `__init__`**

In `custom_components/area_lighting/controller.py`, extend the imports near line 29:

```python
from .circadian_kelvin_router import CircadianKelvinRouter
from .models import AreaConfig, AreaLightingConfig, SceneConfig
```

(The `.models` line is already present — add the new `from .circadian_kelvin_router` import on its own line above it.)

Immediately after the existing field initializations in `__init__` (after the `self.followers` line near line 145), add:

```python
        self._kelvin_router: CircadianKelvinRouter | None = None
        if area.circadian_kelvin_routes is not None:
            self._kelvin_router = CircadianKelvinRouter(
                hass,
                area.id,
                area.circadian_kelvin_routes,
            )
```

- [ ] **Step 2: Add the `_sync_kelvin_router` helper**

In `controller.py`, add this method near other helpers (a good spot is right after `_notify_state_change` around line 575):

```python
    async def _sync_kelvin_router(self) -> None:
        """Tell the kelvin router about the current scene. No-op if absent."""
        if self._kelvin_router is None:
            return
        await self._kelvin_router.sync_to_state(self._state.scene_slug)
```

- [ ] **Step 3: Skip routed lights in the circadian per-light loop**

In `_activate_circadian` (around lines 736-757), change the loop to skip lights that the router owns. Locate:

```python
        tasks: list = []
        for light in self.area.all_lights:
            if not light.circadian_switch:
                continue
            cs = self.area.circadian_switch_for_light(light)
```

Insert immediately after `for light in self.area.all_lights:`:

```python
            if (
                self.area.circadian_kelvin_routes is not None
                and light.id
                in self.area.circadian_kelvin_routes.all_route_lights
            ):
                continue
```

Then at the very end of `_activate_circadian` (after `await asyncio.gather(*tasks)` near line 757), add:

```python
        await self._sync_kelvin_router()
```

- [ ] **Step 4: Add the call to `_activate_scene` paths**

In `_activate_scene` (around lines 672-723):

- Inside the `SCENE_OFF_INTERNAL` branch (around line 695, before `return`):

  ```python
          await self._sync_kelvin_router()
          return
  ```

- The `SCENE_CIRCADIAN` branch is already covered because it delegates to `_activate_circadian` (which calls `_sync_kelvin_router`).
- Inside the visual-scene branch (after `self._notify_state_change()` around line 714 — before the `if source != ActivationSource.LEADER:` block):

  ```python
          await self._sync_kelvin_router()
  ```

- [ ] **Step 5: Add the call to other `handle_*` paths**

In `handle_scene_activated` (around lines 1342-1367), after `self._notify_state_change()`:

```python
        await self._sync_kelvin_router()
```

In `handle_lights_all_off` (around lines 1369-1382), after `self._notify_state_change()`:

```python
        await self._sync_kelvin_router()
```

In `handle_manual_light_change` (around lines 1384-1399), after the existing `self._enforce_occupancy_timer()` / `self._notify_state_change()` calls inside the `if not self._state.dimmed:` block, add:

```python
            await self._sync_kelvin_router()
```

- [ ] **Step 6: Run the integration tests, verify pass**

Run: `cd custom_components/area_lighting && uv run pytest tests/integration/test_circadian_kelvin_routes.py -v`

Expected: 6 passed.

- [ ] **Step 7: Run the full suite for regression**

Run: `cd custom_components/area_lighting && uv run pytest -n auto`

Expected: all tests pass. If a controller-level test fails because of route hooks, re-read the failing test and fix the wiring (most likely a state-transition site you missed).

- [ ] **Step 8: Lint + commit**

```sh
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check .
cd ../.. && git add custom_components/area_lighting/controller.py
git commit -m "(Minor) area_lighting: wire circadian kelvin router into controller"
```

---

## Task 8: Inert-config startup warning

**Goal:** Log a `WARNING` if an area has `circadian_kelvin_routes` configured but no `circadian` scene declared.

**Files:**
- Modify: `custom_components/area_lighting/__init__.py` (startup pass, around line 87 after `hass.data[DOMAIN] = {...}`)
- Test: `custom_components/area_lighting/tests/test_circadian_kelvin_routes_schema.py` (extend) — but as this is a startup-time integration concern, prefer adding to the integration test file

- [ ] **Step 1: Write the failing test**

Append to `custom_components/area_lighting/tests/integration/test_circadian_kelvin_routes.py`:

```python
@pytest.mark.integration
async def test_routes_without_circadian_scene_logs_warning(
    hass: HomeAssistant,
    helper_entities,
    _stub_kitchen_entities,
    caplog,
) -> None:
    """An area with routes but no `circadian` scene logs a warning at startup."""
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "event_handlers": True,
                    "circadian_switches": [{"name": "Kitchen"}],
                    "lights": [
                        {
                            "id": "light.kitchen_fluorescent",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_1",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                    ],
                    "scenes": [
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "off", "name": "Off"},
                    ],
                    "circadian_kelvin_routes": {
                        "routes": [
                            {
                                "kelvin_range": [4500, 5500],
                                "lights": ["light.kitchen_fluorescent"],
                            },
                            {"lights": ["light.kitchen_strip_1"]},
                        ]
                    },
                }
            ]
        }
    }
    import logging

    caplog.set_level(logging.WARNING, logger="custom_components.area_lighting")
    await _setup(hass, cfg)
    assert any(
        "circadian_kelvin_routes" in record.message
        and "no `circadian` scene" in record.message
        and "kitchen" in record.message
        for record in caplog.records
    )
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd custom_components/area_lighting && uv run pytest tests/integration/test_circadian_kelvin_routes.py::test_routes_without_circadian_scene_logs_warning -v`

Expected: FAIL (no such log line).

- [ ] **Step 3: Emit the warning from `async_setup`**

In `custom_components/area_lighting/__init__.py`, immediately after the second `try`/`except validate_circadian_kelvin_routes` block (added in Task 4), add:

```python
    for area in area_config.areas:
        if area.circadian_kelvin_routes is None:
            continue
        if "circadian" not in area.scene_slugs:
            _LOGGER.warning(
                "Area Lighting: area '%s' has circadian_kelvin_routes "
                "but no `circadian` scene declared — routing will be "
                "inert until a circadian scene is added",
                area.id,
            )
```

- [ ] **Step 4: Run the test, verify pass**

Run: `cd custom_components/area_lighting && uv run pytest tests/integration/test_circadian_kelvin_routes.py::test_routes_without_circadian_scene_logs_warning -v`

Expected: pass.

- [ ] **Step 5: Run full suite**

Run: `cd custom_components/area_lighting && uv run pytest -n auto`

Expected: all tests pass.

- [ ] **Step 6: Lint + commit**

```sh
cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check .
cd ../.. && git add custom_components/area_lighting/__init__.py custom_components/area_lighting/tests/integration/test_circadian_kelvin_routes.py
git commit -m "(Minor) area_lighting: warn when circadian_kelvin_routes lacks a circadian scene"
```

---

## Task 9: CONFIGURATION.md documentation

**Goal:** Add a "Circadian kelvin routes" section to `CONFIGURATION.md` covering the schema, semantics, validation rules, and the kitchen worked example.

**Files:**
- Modify: `CONFIGURATION.md` (insert after the existing "Light roles" subsection of "lights and light_clusters")

- [ ] **Step 1: Locate the insertion point**

Open `CONFIGURATION.md`. Find the end of the section titled `### lights and light_clusters` (it ends around line 154 after the "Light roles" table). Insert the new section immediately before `### scenes` (around line 156).

- [ ] **Step 2: Add the new section**

Insert this block between `### lights and light_clusters` and `### scenes`:

````markdown
### Circadian kelvin routes

Optional. When set, the listed lights are dispatched between
mutually-exclusive routes based on the current circadian target color
temperature — but only while the area's active scene is `circadian`.
Other scenes apply their `entities:` block as usual and ignore routing.

Use case: a fixture that mixes light sources with very different native
color temperatures (e.g. fluorescent tubes near 5000K and warm Hue
strips). The fluorescent runs when circadian's target is near its
native CT; the strips take over outside that band.

```yaml
circadian_kelvin_routes:
  source: switch.circadian_lighting_kitchen_kitchen_circadian   # optional
  crossfade_seconds: 2.0                                        # optional, default 2.0
  routes:
    - kelvin_range: [4500, 5500]
      lights: [light.kitchen_fluorescent]
    - lights:
        - light.kitchen_strip_1
        - light.kitchen_strip_2
        - light.kitchen_strip_3
```

| Key                  | Type                       | Required | Default                                                   | Notes |
|----------------------|----------------------------|----------|-----------------------------------------------------------|-------|
| `source`             | entity_id                  | conditionally | the area's sole circadian switch                  | Entity whose `colortemp` attribute drives route selection. Required when the area declares 2+ circadian switches or zero. |
| `crossfade_seconds`  | float `>= 0`               | no       | `2.0`                                                     | Passed as `transition` to the `light.turn_on` / `light.turn_off` calls when the active route changes. `0` snaps. |
| `routes`             | list of [route](#route)     | **yes**  | —                                                         | Mutually-exclusive route definitions. Must have ≥ 2 entries (one banded + one fallback). |

#### Route

| Key            | Type                       | Required | Notes |
|----------------|----------------------------|----------|-------|
| `kelvin_range` | `[lo, hi]` of ints `1000..10000` | no | Inclusive both ends. Required for banded routes; omit for the fallback. |
| `lights`       | list of entity_id (≥ 1)    | **yes**  | Lights driven by this route. Each must also appear in the area's `lights` or `light_clusters`. |

#### Semantics

- Routing is active **only while the area's scene is `circadian`.** Any
  other scene applies its `entities:` block exactly as today and
  bypasses the router. Re-entering circadian re-evaluates from the
  current source state.
- The router reads `state.attributes['colortemp']` on `source`. The
  first banded route whose `kelvin_range` contains that value is
  selected; if none match, the fallback is selected. A small
  hardcoded hysteresis (±25K) is applied around the currently-active
  banded route to suppress flapping at the boundary.
- When the active route changes, lights in the now-inactive route
  receive `light.turn_off` and lights in the now-active route receive
  `light.turn_on`, both with `transition: crossfade_seconds`. The
  `circadian_lighting` switch then catches the new-on event and
  applies the current CT and brightness.
- Lights listed in any route are excluded from the controller's
  default per-light circadian initialization — the router fully owns
  their on/off state while the circadian scene is active.
- If `source` is `unavailable` / `unknown` / lacks the `colortemp`
  attribute, the fallback is selected.

#### Validation rules (parse-time `vol.Invalid`)

1. `routes` must contain **exactly one** entry without `kelvin_range`
   (the fallback).
2. Banded `kelvin_range: [lo, hi]` requires `1000 ≤ lo ≤ hi ≤ 10000`.
3. Two banded ranges within one `routes` list may not overlap.
   Touching endpoints (`[4500, 5500]` and `[5500, 6500]`) overlap at
   5500 and are rejected.
4. Every entity id in `routes[].lights` must also appear in the
   area's `lights` or `light_clusters`.
5. A light may appear in at most one route.
6. `source` is required when the area declares 2+ circadian switches
   or zero. With exactly one switch, `source` defaults to that
   switch's entity id.
7. `crossfade_seconds` is a float `≥ 0`.

#### Soft warning

An area with `circadian_kelvin_routes` but no `circadian` scene logs a
`WARNING` at startup — the routing is inert until a `circadian` scene
is added.
````

- [ ] **Step 3: Sanity-check the doc renders**

Run: `grep -c "Circadian kelvin routes" /home/aaron/git/aaron/home-assistant-area-lighting/CONFIGURATION.md`

Expected: at least 1.

- [ ] **Step 4: Commit**

```sh
git add CONFIGURATION.md
git commit -m "(Patch) docs: CONFIGURATION.md section for circadian_kelvin_routes"
```

---

## Task 10: README + CHANGELOG

**Goal:** Add a one-line bullet in `README.md` pointing at the new section, and a `CHANGELOG.md` entry under the next Minor release.

**Files:**
- Modify: `CHANGELOG.md` (add at top, under "Unreleased" if present, else create that section)
- Modify: `README.md` (add bullet to the feature list)

- [ ] **Step 1: Check existing CHANGELOG.md style**

Run: `head -30 /home/aaron/git/aaron/home-assistant-area-lighting/CHANGELOG.md`

If an `## Unreleased` section exists, append underneath it. Otherwise insert one at the top (immediately after the title).

- [ ] **Step 2: Edit CHANGELOG.md**

If `## Unreleased` already exists, add this under the appropriate sub-heading (Added / Changed):

```markdown
- **Circadian kelvin routes** — per-area config that swaps which lights
  participate in the `circadian` scene based on the live target color
  temperature. See `CONFIGURATION.md` § "Circadian kelvin routes".
```

If no `## Unreleased` section exists, insert at the top of the file just below the title:

```markdown
## Unreleased

### Added

- **Circadian kelvin routes** — per-area config that swaps which lights
  participate in the `circadian` scene based on the live target color
  temperature. See `CONFIGURATION.md` § "Circadian kelvin routes".
```

- [ ] **Step 3: Add a feature bullet to README.md**

Open `README.md`, find the existing feature list (look for bullet items near the top describing the integration's capabilities). Add this bullet, preserving the existing style:

```markdown
- **Circadian kelvin routes** — for fixtures combining lights with
  different native color temperatures (e.g. fluorescent tubes + Hue
  strips), automatically pick which subset participates in the
  `circadian` scene based on the live target kelvin. See
  [`CONFIGURATION.md`](CONFIGURATION.md) for the schema.
```

If the README has no obvious feature list, place the bullet near the
existing references to circadian/scene behavior.

- [ ] **Step 4: Sanity-check**

Run: `grep -c "Circadian kelvin routes" /home/aaron/git/aaron/home-assistant-area-lighting/CHANGELOG.md /home/aaron/git/aaron/home-assistant-area-lighting/README.md`

Expected: both ≥ 1.

- [ ] **Step 5: Commit**

```sh
git add CHANGELOG.md README.md
git commit -m "(Patch) docs: CHANGELOG and README entry for circadian kelvin routes"
```

---

## Task 11: Final verification

**Goal:** Run the full test suite + lint + ruff format check to confirm clean state, end-to-end.

- [ ] **Step 1: Run lint**

```sh
cd custom_components/area_lighting && uv run ruff check .
```

Expected: no issues.

- [ ] **Step 2: Run format check**

```sh
cd custom_components/area_lighting && uv run ruff format --check .
```

Expected: no formatting issues.

- [ ] **Step 3: Run the full test suite**

```sh
cd custom_components/area_lighting && uv run pytest -n auto
```

Expected: all tests pass.

- [ ] **Step 4: Spot-check on a real config (optional but recommended)**

If you have access to the user's Home Assistant config repo (e.g. `/home/aaron/git/aaron/haconf/`), copy a real `area_lighting.yaml` and add a routes block matching the kitchen example. Run `uv run python -c "from custom_components.area_lighting.config_schema import parse_config, validate_circadian_kelvin_routes; ..."` to confirm it parses.

- [ ] **Step 5: Confirm git history is clean**

```sh
git log --oneline main..HEAD
```

Expected: one commit per task (Tasks 1-10 each produce one commit; Task 11 produces none).
