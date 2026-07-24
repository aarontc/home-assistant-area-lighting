# Demand Response Hardening Plan

> **For agentic workers:** execute task-by-task (fresh implementer per task, independent review after each). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Close the seven Important findings and two Minor findings from the whole-branch review of the demand-response feature, so DR is correct across all supported paths (clusters, circadian kelvin routes, alerts, concurrency, `group_exclude`).

**Context:** The base feature is implemented and green (563 tests). These are edge-path correctness fixes. Read the design spec first: `docs/superpowers/specs/2026-07-22-demand-response-design.md`. The finding numbers below (#1..#7) are from the whole-branch review.

## Global Constraints

- Ratio/keep policy is unchanged: `keep = ceil(n * (1-ratio))`, `ratio = 0.50 if n<=5 else 0.80`, shed the config-order tail; shed universe = the area's individual `lights` (config order); clusters excluded from the universe.
- Commit subjects start with `(Patch)` (or `(Minor)` only if a fix adds user-visible surface). No em dashes IN COMMIT MESSAGES (docs may keep the repo's existing em-dash prose style). Never write `skip ci`. Never mention any AI assistant anywhere. Do NOT edit version files (`pyproject.toml`/`manifest.json`/`uv.lock`); if `uv run` rewrites `uv.lock`, do not stage it.
- `uv run ruff check .` and `uv run ruff format --check .` must be clean; run the full suite `uv run pytest -q` from the worktree root before each commit (baseline 563).
- Each fix is TDD: write a test that reproduces the gap (RED), fix, confirm GREEN, then full suite.

---

### Fix Task F1: Cluster entities must not bypass shedding (#1)

**Problem:** `snapshot_scene` captures `area.all_lights` (services.py:114), so a stored snapshot includes the **cluster (Hue-zone) entity** with state `on`. `apply_demand_response` only sheds individual `area.lights`, never the cluster id, so the zone entity survives as an `on` target and, when applied, turns the whole zone on — relighting shed members. It also breaks reconcile idempotency (a second reconcile sees an all-shed zone as off and turns it back on). Same bypass exists in `scene.py._apply_stored` and for a config scene that lists a cluster entity.

**Fix:** Under DR, drive individual members only — drop cluster-entity keys from the applied AND tracked target sets. Clusters remain a pure batching optimization via `cluster_specs` (the dispatcher still coalesces kept members).

**Files:** `controller.py`, `scene.py`; Test: `tests/integration/test_demand_response_cluster.py` (new).

- [ ] **Step 1 (RED):** New integration test. Area with 6 individual lights `light.zone_a`..`light.zone_f` (roles dimming) AND a `light_clusters` entry `light.zone_all` whose `members` are all six. A `bright` scene stored as a snapshot-style config `entities` that includes `light.zone_all: {state: on}` plus each member `{state: on, brightness: 200}` (simulating what `snapshot_scene` captures). With DR active, activate the scene and assert: `light.zone_all` receives NO `turn_on`; only the two kept members (`zone_a`, `zone_b`) are turned on; the four shed members are off; and `ctrl._active_scene_targets` has no `on` entry for `light.zone_all`. Add a second assertion that calling `async_reconcile_demand_response()` again issues zero `turn_on` for shed members or the zone (idempotent). Confirm it fails today (the zone is turned on).

- [ ] **Step 2 (fix):** In `controller.py`:
  - Add a helper `_cluster_entity_ids(self) -> set[str]: return {c.id for c in self.area.light_clusters}`.
  - In `_effective_scene_targets`, when DR active, after `apply_demand_response(...)`, also drop cluster keys: `targets = {eid: st for eid, st in targets.items() if eid not in self._cluster_entity_ids()}`.
  - In `_apply_scene_data`, the snapshot/config branch: after the existing `if self._demand_response_active(): light_entities = apply_demand_response(...)`, additionally drop cluster keys under DR: `light_entities = {eid: st for eid, st in light_entities.items() if eid not in self._cluster_entity_ids()}`. (Leave `cluster_specs` as-is so kept members still batch.)
  In `scene.py._apply_stored`: under DR, after `apply_demand_response`, drop `{c.id for c in self._area.light_clusters}` keys from `stored` before the loop.

- [ ] **Step 3 (GREEN + full suite + commit)** — subject: `(Patch) area_lighting: drive individual members, not clusters, under demand response`.

---

### Fix Task F2: Circadian sheds over the ACTIVE route, and external circadian refreshes the shed set (#3, #4)

**Problem #3:** `_circadian_on_ids` counts **all** kelvin-route lights (across every route) plus circadian-switch lights, but at any colortemp only the *active* route is lit. This over-sizes `n`, "keeps" inactive-route lights, and can leave the active route with too few (or, with an adverse config order, zero) survivors. The shed set is also computed once and never recomputed when the route changes.

**Problem #4:** `handle_scene_activated("circadian")` (external `scene.turn_on`) transitions to circadian without recomputing `_dr_shed_ids`, then syncs the router, which reads a stale shed set; the `off` branch never clears it.

**Fix design:**
- Make the circadian on-set **active-route aware**. Add `_route_source_colortemp(self) -> float | None` that reads the `colortemp` attribute of `self.area.circadian_kelvin_routes.source` (fall back to `sensor.circadian_values` colortemp if the routes source is unavailable; `None` if neither). Rewrite `_circadian_on_ids` so that, when the area has routes, the route portion of the on-set is only the **active** route's lights (`select_route(routes.routes, colortemp, None)`), plus circadian-switch lights that are not route lights; when the area has no routes, it is all circadian-switch lights (unchanged). Keep config order.
- **Recompute on route change.** Add `async def recompute_and_apply_circadian_dr(self) -> None`: if not circadian or not DR-active, set `_dr_shed_ids = frozenset()` and return; else recompute `_dr_shed_ids = _compute_circadian_shed_ids()` and re-drive only the **non-route** circadian lights whose shed status flipped (diff against live `hass.states`: shed+on -> turn_off; kept+off -> turn_on with the circadian brightness/kelvin, mirroring `_activate_circadian`'s per-light data). The router owns route lights and already subtracts `dr_shed_ids`.
- In `circadian_kelvin_router._reconcile`, at the top of the `async with self._reconcile_lock` block (before reading `dr_shed_ids`), call the controller (via the existing `hass.data[DOMAIN]["controllers"].get(area_id)` lookup) `await ctrl.recompute_and_apply_circadian_dr()` so `dr_shed_ids` is fresh for the current colortemp. Then compute `shed = set(ctrl.dr_shed_ids)` as before. (The `_last_shed_ids` guard already forces re-application when the shed set changes.)
- #4: in `handle_scene_activated`, the `circadian` branch adds `self._dr_shed_ids = self._compute_circadian_shed_ids()`; the `off` branch adds `self._dr_shed_ids = frozenset()`.

**Files:** `controller.py`, `circadian_kelvin_router.py`; Test: update `tests/integration/test_demand_response_kelvin.py` and `tests/integration/test_demand_response_circadian.py`.

- [ ] **Step 1 (RED):** Update/add tests:
  - Active-route sizing: kitchen fixture (4 route lights: fluorescent banded, 3 strips fallback), colortemp 3000 (strips active). DR on, activate circadian. The active on-set is the **3 strips** -> keep `ceil(3*0.5)=2` -> assert exactly **two** strips lit and one shed (NOT one strip as the old code produced). The existing `test_router_never_lights_shed_route_bulbs` expectation changes accordingly (shed = one strip, e.g. `light.kitchen_strip_3`); update it.
  - Route-change re-shed: activate at 3000 (2 strips), then set the routes source colortemp to 5000 (fluorescent active, strips inactive); assert the router turns strips off and fluorescent on, and `ctrl.dr_shed_ids` reflects the fluorescent-only on-set (n=1 -> keep 1 -> shed none).
  - External circadian (#4): after `handle_scene_activated("circadian")` under DR, `ctrl.dr_shed_ids` is non-empty and the router does not light shed strips.
  - Confirm the sizing test fails against current code (which keeps only 1 strip).

- [ ] **Step 2 (fix):** implement the design above.

- [ ] **Step 3 (GREEN + full suite + commit)** — subject: `(Patch) area_lighting: shed circadian over the active kelvin route`.

Note: also update the design spec's circadian/kelvin section to state that the shed set is per-active-route and recomputed on route changes (supersedes the "router reads, never recomputes" simplification for routed circadian). Do this in the same commit.

---

### Fix Task F3: Reconcile robustness under alerts and concurrency (#6, #5)

**Problem #6:** `async_reconcile_demand_response` does not skip `_alert_active` areas, so a DR flip during an alert races the alert; the alert's `finally` restores `_state`/`_active_scene_targets` (alert.py:299-300) but never restores/recomputes `_dr_shed_ids`, so the DR edge is lost after the alert.

**Problem #5:** The setter fans out reconciles fire-and-forget (`hass.async_create_task`), so rapid flips run concurrent reconciles that can interleave and write tracking out of order; and an activation-from-off in flight when the flag flips can complete unfiltered (reconcile saw the old off state and skipped).

**Fix design:**
- Add `self._dr_lock: asyncio.Lock = asyncio.Lock()` in `AreaLightingController.__init__`. Wrap the body of `async_reconcile_demand_response` in `async with self._dr_lock:` (it never calls itself, so no re-entrancy). This serializes concurrent reconciles; each reads the current global flag, so the last-scheduled reconcile converges to the final state.
- Alert bypass: at the top of `async_reconcile_demand_response`, `if self._alert_active: return` (defer during an alert). In `alert.py execute_alert`, in the `finally` block AFTER `controller._alert_active = False`, add: `if <DR active>: await controller.async_reconcile_demand_response()` (re-apply the DR edge that was deferred, and refresh `_dr_shed_ids`). Determine DR-active via the same `hass.data[DOMAIN]["global"].demand_response_active` check the controller uses (add a tiny public `controller.demand_response_active` property that returns `self._demand_response_active()` so alert.py need not reach into privates).
- Activation-during-flip closure: at the END of `_activate_scene` (all three branches: off_internal, circadian, visual) and after `_activate_circadian`'s work, add a guard `if self._demand_response_active(): await self.async_reconcile_demand_response()` ONLY when the just-applied result may be unsharded — i.e., re-run the (idempotent) reconcile once at activation completion. To avoid `_activate_circadian` <-> reconcile recursion, gate with a re-entrancy flag `self._in_dr_reconcile` set inside `async_reconcile_demand_response` (skip the end-of-activation reconcile while already reconciling). Because reconcile is ON/OFF-only and idempotent, the extra pass is a no-op in the common case and only sheds when a mid-activation flip left the area unfiltered.

  (Simpler acceptable alternative if the re-entrancy flag proves fragile in review: acquire `_dr_lock` around the activation's DR-relevant tail instead. Pick whichever the reviewer confirms is deadlock-free.)

**Files:** `controller.py`, `alert.py`; Test: `tests/integration/test_demand_response_reconcile.py` (extend), `tests/integration/test_demand_response.py` (alert) .

- [ ] **Step 1 (RED):** Tests:
  - Alert + DR flip: area in a lit scene, start an alert, flip DR on mid-alert (call the setter), let the alert finish; assert after restore the shed bulbs are off and `dr_shed_ids` is populated (DR edge survived the alert). Confirm it fails today.
  - Reconcile idempotency under lock: call `async_reconcile_demand_response()` twice concurrently (gather) after a flip; assert the end state is shed and consistent (no double-relight).
  - (If feasible) activation-during-flip: simulate by flipping DR after `_effective_scene_targets` resolves but before state transitions — assert the area ends shed. If this is too timing-dependent to test deterministically, cover the end-of-activation reconcile path directly (activate from off with DR already active, assert shed; then a targeted unit-ish test that the end-of-activation reconcile is invoked).

- [ ] **Step 2 (fix):** implement the design above; keep the re-entrancy guard simple and deadlock-free.

- [ ] **Step 3 (GREEN + full suite + commit)** — subject: `(Patch) area_lighting: serialize demand-response reconcile and survive alerts`.

---

### Fix Task F4: Dark bring-up, group_exclude consistency, and minors (#2, #7, minors)

**Problem #2:** `_bring_dark_area_to_min` first activates a remembered scene (shed by the scene's on-set), then `_set_all_lights_to_pct` turns on only kept-of-all-lights but never turns the shed tail OFF or updates tracking — so a bulb the scene lit but the all-lights shed would drop stays on, and `_active_scene_targets` describes the scene, not the dark bring-up.

**Problem #7:** `scene.py._apply_skeleton` honors `group_exclude` when sizing/applying the shed, but the controller's `_resolve_raw_scene_targets` and `_apply_scene_data` skeleton branch ignore `group_exclude`, so the two paths size `n` differently and track kept bulbs as off (or vice versa).

**Minor A:** `apply_demand_response` docstring says "Return a copy" but the no-shed branch returns the original mapping by identity. Reword the docstring to "Return `targets` with the shed tail forced off (a shallow copy when anything is shed; the original mapping unchanged when nothing is shed)."

**Minor B:** On restart while DR is active, `_dr_shed_ids`/`_active_scene_targets` are empty until the next activation, so diagnostics read stale/empty. Acceptable for lights (physical state is preserved), but note it: in `reconcile_startup_state`, when the persisted state is a scene/circadian and DR is active, set `_dr_shed_ids` via the matching `_compute_*` so diagnostics are accurate immediately (do NOT re-drive lights at startup).

**Fix design:**
- #2: In `_set_all_lights_to_pct`, under DR, ALSO turn the shed lights off (append `light.turn_off` for each shed id) so the preceding scene activation's shed bulbs are dropped, and update tracking: set `_dr_shed_ids` to the all-lights shed set and mark shed ids as off-targets in `_active_scene_targets`. (Keep the non-DR path unchanged.)
- #7: Make the controller honor `group_exclude` in the skeleton path, matching scene.py. In `_resolve_raw_scene_targets` skeleton branch and `_apply_scene_data` skeleton branch, treat lights in the active scene's `group_exclude` as not-in-scene (off / skipped), so the controller and scene.py compute the same on-set. (Fetch `group_exclude` from `self._get_scene_config(scene_slug)`.)

**Files:** `controller.py`, `demand_response.py` (docstring); Test: `tests/integration/test_demand_response_circadian.py` (dark bring-up) or a new file, and `tests/integration/test_demand_response.py` (group_exclude), plus `tests/test_demand_response.py` (docstring is untestable; skip).

- [ ] **Step 1 (RED):** Tests:
  - Dark bring-up: 6-light area, DR active, remembered scene lights only `light.x_6` (in the shed tail of all-lights). From a fully-dark area, raise; assert the final on-set is exactly the kept-of-all-lights (`x_1`,`x_2`) at step brightness and `x_6` is OFF, and `dr_shed_ids` covers the shed tail. Confirm it fails today (x_6 stays on).
  - group_exclude: a skeleton scene with `group_exclude: [light.g_2]` in a 6-light area under DR; assert the controller's applied on-set and `_active_scene_targets` match scene.py's (the excluded light is off in both, and the shed sizing excludes it). Confirm the controller currently diverges.

- [ ] **Step 2 (fix):** implement #2, #7, and the Minor A docstring + Minor B startup diagnostics.

- [ ] **Step 3 (GREEN + full suite + commit)** — subject: `(Patch) area_lighting: fix dark bring-up, group_exclude, and demand-response diagnostics`.

---

## Final verification

- [ ] Full CI-equivalent gate green from the worktree root: `cd custom_components/area_lighting && uv run ruff check . && uv run ruff format --check . && uv run pytest -n auto`.
- [ ] No version files touched: `git diff --name-only ea8aa88..HEAD | grep -E 'pyproject.toml|manifest.json|uv.lock' && echo CHANGED || echo OK`.
- [ ] Re-run the whole-branch review (gpt-5.6-sol) over `ea8aa88..HEAD` to confirm all seven findings are resolved.
