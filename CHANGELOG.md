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
