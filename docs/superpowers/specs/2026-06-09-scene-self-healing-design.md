# Scene self-healing (glitch-aware divergence handling) — design

## Summary

When a scene-controlled bulb is changed *out-of-band* — a Philips Hue
power-on default after a momentary power blip, an RF dropout, or a recovery
from `unavailable` — the bulb lands at a value the component never
commanded. Today the manual-detection path reads any such post-grace
divergence as a deliberate manual override and latches the area to
`manual`, freezing a mismatched state until the next motion/remote event.

This feature adds **glitch-aware classification**. A divergence is sorted
into one of three buckets:

- **Glitch (high/medium confidence)** → *heal*: instantly re-assert that one
  bulb's active scene target; the area stays in its scene.
- **Genuine manual change** → *latch `manual`* (today's behavior, unchanged).
- **Still settling** (mid-fade) → *ignore* (today's behavior, unchanged).

A one-shot post-settle self-check catches a glitch that lands *during* a
fade (which the event path ignores as "settling"). A loop cap prevents a
flapping bulb from triggering an endless re-assert war; when healing
repeatedly fails, the component raises an HA Repairs issue. The feature is
**on by default**, with a global `scene_self_heal` kill-switch in
`area_lighting.yaml`.

## Motivation

Observed incident (`upstairs_bathroom`, 2026-06-09): area_lighting faded to
the `ambient` scene (white bulbs → `10/2700K`, 60 s fade) at 21:27:46. At
21:29:36 — 110 s later, ~50 s *after* the fade completed —
`light.upstairs_bathroom_vanity_right_w` jumped to `228/3086K` with **no
originating Home Assistant service call** (a Hue-side change). The component
correctly classified it as a divergence and latched `manual`. Separately
`…_vanity_left_w` went `unavailable` at 21:31:46 and recovered at 21:38:17.
Net result: a visibly mismatched vanity (one white bulb dim-ambient, the
other stuck bright), frozen because the area was now `manual`.

The manual latch is *correct by design* — an external force changed a
scene-controlled light — but for a transient Hue glitch the better outcome
is to quietly snap the bulb back to the scene it belongs to, and only
involve the user when a bulb diverges so often it can't be healed. This
feature draws that line.

## Behavior

All timings are relative to a bulb's `commanded_at` (stamped on every scene
command by `_stamp_targets_with_command_metadata`), with
`grace = MANUAL_DETECTION_GRACE_SECONDS` (4 s),
`HEAL_WINDOW = SCENE_HEAL_WINDOW_SECONDS` (60 s), and
`settle = commanded_at + transition + grace`.

| Situation (on-state divergence on a tracked member bulb) | Classification | Action |
|---|---|---|
| `old_state` was `unavailable`/`unknown`, new is `on`, scene active, value ≠ target | recovery glitch (tier 1) | **heal** |
| age ≤ `settle` | still settling | ignore (today) |
| `settle` < age ≤ `settle + HEAL_WINDOW`, value ≠ target | window glitch (tier 2) | **heal** |
| age > `settle + HEAL_WINDOW`, value ≠ target, reachable | manual change | latch `manual` (today) |
| value matches target (any time) | converged | ignore (today) |

**Heal action.** Re-assert *only the diverged bulb* by re-applying its
entry from `_active_scene_targets` via a `light.turn_on` with the target
attributes and **no transition** (instant snap). The area's scene/state is
**not** changed — it stays in the scene it was already in. `commanded_at`
for that bulb is re-stamped so the heal command does not re-trigger
detection on itself.

**Loop protection.** Each heal of a bulb is recorded. If a bulb is healed
more than `SCENE_HEAL_MAX_ATTEMPTS` (3) times within a rolling
`SCENE_HEAL_ATTEMPT_WINDOW_SECONDS` (300 s) window, the component **gives
up** on that bulb: it stops healing, latches the area to `manual` (so it
isn't fighting a genuinely broken/flapping bulb), and raises a Repairs
issue. This makes the worst case "behave like today, plus a diagnostic."

**Post-settle self-check (closes the during-fade gap).** When a visual
scene is activated, schedule exactly one delayed callback at `settle`. It
compares every target bulb to its target and heals any mismatch. This
catches a glitch that occurred *while* the fade was running (which the
event path ignores). A subsequent scene command cancels/replaces the
pending check — at most one outstanding per area.

**Alert surface (Repairs + logs only).** No new entity, no push.

- `info` log on each heal: `Area X: healed scene drift entity=… reason=recovery|glitch_window|post_settle`.
- `debug` logs for classification skips (today's lines are retained).
- A Repairs issue (`scene_drift_unhealable`) is raised when the loop cap
  trips, naming the area + bulb + likely cause ("repeatedly diverging from
  its scene — probable Hue mesh/power problem"). It is **cleared
  automatically** when that bulb next matches its target or the area is
  cleanly re-activated/turned off.

**Configuration.** A top-level `scene_self_heal` boolean (default `true`)
in `area_lighting.yaml` globally enables the feature. When `false`, the
classification falls through to today's behavior exactly (divergence →
`manual`), no healing, no self-check, no Repairs issue. There is no
per-area toggle.

## Implementation

### Constants — `const.py`

Add next to `MANUAL_DETECTION_GRACE_SECONDS` (line 76):

```python
SCENE_HEAL_WINDOW_SECONDS = 60          # tier-2 glitch window after settle
SCENE_HEAL_MAX_ATTEMPTS = 3             # heals per bulb per attempt window
SCENE_HEAL_ATTEMPT_WINDOW_SECONDS = 300 # rolling window for the loop cap
```

Repairs issue id: `SCENE_DRIFT_ISSUE_ID = "scene_drift_unhealable"`.

### Config flag — `models.py` / `config_schema.py`

`AreaLightingConfig` (`models.py:290`) gains one field:

```python
scene_self_heal: bool = True
```

Add `vol.Optional("scene_self_heal", default=True): cv.boolean` to the root
schema in `config_schema.py`, and thread it through wherever the root dict
is parsed into `AreaLightingConfig`. The controller reads it once via
`_config(hass).scene_self_heal` (already available through
`hass.data[DOMAIN]["config"]`), cached as `self._scene_self_heal: bool` in
`__init__`.

### Detection routing — `event_handlers.py`

`_make_manual_detection_handler` (lines 471–574) keeps its existing skip
ladder (new-state-not-on, already-manual, dimmed, area-off, circadian,
alert, grace window, transition window, matches-target). The change is at
the **tail**, replacing the unconditional `manual detection fired` with a
classification:

```python
# (reached only when the bulb is ON, area is in a managed scene, past the
#  grace + transition windows, and the value diverges from the target)
if ctrl.scene_self_heal_enabled:
    import time as _time
    target = ctrl._active_scene_targets.get(entity_id)
    is_recovery = old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
    in_glitch_window = False
    if target is not None:
        commanded_at = target.get("commanded_at")
        transition = target.get("transition") or 0.0
        if commanded_at is not None:
            age = _time.monotonic() - commanded_at
            settle = transition + MANUAL_DETECTION_GRACE_SECONDS
            in_glitch_window = age <= settle + SCENE_HEAL_WINDOW_SECONDS
    if target is not None and (is_recovery or in_glitch_window):
        reason = "recovery" if is_recovery else "glitch_window"
        hass.async_create_task(ctrl.handle_scene_drift_reassert(entity_id, reason))
        return

_LOGGER.info("Area %s: manual detection fired entity=%s", area_id, entity_id)
hass.async_create_task(ctrl.handle_manual_light_change())
```

`STATE_UNAVAILABLE` / `STATE_UNKNOWN` are imported from
`homeassistant.const`. `ctrl.scene_self_heal_enabled` is a property over
the cached flag. The recovery branch keys off `old_state.state`, which the
handler already has in scope (line 498).

### Heal + loop protection — `controller.py`

State, added in `__init__` near `_active_scene_targets` (line 133):

```python
self._heal_attempts: dict[str, list[float]] = {}   # entity_id -> monotonic stamps
self._heal_selfcheck_unsub: Callable[[], None] | None = None
```

New method:

```python
async def handle_scene_drift_reassert(self, entity_id: str, reason: str) -> None:
    """Re-assert a single bulb's active scene target after a detected glitch.

    Subject to the loop cap: after SCENE_HEAL_MAX_ATTEMPTS heals within
    SCENE_HEAL_ATTEMPT_WINDOW_SECONDS, give up — latch manual and raise a
    Repairs issue instead of re-asserting again.
    """
    target = self._active_scene_targets.get(entity_id)
    if target is None or target.get("state") != "on":
        return

    now = time.monotonic()
    stamps = [t for t in self._heal_attempts.get(entity_id, [])
              if now - t < SCENE_HEAL_ATTEMPT_WINDOW_SECONDS]
    if len(stamps) >= SCENE_HEAL_MAX_ATTEMPTS:
        _LOGGER.warning(
            "Area %s: scene-heal gave up on %s after %d attempts; latching manual",
            self.area.id, entity_id, len(stamps))
        self._raise_scene_drift_issue(entity_id)
        await self.handle_manual_light_change()
        return

    stamps.append(now)
    self._heal_attempts[entity_id] = stamps
    _LOGGER.info("Area %s: healed scene drift entity=%s reason=%s",
                 self.area.id, entity_id, reason)
    # Instant re-assert; re-stamp commanded_at so the heal doesn't self-trigger.
    await self._apply_light_state(entity_id, target, transition=None)
    self._active_scene_targets[entity_id] = {**target, "commanded_at": now}
```

`_apply_light_state` (line 861) already passes the allowlisted attributes
through and emits `light.turn_on`; calling it with `transition=None` yields
an instant snap.

**Issue raise/clear** (mirrors the `ir.async_create_issue` /
`async_delete_issue` pattern in `event_handlers.py:260–276`):

```python
def _raise_scene_drift_issue(self, entity_id: str) -> None:
    from homeassistant.helpers import issue_registry as ir
    ir.async_create_issue(
        self.hass, DOMAIN, f"{SCENE_DRIFT_ISSUE_ID}_{self.area.id}",
        is_fixable=False, is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=SCENE_DRIFT_ISSUE_ID,
        translation_placeholders={"area": self.area.name, "entity": entity_id})

def _clear_scene_drift_issue(self) -> None:
    from homeassistant.helpers import issue_registry as ir
    ir.async_delete_issue(self.hass, DOMAIN, f"{SCENE_DRIFT_ISSUE_ID}_{self.area.id}")
    self._heal_attempts.clear()
```

Call `_clear_scene_drift_issue()` from `_activate_scene` (after a clean
visual-scene activation) and from the all-off path (`handle_lights_all_off`,
line 1432) so a healthy transition resets the give-up state.

### Post-settle self-check — `controller.py`

In `_activate_scene`'s visual-scene branch (lines 743–752), after
`_apply_scene_data`, schedule the one-shot verifier (only when
`self._scene_self_heal` and the scene has targets):

```python
from homeassistant.helpers.event import async_call_later

if self._heal_selfcheck_unsub is not None:
    self._heal_selfcheck_unsub()          # supersede any pending check
    self._heal_selfcheck_unsub = None
if self._scene_self_heal and self._active_scene_targets:
    delay = (transition or 0.0) + MANUAL_DETECTION_GRACE_SECONDS + 1.0
    self._heal_selfcheck_unsub = async_call_later(
        self.hass, delay, self._run_post_settle_selfcheck)
```

```python
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
                self.handle_scene_drift_reassert(entity_id, "post_settle"))
```

The pending check is also cancelled in the unload/`async_unload` path
alongside the existing unsubscribes.

### Diagnostics — `controller.py`

Extend `diagnostic_snapshot()` (line 327) with:

```python
"scene_self_heal_enabled": self._scene_self_heal,
"scene_heal_attempts": {k: len(v) for k, v in self._heal_attempts.items()},
```

`_heal_attempts` is intentionally **ephemeral** — not added to
`state_dict()` / `load_persisted_state()`; the loop window resets on
restart, which is the desired behavior. Repairs issues use
`is_persistent=False`, matching the existing missing-entities issue.

### Translations — `translations/en.json` + `strings.json`

Add an `issues.scene_drift_unhealable` block (title + description using the
`{area}` / `{entity}` placeholders), consistent with the existing
`missing_external_entities` issue. `tests/test_translations.py` enforces
parity, so both files must be updated.

## Testing

New `tests/integration/test_scene_self_healing.py`:

1. **Tier-2 window heal.** Activate a scene (with a transition), advance the
   monotonic clock past `settle` but within `settle + 60 s`, fire a divergent
   on-state event for a member bulb → assert a `light.turn_on` re-assert to
   the target was issued and the area stayed in its scene (not `manual`).
2. **Beyond the window → manual.** Same divergence at `settle + 61 s+` →
   asserts `handle_manual_light_change` ran and no re-assert was issued.
3. **Recovery heal.** Member goes `on → unavailable → on` at a divergent
   value while the scene is active → heal re-assert, area stays in scene.
4. **Matches target → no-op.** Recovery/divergence event whose value matches
   the target (within tolerance) → no re-assert, no manual latch.
5. **Loop cap → Repairs + manual.** Force >3 heals of one bulb within the
   window → assert the 4th gives up, raises issue `scene_drift_unhealable_<area>`,
   and latches `manual`; then a matching observation clears the issue.
6. **Post-settle self-check.** Glitch a bulb *during* the fade (event path
   ignores it as settling); advance to the scheduled callback → assert the
   self-check heals it.
7. **Incident replay.** Reproduce the `upstairs_bathroom` sequence (ambient
   fade → `right_w` divergence at +110 s → `left_w` unavailable→recovery) →
   assert the area ends in `ambient` with all members consistent and never
   latches `manual`.
8. **Kill-switch off.** With `scene_self_heal=false`, a window-glitch
   divergence latches `manual` exactly as today; no self-check is scheduled.

Unit coverage for the classification boundary lives alongside the existing
`tests/test_state_matches_scene_target.py` style. All tests use the current
`pytest-homeassistant-custom-component` harness; no new dependencies. The
monotonic clock is advanced via the existing freezer/patch helpers used in
`tests/integration/test_per_entity_transition_grace.py`.

## Out of scope

- **Per-area configuration.** One global `scene_self_heal` flag only; the
  loop cap makes a blanket default safe.
- **A diagnostic entity or push notification.** Surface is Repairs + logs;
  the snapshot fields are for the existing diagnostics download.
- **Distinguishing a fast genuine manual tweak inside the glitch window**
  from a glitch. With the chosen `settle + 60 s` window, a manual change
  made within ~1 minute of a scene command may be re-asserted. This is an
  accepted trade-off (chosen over a tighter window that would miss the
  real-world +110 s case).
- **Healing other integrations' lights.** Only entities the component owns
  (`area.lights`) and tracks in `_active_scene_targets` are eligible.
- **Persisting heal counters across restarts.** Deliberately ephemeral.
