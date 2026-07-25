# Demand Response — Design Spec

Date: 2026-07-22
Status: Approved for planning
Scope: One implementation plan (single feature)

## 1. Goal

Add a global "demand response" (DR) mode to `area_lighting`. While DR is
active, every lighting activation sheds a fraction of the bulbs it would
otherwise turn **on**, reducing electrical load during a utility demand-response
event. Turning lights **off** is never affected. When DR clears, areas that are
not in `manual` restore to their normal lighting.

The design leans on a single insight: because the mode is captured in one
persisted flag (`_demand_response_active`) and folded into the point where a
scene's per-light targets are resolved, the "which bulbs should be on" logic
stays **idempotent** even when a subset is dropped. There is no separate
suppression state to keep in sync (unlike alerts).

## 2. Behavior summary

- A global owned switch `switch.area_lighting_demand_response_active` (default
  off, persisted, survives restart) turns the mode on and off. Any utility
  integration or automation can drive it with `switch.turn_on`.
- The shed ratio is computed **per activation**, against the number of bulbs
  that activation would turn on:
  - `n <= 5` bulbs on -> shed 50%
  - `n >= 6` bulbs on -> shed 80%
  - `keep = ceil(n * (1 - ratio))`, so at least one bulb survives whenever the
    activation lights anything.
- Bulbs are shed from the **config-order tail** of the on-set (the first-declared
  bulbs survive). Order = importance; the user declares the most important lights
  first.
- Off commands, alerts, and `manual` areas are untouched.
- Two mechanisms enforce the mode (Section 5 and 6): steady-state filtering of
  every new activation, and, at the moment the switch flips, re-activation of
  already-lit areas through their NORMAL activation paths (Section 8). There is
  no separate reconcile subsystem and no lock.

### Worked examples

| Area total | Scene turns on | n | ratio | keep = ceil(n·(1−r)) | lit under DR |
|---|---|---|---|---|---|
| 25 | 2 bulbs | 2 | 50% | 1 | 1 |
| 25 | all 25 | 25 | 80% | 5 | 5 |
| 5 | all 5 | 5 | 50% | 3 | 3 |
| 6 | all 6 | 6 | 80% | 2 | 2 |
| 1 | 1 bulb | 1 | 50% | 1 | 1 |

The ratio is a function of the **activation's on-count**, never the area's total
bulb count. A dim two-bulb scene in a 25-bulb room sheds one bulb; a full-room
scene in the same room sheds twenty.

## 3. The shed universe

The universe of physical bulbs is `AreaConfig.lights` (individual
`LightConfig` entries), in config-declaration order.

`AreaConfig.light_clusters` (Hue Zones and similar) are **excluded**. A cluster
is not a distinct load; it is an addressing shortcut whose `members` are the same
physical bulbs already declared in `lights`, used by `select_dispatch_commands`
to coalesce N per-light calls into one group call when every member shares a
target. DR therefore needs zero cluster-specific logic:

- Shed some members and keep others -> they land in different target cohorts, so
  `select_dispatch_commands` stops coalescing that zone and emits per-light
  calls. Correct by construction.
- Shed *all* of a cluster's members (all forced off) -> they share the "off"
  cohort and coalesce into a single group `turn_off`. Also correct, and optimal.

**Amendment (hardening):** "zero cluster-specific logic" held only for the
dispatcher's cohort logic. Every path that can command a cluster entity
directly must refuse to drive it **on** while DR is active, because a zone
`turn_on` relights shed members through the aggregate: scene targets drop
cluster entries (`_effective_scene_targets` / `_apply_scene_data`), circadian
activation skips clusters, brightness stepping excludes them from the on-set,
and the kelvin router expands cluster route entities to members (Section 5.1).
Clusters remain a batching optimization for kept cohorts and full-off.

**Recorded assumption:** cluster `members` are always also declared in `lights`
(as `CONFIGURATION.md` prescribes and the worked example demonstrates). If a
future config allows members-only bulbs, either (a) extend the universe to
`lights` ids ∪ cluster `members`, or (b) add a parse-time validation that every
member appears in `lights`. Option (b) is preferred and cheap.

## 4. Core primitive

A pure, HA-free function in a new module `demand_response.py` (mirroring the
style of `cluster_dispatch.py`, so it is trivially unit-testable):

```python
from math import ceil

def apply_demand_response(
    targets: dict[str, dict],
    ordered_light_ids: list[str],
) -> dict[str, dict]:
    """Force the config-order tail of the ON-set to off. Pure.

    `targets` maps entity_id -> state dict (as produced by scene resolution).
    `ordered_light_ids` is the area's individual lights in config order.
    Callers invoke this ONLY when DR is active; the active-flag gate stays in
    the controller so this function has no dependency on global state.
    """
    on_ids = [
        eid for eid in ordered_light_ids
        if targets.get(eid, {}).get("state") == "on"
    ]
    n = len(on_ids)
    if n == 0:
        return targets
    ratio = 0.50 if n <= 5 else 0.80
    keep = ceil(n * (1 - ratio))
    out = dict(targets)
    for eid in on_ids[keep:]:
        out[eid] = {"state": "off"}
    return out
```

Notes:
- The on-set is drawn from `ordered_light_ids` (the area's own lights), so a
  target key that is not one of the area's lights is never counted or shed.
- Shed bulbs are rewritten to an explicit `{"state": "off"}` target, not merely
  removed. This is what makes tracking (and every replay of the resolved
  targets) idempotent (Section 5).

## 5. Idempotent target resolution (the heart)

`controller.py` currently resolves a scene's per-light targets in two places
that duplicate the same snapshot -> config -> skeleton logic:

- `_resolve_scene_targets(scene_slug)` (`controller.py:910`) — builds the tracked
  `_active_scene_targets`.
- `_apply_scene_data(scene_slug, transition)` (`controller.py:822`) — builds the
  command set and fans out via `select_dispatch_commands`.

Introduce a single filtered resolver both consume:

```python
def _effective_scene_targets(self, scene_slug):
    targets = self._resolve_raw_scene_targets(scene_slug)   # existing dedup'd logic
    if self._demand_response_active():                      # reads hass.data global
        targets = apply_demand_response(
            targets, [l.id for l in self.area.lights]
        )
    return targets
```

- `_activate_scene` sets `self._active_scene_targets = self._effective_scene_targets(slug)`.
- `_apply_scene_data` fans out from that same resolved dict (small refactor so it
  consumes the resolved targets instead of independently re-deriving them). One
  filter point governs both what is **commanded** and what is **tracked**.

Because shed bulbs carry an explicit **off-target** in `_active_scene_targets`:

- Manual detection (`event_handlers.py:463`, comparing live state to
  `_active_scene_targets` via `state_matches_scene_target`,
  `controller.py:938`) sees "off target, actually off -> match" and does not
  latch `manual`.
- Scene self-heal (`_run_post_settle_selfcheck` `controller.py:1538`,
  `handle_scene_drift_reassert` `controller.py:1555`) only re-asserts **on**
  targets, so it never fights DR to turn a shed bulb back on.

No `_alert_active`-style suppression flag is needed. `_demand_response_active`
folded into resolution makes the tracking layer correct by construction.

### 5.1 Single-source-of-truth invariant

The shed decision for the current activation is computed **once**, when the
effective targets are resolved, and is the single source of truth. Secondary
reconcilers (self-heal, the kelvin router) must **read** the shed decision from
`_active_scene_targets` (a shed bulb is an off-target there) and must **never**
recompute the ratio themselves. Recomputing over a different on-set (e.g. the
router's route-lights subset) would size `n` differently and could disagree on
which specific bulbs are off.

**Routed-circadian amendment.** For circadian in an area with
`circadian_kelvin_routes`, the on-set is a function of the current colortemp:
only the ACTIVE route is lit, so the shed set is sized over the active route's
lights (plus circadian-switch lights outside any route) and must be
**recomputed on route changes**. Ownership is unchanged: the controller
(`recompute_and_apply_circadian_dr`) is the only writer of `dr_shed_ids`; the
router still never computes a ratio itself, it asks the controller to refresh
the set at the top of every reconcile and then reads it.

Two consistency rules keep the controller's sizing and the router's dispatch
agreeing on the same route:

- **Hysteresis-consistent sizing.** The controller selects the active route
  with the router's `current_index` (exposed as a read-only property), so
  `select_route`'s hysteresis grace applies identically in both places. The
  router publishes its new index **before** it asks the controller to refresh
  the shed set, so the shed is always sized over the route the reconcile is
  activating. Without this, a colortemp inside the hysteresis band would make
  the controller size the shed over the strict-selection route while the
  router keeps the previous route lit (a multi-bulb active route could be
  fully relit).
- **Cluster route expansion.** A route's `lights` may name a cluster entity
  (the validator allows it). The shed universe is individual lights, so both
  the controller's on-set sizing and the router's dispatch expand cluster
  route entities to their members while DR is active; the cluster entity is
  never commanded (a zone `turn_on` would relight shed members through the
  aggregate, and a zone `turn_off` would kill kept members). Without DR the
  cluster entity stays the route target (batching preserved).

## 6. On-emitters and where the filter goes

There is no single turn-on chokepoint. Each independent on-emitter must run its
resolved targets through the primitive (or read the already-resolved shed set).
Alerts are the sole exception.

| Emitter | File:line | DR handling |
|---|---|---|
| Controller scene fan-out | `_apply_scene_data` `controller.py:822`, via `_effective_scene_targets` | Filtered at resolution (Section 5). |
| Motion / remote / ambience / favorite / leader-follower | all route through `lighting_on` -> `_activate_scene` | Inherit the scene filter for free. No extra work. |
| Circadian activation | `_activate_circadian` `controller.py:782` | Build its on-set (with kelvin routes: the ACTIVE route's lights plus non-route circadian lights, per Section 5.1's routed-circadian amendment), run through `apply_demand_response`, turn on only kept bulbs. Cluster entities are skipped under DR (a cluster with a circadian switch would relight shed members through the zone; members are driven individually). Record shed bulbs as off so the router agrees (Section 6.1). |
| Kelvin router | `CircadianKelvinRouter._reconcile` `circadian_kelvin_router.py:115` | Publish the newly selected route index, then ask the controller to refresh the shed set for the current colortemp (`recompute_and_apply_circadian_dr`), re-check that the router is still active after that await (a `deactivate()` interleaved with it drops the dispatch), then skip any routed light the shed set lists. Cluster route entities are expanded to members under DR (Section 5.1). The router itself never computes a ratio. |
| Raise/lower dark-room bring-up | `_set_all_lights_to_pct` `controller.py:1101` | Build the all-lights on-target, run through the primitive; only kept bulbs come up. `_step_on_lights_pct` (already-on lights only) excludes cluster entities under DR so a step never drives a zone aggregate. |
| HA Scene entity (external `scene.turn_on`) | `scene.py` `_apply_stored:127` / `_apply_skeleton:188` | Build its target dict and run through the primitive (needs the area's ordered light ids + the DR flag from `hass.data[DOMAIN]["global"]`). The controller's `handle_scene_activated` tracking resolves via `_effective_scene_targets`, so tracking stays consistent. |
| Alerts | `alert.py:234` | **Bypass.** Alerts already snapshot/restore and set `_alert_active`; DR does not filter them (safety signals). |

### 6.1 Recording the shed set for non-scene activations

The scene path stores shed bulbs as off-targets in `_active_scene_targets`
automatically. The circadian path does not necessarily populate
`_active_scene_targets`. To uphold the Section 5.1 invariant across both paths,
the plan will pick one storage mechanism and use it everywhere:

- **Option A:** have `_activate_circadian` write shed bulbs as off-entries into
  `_active_scene_targets` (single source, consistent with the scene path).
- **Option B:** add a `self._dr_shed_ids: frozenset[str]` cache set at every
  activation and read by the router.

**Resolved at plan time: Option B.** The circadian path leaves
`_active_scene_targets` empty, and the kelvin router (a separate object with no
scene-target dict of its own) needs a read surface, so the controller stores
`self._dr_shed_ids: frozenset[str]` for the current activation and exposes it as
a public `dr_shed_ids` property. The scene path additionally carries shed bulbs
as off-targets in `_active_scene_targets` (needed anyway for manual-detection /
self-heal). The invariant holds: the router reads `dr_shed_ids`, never recomputes
the ratio over its own smaller route-lights on-set. The earlier "computed once
per activation, router only reads" simplification is superseded for routed
circadian: there the controller re-derives `dr_shed_ids` from the active route
whenever the router reconciles, and `handle_scene_activated` sets it on external
circadian activation (clears it on external off). See the Section 5.1
routed-circadian amendment.

## 7. Trigger, state, and persistence

Extend `GlobalToggles` (`global_state.py:22`) exactly as the existing occupancy
flag:

- Field `_demand_response_active: bool = False`; read-only property.
- `async_set_demand_response_active(enabled)` — on change, notify listeners,
  schedule save, and walk `hass.data[DOMAIN]["controllers"].values()` creating
  one task per controller calling `reactivate_for_demand_response()`
  (Section 8), mirroring the occupancy setter at `global_state.py:80`.
- Add `demand_response_active` to `state_dict()` and `load_persisted_state()`
  (persisted via the reserved global `StateStorage` key).

Add one row to `GLOBAL_SWITCH_DEFS` (`switch.py:30`) producing the owned entity
`switch.area_lighting_demand_response_active`, and route it through the setter in
`AreaLightingGlobalSwitch._set`.

## 8. Re-activation on the switch edge

Steady-state filtering (Section 6) only affects *new* activations. Already-lit
areas are handled on a flag flip by **re-driving each area through its NORMAL
activation path**, in both directions. `manual` and `off` areas are skipped, as
is an area an alert currently owns: the alert's finally block re-drives it
after its restore (Section 6's alert bypass) whenever the flag is active or a
shed set is still tracked. That condition is deliberately independent of
whether the flag changed during the alert, so a flip whose re-activation the
alert outran (scheduled just before the alert started) is applied too.

The setter (`async_set_demand_response_active`) fans out one task per
controller calling `reactivate_for_demand_response()`:

- **Scene areas:** re-run `_activate_scene(scene_slug, source)` for the
  currently-active slug, with follower propagation suppressed
  (`propagate_to_followers=False`): the setter already re-drives every
  controller with its own scene, so a leader's re-drive must not overwrite a
  follower that is independently in a different scene. The DR filter folded
  into target resolution (Section 5) sheds the tail on activate and replays
  the full scene on deactivate. Tracking (`_active_scene_targets`,
  `dr_shed_ids`) is rebuilt by the activation itself, exactly as for any
  other activation.
- **Circadian areas:** re-run `_activate_circadian(source)`. Circadian values
  are computed, so kept bulbs are re-sent the same value (no visible change)
  while the DR filter turns shed bulbs off (activate) or brings them back
  (deactivate).

Because DR is a pure idempotent filter applied at every activation, the flip
needs no state of its own: there is **no separate reconcile subsystem, no
lock, no serialization** between a flip and normal light control. A flip can
never block (or be blocked by) an activation; concurrent triggers simply race
as ordinary activations already do, and the last completed activation wins
with self-consistent tracking. Both directions are idempotent: re-firing the
activation when the area is already converged re-issues the same commands with
the same result.

**Accepted tradeoff (reliability over DR precision):** the flip re-fires the
active scene, so every bulb the scene manages is re-commanded to its scene
target. A manual dim level set before the DR event is therefore not preserved
across the flip (the bulbs return to scene brightness), and a `dimmed` area's
shed bulbs likewise restore at scene brightness. This is deliberate: the prior
design's diff-based edge reconcile preserved kept-bulb dim levels but required
a second concurrent subsystem mutating activation tracking under a shared
lock, which coupled normal light control to demand response (an activation
could wait on, or be starved by, DR work). Reliable normal lighting is the
priority; DR precision beyond "shed on, restore on clear" is not.

## 9. Manual and restart

- A user externally turning on a shed bulb produces a real divergence from its
  off-target -> normal manual detection latches the area `manual` (user intent
  wins). Manual areas are excluded from the flip re-activation, so the bulb
  stays on and the area is not re-shed. Correct by default.
- `demand_response_active` persists. On restart mid-event the flag restores;
  target resolution stays shed because `_effective_scene_targets` reads the flag.
  The plan must ensure `_active_scene_targets` is rebuilt through the filtered
  resolver after restart (either lazily on the next activation, or during startup
  reconciliation) so a restart during a live DR event does not leave stale
  full-scene targets that self-heal would act on.

## 10. Observability

Minimal, YAGNI-respecting:

- DR state is already visible via the owned switch entity.
- Add `demand_response_active` (bool) and the current activation's shed
  entity_ids/count to the existing `diagnostic_snapshot` (`controller.py:351`).
- No new sensor entity in v1.

## 11. Testing

Unit (`demand_response.py`, HA-free, fast):
- `apply_demand_response`: ratio boundaries n = 0, 1, 2, 5, 6, 10, 25; `ceil`
  rounding; keep >= 1 whenever n >= 1; empty/no-op when n = 0; determinism
  (same input -> same shed tail); config-order tail selection; target keys not in
  `ordered_light_ids` are ignored.

Integration (`pytest-homeassistant-custom-component`):
- Switch entity exists, defaults off, persists across a reload.
- DR on then scene activation: only `keep` bulbs come on; the config-order tail
  stays off; the exact worked-example counts (2->1, 25->5, 6->2).
- Flip re-activation on activate: an already-lit area sheds its tail
  immediately; kept bulbs never receive a turn_off.
- Flip re-activation on deactivate: shed bulbs return at their scene targets;
  nothing is turned off.
- No lock: a normal activation completes while a flip re-activation is
  in flight (structural: no `_dr_lock` exists).
- Off still fully turns the area off during DR.
- Manual light-on during DR latches `manual` and survives the clear (not
  re-shed, not re-activated).
- External `scene.turn_on` during DR is filtered (the HA Scene-entity path).
- Alerts during DR are unaffected (full pattern runs, then restores).
- Cluster behavior: a partially-shed Hue Zone emits per-light calls; a fully-shed
  zone coalesces to a single group off.
- Circadian + kelvin router: routed shed bulbs are not turned on by a subsequent
  router reconcile (invariant from Section 5.1).
- Persistence: DR active across a restart keeps areas shed; no stale full-scene
  targets trip self-heal.

## 12. Non-goals (explicitly deferred, all additive)

- Per-area exemption (`demand_response_exempt`).
- Configurable ratios / thresholds (hardcoded 50% / 80% and the 5/6 boundary for
  v1).
- Brightness-cap shed mechanism (drop-whole-bulbs only).
- Graduated / tiered DR levels (binary on/off for v1).
- Role-aware or per-light explicit shed priority (config-order only).

## 13. Key file references

- `global_state.py:22` `GlobalToggles`; `:80` occupancy setter (the pattern to copy)
- `switch.py:30` `GLOBAL_SWITCH_DEFS`; `:125` `AreaLightingGlobalSwitch`
- `controller.py:822` `_apply_scene_data`; `:910` `_resolve_scene_targets`;
  `:880` `_apply_light_state`; `:724` `_activate_scene`; `:782`
  `_activate_circadian`; `:1101` `_set_all_lights_to_pct`; `:1086`
  `_step_on_lights_pct`
- `controller.py:938` `state_matches_scene_target`; `:1538`
  `_run_post_settle_selfcheck`; `:1555` `handle_scene_drift_reassert`; `:351`
  `diagnostic_snapshot`
- `controller.py:139` `_active_scene_targets`; `:75` `_alert_active`
- `area_state.py:96` `is_manual`; `:151` `transition_to_manual`
- `cluster_dispatch.py:36` `select_dispatch_commands`
- `circadian_kelvin_router.py:115` `_reconcile`
- `scene.py:127` `_apply_stored`; `:188` `_apply_skeleton`; `:99` `async_activate`
- `alert.py:234` `execute_alert`
- `models.py:222` `AreaConfig.lights`; `:238` `all_lights`; `:33` `LightConfig`
- `hass.data[DOMAIN]`: `"controllers"`, `"global"`, `"config"` (`__init__.py:110`)
