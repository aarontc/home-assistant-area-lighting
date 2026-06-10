# Changelog

All notable changes to this project are recorded here. Versions and tags are
created automatically on `main` by the `tag:auto` CI job from commit subject
prefixes (`(Major)` / `(Minor)` / `(Patch)`); this file is a curated, human-
readable companion that highlights user-facing changes.

## Unreleased

### Added

- **Scene self-healing** — out-of-band Hue glitches (power-on defaults, RF
  dropouts, recovery from `unavailable`) are now auto-corrected back to the
  active scene instead of latching the area to `manual`. A bulb that keeps
  diverging (more than 3 heals within 5 minutes) is left in `manual` and
  raises a Home Assistant Repairs issue (`scene_drift_unhealable`), cleared
  the next time the area changes scene or turns off. Disable globally with
  `scene_self_heal: false`.

- **Circadian kelvin routes** — new per-area `circadian_kelvin_routes:` config
  that, while the `circadian` scene is active, dispatches a configured set of
  lights between mutually-exclusive routes based on the live target color
  temperature. Solves the "fluorescent + Hue strip" mixed-fixture problem.
  See [`CONFIGURATION.md`](CONFIGURATION.md) § "Circadian kelvin routes".

### Fixed

- **Scene `rgbw_color` / `rgbww_color` silently dropped** — the scene-apply
  allowlist omitted `rgbw_color` and `rgbww_color`, so a scene specifying them
  (e.g. a christmas scene driving WiZ `rgbww` bulbs) turned the lights on but
  never changed their color — only `brightness`/`state` were applied. Both keys
  are now forwarded to `light.turn_on`, letting Home Assistant convert them to
  the bulb's native color mode (e.g. `rgbw` → `rgbww`). The honored scene
  attributes now live in a single `SCENE_LIGHT_ON_ATTRIBUTES` allowlist shared
  by the apply paths and the config schema, so the two can't drift again.

  To prevent this class of silent failure, a scene's per-light `entities` block
  is now **strictly validated**: only supported attributes (`state` plus the
  allowlist) are accepted, so an unsupported key — e.g. `color_mode`, which is
  read-only on a light and was never applied — now raises at startup instead of
  being silently ignored. **Action required:** remove any `color_mode` (or other
  non-`light.turn_on`) keys from scene `entities`.

- **Linked-motion remote area stranded on after re-triggered motion** — a
  remote area lit by `linked_motion` (e.g. the theater raised to its pass-through
  scene by stairs motion) would never turn back off when the source area's
  motion re-triggered while the remote was still in the linked scene. That
  re-trigger resolves to no remote activation (the remote's current scene maps
  to `remote_scene: null`), and `_activate_linked_areas` cleared the
  pending-cleanup tracking unconditionally — so the later motion-timer expiry
  had nothing to turn off. `_activate_linked_areas` now merges into the tracking
  instead of replacing it; cleanup still guards on the remote's current scene,
  so stale entries are harmless.

- **Spurious manual detection during long fade transitions** — when a scene
  activation carried a long fade (e.g. `lighting_off_fade` with a 60 s
  motion fadeout), the area-wide 4 s grace window expired well before the
  fade did. The bulb kept reporting `state=on` with brightness gradually
  decreasing toward the fade endpoint; `state_matches_scene_target` saw
  the divergence and demoted the area to `manual` mid-transition, breaking
  the next remote / motion cycle. Every entry in `_active_scene_targets`
  now carries a `commanded_at` monotonic timestamp and the `transition`
  duration that went with it, and manual detection consults those per-entity
  values to skip comparisons until the commanded transition has elapsed
  (plus the existing 4 s buffer).
- **Spurious manual detection on xy-native bulbs** — when a scene targeted
  `hs_color` and the bulb's native color space is xy (Philips Hue and
  similar), the hs ↔ xy round trip across the bridge's gamut clamping shifted
  the reported hue past the 10° tolerance and demoted the area to `manual`
  even though the bulb was doing exactly what was asked. The most visible
  symptom was an "on" remote press in the bedroom appearing to do nothing
  while night mode was active: `manual` + `night_mode=True` correctly
  re-activates the night scene, which is already on-screen. `state_matches_scene_target`
  now compares in xy space when the bulb's actual `color_mode` is `xy`,
  converting an hs target via `homeassistant.util.color.color_hs_to_xy`. The
  hs-mode comparison path is unchanged.
