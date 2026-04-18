* ~~Add an appropriate mdi icon for each of the entities created by this integration.~~ **Done.** Icons updated 2026-04-09:
  * `switch.{area}_motion_light_enabled` → `mdi:motion-sensor`
  * `switch.{area}_motion_override_ambient` → `mdi:shield-off-outline` (ambient guard off)
  * `number.{area}_manual_fadeout_seconds` → `mdi:remote` (remote-initiated)
  * `number.{area}_motion_fadeout_seconds` → `mdi:motion-pause-outline` (motion just ended)
  * `number.{area}_motion_timeout_minutes` → `mdi:timer-sand` (countdown)
  * `number.{area}_motion_night_timeout_minutes` → `mdi:timer-sand-empty` (shorter night variant)
  * `number.{area}_occupancy_timeout_minutes` → `mdi:account-clock`
  * `number.{area}_occupancy_night_timeout_minutes` → `mdi:account-clock-outline`

  Regression test at `tests/integration/test_entity_naming.py::test_entity_icons_match_function` locks these in.

* Implement a user-friendly configuration flow for setting up the area lighting integration. **Deferred.** YAML stays the source of truth for now. Tracked as README stretch goal #7 ("ConfigEntry-based integration"). Converting to ConfigFlow would also enable Settings → Devices → Create dashboard (currently we only group via Settings → Areas).

* ~~Hue Zone / light cluster support (not yet ported from templater).~~ **Done.** Implemented in `cluster_dispatch.py` (2026-04-09). Per-scene scoping via `LightConfig.scenes` and greedy cluster selection are both active. All 9 Hue Zone clusters across 7 areas are configured with member lists from the live HA API. Proof tests in `tests/test_cluster_dispatch.py`.

* **Scene cycle definitions.** `SceneConfig.cycle` is parsed from config but unused at runtime. The `determine_on_action()` function in `scene_machine.py` uses hardcoded cycling logic. Affects main_bedroom bedside_aaron/mara scenes which define custom cycle sequences in templater.yaml.

* **Custom remote button mappings.** `LutronRemoteConfig.buttons` is parsed but the remote handler hardcodes `LUTRON_BUTTON_MAP`. The favorite button always triggers the standard favorite action instead of respecting per-remote overrides. Affects main_bedroom bedside remotes (bedside_east→bedside_aaron, bedside_west→bedside_mara, entry→night).

* **Follow-area scene mirroring.** `follow_area_id` was removed (D11). main_closet (→main_bathroom) and pantry (→kitchen) now operate independently instead of mirroring their parent area's scene.

* **Submit logo to `home-assistant/brands`.** HA and HACS load brand images exclusively from `brands.home-assistant.io`, backed by [home-assistant/brands](https://github.com/home-assistant/brands) — not from this repo. Until a PR is merged there, the logo only shows in the rendered README (HACS detail view, GitLab/GitHub repo pages). Submit a PR adding `custom_integrations/area_lighting/` with the following files, generated from `assets/Home Assistant Area Lighting.png` (1024×1024 master):
  * `icon.png` — 256×256, square, PNG with transparency, **visible content in the inner 192×192** (~32px transparent padding on all sides)
  * `icon@2x.png` — 512×512, square, ~64px transparent padding (visible content 384×384)
  * `logo.png` — (optional, only if we want a wordmark variant) max height 128, transparent background
  * `logo@2x.png` — (optional) max height 256
  
  Spec: https://github.com/home-assistant/brands#guideline. Validated by `hassfest` in the brands repo CI. After merge, the logo appears automatically in HA's "Add Integration" picker, the configured integration card (Settings → Devices & Services), and HACS's card + detail view.


* ~~Add "alert" feature to flash lights in an area or all areas for a short time~~ **Done.** Alert patterns defined globally under `alert_patterns:` in config YAML. `area_lighting.alert` service takes `area_id` + `pattern`. Supports color/white target filtering, repeat, start_inverted, and restore. Cluster dispatch optimization. See README § Alerts.

* ~~**HACS version display: create GitHub releases from tags.**~~ **Done.** GitHub Actions workflow `.github/workflows/release.yaml` on the GitHub mirror runs every 5 minutes, scans `v*` tags, and publishes a release for any tag missing one — severity-grouped notes from `git log`, version-bump chore filtered out, auto-provided `GITHUB_TOKEN` for the API call. Up to ~5 min latency between mirror sync and release; idempotent, race-free, self-healing. Replaced an earlier in-`tag:auto` poll-and-release attempt that never produced a successful release, and a short-lived `push: tags` design that GitHub did not reliably fire events for on mirror pushes. Spec at `docs/superpowers/specs/2026-04-17-github-release-publishing-design.md`.

* Add "party mode" features with color cycling effects

