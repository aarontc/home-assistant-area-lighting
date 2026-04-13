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
