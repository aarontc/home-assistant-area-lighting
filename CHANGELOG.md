# Changelog

All notable changes to this project are recorded here. Versions and tags are
created automatically on `main` by the `tag:auto` CI job from commit subject
prefixes (`(Major)` / `(Minor)` / `(Patch)`); this file is a curated, human-
readable companion that highlights user-facing changes.

## Unreleased

### Added

- **Circadian kelvin routes** — new per-area `circadian_kelvin_routes:` config
  that, while the `circadian` scene is active, dispatches a configured set of
  lights between mutually-exclusive routes based on the live target color
  temperature. Solves the "fluorescent + Hue strip" mixed-fixture problem.
  See [`CONFIGURATION.md`](CONFIGURATION.md) § "Circadian kelvin routes".

### Fixed

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
