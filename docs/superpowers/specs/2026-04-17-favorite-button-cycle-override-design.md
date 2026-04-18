# Design: Per-Remote Favorite Button Scene Cycle Override

## Summary

Allow each Lutron remote's favorite button to activate a configured scene or
cycle of scenes, overriding the default holiday/night cycling. Backward
compatible — remotes without the override keep existing behavior.

## Config surface

`AreaConfig.lutron_remotes[].buttons.favorite` accepts:

1. **A single area scene slug** — always activates that scene.

   ```yaml
   buttons:
     favorite: night
   ```

2. **A list of area scene slugs** — cycles through them on repeated presses.

   ```yaml
   buttons:
     favorite:
       - reading
       - night
   ```

3. **A Home Assistant scene entity ID** — calls `scene.turn_on`. Single target
   only (no cycling). Detected by `scene.` prefix.

   ```yaml
   buttons:
     favorite: scene.main_bedroom_reading
   ```

### Validation

- Bare slugs are validated at config-parse time against the owning area's
  scenes. Unknown slugs raise `vol.Invalid`.
- `scene.` entity IDs are not validated at config time (the entity may not
  exist yet).
- `scene.` entity IDs cannot appear in a list (cycling requires slug-based
  state tracking).

## Cycling logic

Same pattern as the on-button scene cycling:

- If the current scene matches an entry in the cycle, activate the **next**
  entry (wrapping around).
- If the current scene doesn't match any entry, activate the **first** entry.

Example for `[reading, night]`: off -> reading -> night -> reading -> ...

## Default behavior (when override is absent)

Unchanged. `determine_favorite_action` in `scene_machine.py` handles the
existing holiday/night cycling:

1. If holiday mode active and area has matching holiday scene and not
   already in holiday scene, activate holiday scene.
2. Otherwise activate `night`.

## State machine integration

When the override is set, the favorite button bypasses
`determine_favorite_action` entirely:

- `<any state> -> <configured scene>`: favorite / `buttons.favorite` set
- Existing holiday/night edges remain as fallback when no override is
  configured.

The override uses the same `_activate_scene` pipeline as any other scene
activation, so motion/occupancy timer interaction, manual-detection grace
period, and persistence all apply automatically.

## Activation source

`ActivationSource.USER` — consistent with all other remote buttons, which
currently use their method's default USER source.

## Interactions

- **Dimmed state**: clears dimmed flag, overwrites dimmed restore context.
- **Ambience**: never enters ambience-owned state. Standard off-button
  ambience gating still applies.
- **Motion/occupancy timers**: cancels motion timer (area is now user-owned).
  Occupancy timer continues.
- **Alerts**: alerts take priority per existing semantics.
- **Persistence**: no new persisted state. Scene persistence captures the
  activated scene.

## Implementation touchpoints

### `config_schema.py`

- Replace `vol.Optional("buttons", default={}): dict` with a typed schema
  accepting `{favorite: str | list[str]}`.
- Add post-parse validation: for each remote, if `buttons.favorite` contains
  bare slugs, validate them against the area's scene list.
- Normalize to `list[str]` and store on `LutronRemoteConfig.favorite_cycle`.

### `models.py`

- Add `favorite_cycle: list[str]` field to `LutronRemoteConfig`.

### `controller.py`

- Add `favorite_cycle: list[str] | None = None` parameter to
  `lighting_favorite`.
- If set and single `scene.` entry: call `scene.turn_on`, return.
- If set: find current scene in cycle, activate next (or first), return.
- If not set: fall through to existing `determine_favorite_action`.

### `event_handlers.py`

- In `_make_remote_handler`, special-case the favorite button dispatch to
  pass `remote.favorite_cycle` to `ctrl.lighting_favorite`.

### `scene_machine.py`

- No changes. Override bypasses `determine_favorite_action`.

## Test plan

### Unit tests (`tests/test_favorite_override.py`)

1. Default (no override) + holiday active + area has holiday scene ->
   holiday scene activated.
2. Default + no holiday + area has night -> night activated.
3. Default + no holiday + no night -> night activated (current behavior).
4. Single slug override: `night` -> night activated.
5. Single slug override: `circadian` + holiday active -> circadian activated
   (override wins).
6. Cycle `[reading, night]`: from off -> reading (first in cycle).
7. Cycle `[reading, night]`: from reading -> night (next in cycle).
8. Cycle `[reading, night]`: from night -> reading (wraps around).
9. Cycle `[reading, night]`: from manual -> reading (not in cycle, starts at
   first).
10. `scene.some_custom` -> `scene.turn_on` service call emitted.
11. Override + area was dimmed -> dimmed cleared, target scene activated.
12. Override + motion-owned state -> scene activated with source USER.

### Schema tests (`tests/test_favorite_override.py`)

13. Bare slug referring to unknown scene -> `vol.Invalid`.
14. `scene.whatever` entity id -> accepted.
15. Bare slug from a different area -> `vol.Invalid`.
16. List with valid slugs -> accepted.
17. List with `scene.` entry -> rejected.

### Integration tests (`tests/integration/test_remote_events.py`)

18. Fire `lutron_caseta_button_event` with button `stop` (favorite) + remote
    has `buttons.favorite: scene.foo` -> `scene.turn_on` service call.
19. Same with bare slug -> area scene machine transitioned.
20. Cycle test: fire favorite twice -> verify cycle progression.
