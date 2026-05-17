# Design: Circadian Kelvin Routes

## Summary

While the `circadian` scene is active in an area, dispatch a configured set
of lights between mutually-exclusive routes based on the current target
color temperature (kelvin). A route is selected when the live `colortemp`
attribute from the area's circadian switch falls inside the route's
declared `kelvin_range`; one fallback route (no range) covers everything
else.

Built to solve: a kitchen overhead fixture combines fluorescent tubes
(fixed ~5000K, brighter than the strips) and Hue lightstrips (full CT
range). The fluorescents should run when the circadian target is near
their native CT (e.g. 4500–5500K) and the strips should take over below
and above that band. The swap must be automatic and seamless.

Behavior is scoped strictly to the circadian scene path. Non-circadian
scenes continue to apply their inline `entities:` block exactly as today
and ignore routing entirely.

## Config surface

New per-area key `circadian_kelvin_routes` (optional). When present it is
a dict with shared parameters plus a `routes:` list.

```yaml
areas:
  - id: kitchen
    name: Kitchen

    circadian_switches:
      - { name: Kitchen, min_brightness: 20, max_brightness: 100 }

    lights:
      - { id: light.kitchen_fluorescent, circadian_switch: Kitchen, circadian_type: ct }
      - { id: light.kitchen_strip_1,     circadian_switch: Kitchen, circadian_type: ct }
      - { id: light.kitchen_strip_2,     circadian_switch: Kitchen, circadian_type: ct }
      - { id: light.kitchen_strip_3,     circadian_switch: Kitchen, circadian_type: ct }

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

    scenes:
      - { id: circadian, name: Circadian }
      - { id: off,       name: Off }
```

### Keys

| Key                     | Type                            | Required | Default                              | Notes                                                                                  |
|-------------------------|---------------------------------|----------|--------------------------------------|----------------------------------------------------------------------------------------|
| `source`                | entity id                       | no       | the area's single circadian switch    | Entity whose state attribute drives route selection. Required when the area declares 2+ circadian switches. |
| `crossfade_seconds`     | float ≥ 0                       | no       | `2.0`                                | Transition passed to `light.turn_on` / `light.turn_off` when the active route changes. `0` snaps. |
| `routes`                | list of [route](#route)          | **yes**  | —                                    | Mutually-exclusive route definitions. Must contain ≥ 2 entries (a banded route plus a fallback). |

### Route

| Key            | Type                       | Required | Notes                                                                 |
|----------------|----------------------------|----------|-----------------------------------------------------------------------|
| `kelvin_range` | `[lo, hi]` of ints         | no       | Inclusive both ends. Required for banded routes. Omit to declare this route as the fallback. |
| `lights`       | list of entity_id (≥ 1)    | **yes**  | Lights driven by this route. Each must also appear in the area's `lights` or `light_clusters`. |

### Source attribute

Hardcoded to `colortemp` (the attribute name `circadian_lighting`'s switch
and sensor entities expose). Not configurable; if a future signal source
uses a different attribute name, the schema can be extended then.

### Source vs. applied CT (intentional divergence)

The current circadian activation loop (`controller.py:725-757`) reads
`colortemp` from the **global** `sensor.circadian_values` entity when
deciding what `color_temp_kelvin` to set on each light. The router's
default `source` is the per-area `switch.circadian_lighting_*`, which
overrides `colortemp` with `sleep_colortemp` while a configured
sleep_entity is in its sleep state.

This divergence is intentional and desired: during sleep mode the
per-area switch reports the sleep CT (e.g. 1000K), so the router
correctly selects the fallback (warm) route, while the global sensor
keeps reporting the natural circadian curve for any downstream code
that wants the unmodified value. If a user wants routing to track the
unmodified global curve instead, they can set
`source: sensor.circadian_values` explicitly.

## Semantics

### Activation gate

Routing is in effect only while the area's active scene is `circadian`.
On every scene transition the controller (de)registers the routing
listener:

- Entering `circadian` → register state-change listener on `source`,
  evaluate the current `colortemp` immediately, dispatch the matching
  route.
- Leaving `circadian` → unregister the listener. The newly-active scene
  applies its own `entities:` block (or skeleton dispatch) to the area's
  lights, including any that were on/off via routing.

### Route selection

On each state-change of `source` (and on the entry evaluation):

1. Read `state.attributes['colortemp']`. Coerce to `float`. If missing,
   non-numeric, or the entity is `unavailable` / `unknown`, **select the
   fallback** and log at `DEBUG`.
2. Otherwise pick the first banded route whose `kelvin_range` contains
   the value, applying hysteresis (see below). If none match, select the
   fallback.

### Hysteresis

Hardcoded constant `CIRCADIAN_KELVIN_HYSTERESIS = 25` in `const.py`.

Selection rule for a banded route with range `[lo, hi]`, given the
currently-active route `prev`:

- If `prev` is this same route, it stays active while `lo - 25 ≤
  colortemp ≤ hi + 25` (sticky boundaries).
- Otherwise it becomes active when `lo ≤ colortemp ≤ hi` (strict
  entry).

The fallback has no boundaries to be sticky about; it is selected when
no banded route matches under the rules above.

This prevents flapping when `colortemp` hovers exactly at a boundary
across consecutive updates. Circadian CT updates are slow in practice
(5-min default cadence) so 25K is comfortably more than enough margin.

### Dispatch (reconciliation)

The set of "lights that should be on under routing" = the active route's
`lights`. The set of "lights that should be off under routing" = the
union of all the variant's `lights` minus the active set.

Reconciliation is idempotent: on each event, compute target on/off sets,
diff against current HA state, issue only the calls needed.

- Outgoing lights → `light.turn_off` with `transition: crossfade_seconds`.
- Incoming lights → `light.turn_on` with `transition: crossfade_seconds`,
  no brightness/CT arguments. The `circadian_lighting` switch catches
  the on-transition via its existing `_light_state_changed` hook
  (`switch.py:375-386` in the upstream component) and applies the
  current CT/brightness on its next update.

Lights listed in `routes[].lights` are excluded from the controller's
default "all area lights on during circadian" behavior — the variant
owns their activation state for the duration of the circadian scene.

### Source attribute caching

While the area's active scene is not `circadian`, the controller does
not subscribe to `source` and does not act on its changes. On the next
entry into `circadian`, the route is re-evaluated from scratch using
the source's current state.

## Validation rules (parse-time `vol.Invalid`)

1. `circadian_kelvin_routes.routes` must contain **exactly one** route
   with no `kelvin_range` (the fallback). Zero or two+ fallbacks are
   rejected.
2. Banded `kelvin_range: [lo, hi]` requires `1000 ≤ lo ≤ hi ≤ 10000`.
3. Two banded routes within the same `routes` list may not have
   overlapping ranges. Touching endpoints (`[4500, 5500]` and
   `[5500, 6500]`) overlap at 5500 and are rejected; non-overlapping
   pairs must have a gap (e.g. `[4500, 5499]` / `[5500, 6500]`).
4. Every entity id in `routes[].lights` must also appear in the area's
   `lights` or `light_clusters`.
5. A light may appear in at most one route across the area's
   `circadian_kelvin_routes`.
6. `source` is required when the area declares more than one circadian
   switch. When the area declares exactly one, `source` defaults to its
   switch entity id. When the area declares zero, `source` is required
   and must be supplied explicitly.
7. `crossfade_seconds` is a float `≥ 0`.

## Soft warnings (logged at startup, not `vol.Invalid`)

- Area has `circadian_kelvin_routes` but no `circadian` scene declared:
  log `WARNING` that the routing is inert until a `circadian` scene is
  added.

## Touchpoints

### `const.py`

- `CIRCADIAN_KELVIN_HYSTERESIS = 25`
- `DEFAULT_CIRCADIAN_KELVIN_CROSSFADE_SECONDS = 2.0`

### `models.py`

Two new dataclasses:

```python
@dataclass
class CircadianKelvinRouteConfig:
    lights: list[str]
    kelvin_range: tuple[int, int] | None = None  # None marks fallback

    @property
    def is_fallback(self) -> bool:
        return self.kelvin_range is None


@dataclass
class CircadianKelvinRoutesConfig:
    routes: list[CircadianKelvinRouteConfig]
    source: str  # resolved to a concrete entity_id at parse time
    crossfade_seconds: float = DEFAULT_CIRCADIAN_KELVIN_CROSSFADE_SECONDS

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

New field on `AreaConfig`:

```python
circadian_kelvin_routes: CircadianKelvinRoutesConfig | None = None
```

### `config_schema.py`

- New voluptuous schema for the dict + nested route list.
- `source` default-resolution and the "required when 2+ switches" rule
  run in the same post-parse pass that already validates leader/follower
  and favorite slugs.
- Range / overlap / fallback-count / light-membership rules implemented
  as a single `_validate_circadian_kelvin_routes(area)` helper invoked
  from the existing area validator.

### `controller.py`

A new helper `_circadian_kelvin_router` per controller, instantiated only
when the area has `circadian_kelvin_routes` configured. Owns:

- The `source` state-change listener (registered on entry to `circadian`,
  deregistered on exit).
- A small state machine that remembers the last-selected route id so
  hysteresis can apply.
- The reconciliation method that diffs target on/off sets against
  current HA state and issues the necessary service calls with the
  configured crossfade.

The controller's existing scene-transition hook calls
`router.on_scene_enter(slug)` / `router.on_scene_exit(slug)`. The router
no-ops for any slug other than `circadian`. The router's first
reconciliation pass is invoked at the end of `_activate_circadian`, after
the per-light circadian initialization completes — that ordering lets the
existing loop wake the strips' `_light_state_changed` hooks before the
router potentially turns the inactive ones off.

The circadian activation loop at `controller.py:737-757` (which calls
`light.turn_on` for each light with a `circadian_switch`) is amended to
**skip** lights present in `area.circadian_kelvin_routes.all_route_lights`.
The router fully owns those lights' on/off state for the duration of the
circadian scene; the existing loop continues to manage every other
circadian-aware light in the area unchanged.

### `tests/`

**Unit (schema):**

- Valid: minimum config (one banded + one fallback), default `source`
  resolution, multi-banded with gaps, explicit `crossfade_seconds: 0`.
- Invalid: zero fallbacks, two fallbacks, overlapping bands, touching
  bands at endpoint, light not declared on the area, light declared in
  two routes, `lo > hi`, out-of-range kelvin, missing `source` with 2+
  switches, `crossfade_seconds: -1`.

**Integration (controller, using `pytest-homeassistant-custom-component`):**

- Entering `circadian` selects the route for the current `colortemp` and
  turns on only that route's lights.
- A `colortemp` state-change that crosses a range boundary swaps the
  active route: outgoing lights `turn_off`, incoming lights `turn_on`,
  both with the configured crossfade.
- Hysteresis: nudge `colortemp` to exactly `hi + 10` then back to `hi`
  while a banded route is active → no swap. Nudge to `hi + 30` then
  back to `hi + 10` → swap out then swap back in only when crossing the
  strict boundaries.
- `source` becomes `unavailable` while routing is active → fallback
  selected, route's lights turned off, fallback's lights turned on.
- Scene change away from `circadian` → state-change listener
  deregistered (verify with a fired event that should not trigger
  reconciliation).
- Returning to `circadian` after the source moved out-of-band while
  another scene was active → router re-evaluates from current source
  state, not stale cache.
- Area with `circadian_kelvin_routes` but no `circadian` scene logs the
  warning at startup and the controller doesn't blow up.

A new conftest fixture `kitchen_with_routes_config` builds the
two-band kitchen scenario inline (lives in the test file, not
`tests/integration/conftest.py`, per project convention).

### Documentation

- `CONFIGURATION.md`: new section after the `lights and light_clusters`
  section titled "Circadian Kelvin Routes" with the schema reference
  table and the worked example above.
- `CHANGELOG.md`: entry under the next Minor release.
- `README.md`: short bullet under the feature list pointing at
  `CONFIGURATION.md`.

## Non-goals

- Multi-attribute routing (e.g. by illuminance, sun position, motion).
  Out of scope; the schema name pins this to circadian + kelvin
  deliberately.
- Per-route override of crossfade timing. Cross-fades are uniform across
  a routing block. If a user needs per-light fade behavior, that's
  expressible elsewhere (existing `night_fadeout_seconds`, scene
  transitions).
- Re-evaluation while a non-circadian scene is active. Routing pauses
  entirely until the scene returns to `circadian`.
- A user-facing override switch (e.g. "force fluorescent on regardless
  of CT"). Manual scene activation already supports this case by
  authoring a scene that includes the fluorescent in its `entities:`.

## Open questions

None at draft time.
