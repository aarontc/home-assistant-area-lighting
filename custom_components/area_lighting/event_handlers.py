"""Event handlers for the Area Lighting integration.

Replaces all global automations by registering HA event/state listeners
and routing events to the appropriate AreaLightingController.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
)

from .area_state import ActivationSource
from .const import (
    DOMAIN,
    GLOBAL_MOTION_LIGHT_ENABLED_ENTITY,
    HOLIDAY_MODE_ENTITY,
    MANUAL_DETECTION_BRIGHTNESS_THRESHOLD,
    MANUAL_DETECTION_GRACE_SECONDS,
)
from .controller import AreaLightingController
from .models import AreaLightingConfig

_LOGGER = logging.getLogger(__name__)


def _controllers(hass: HomeAssistant) -> dict[str, AreaLightingController]:
    return hass.data[DOMAIN]["controllers"]


def _config(hass: HomeAssistant) -> AreaLightingConfig:
    return hass.data[DOMAIN]["config"]


_REPAIRS_ISSUE_ID = "missing_external_entities"


def _build_circadian_switches_block(config: AreaLightingConfig) -> str:
    """Generate the switch: block for all circadian_lighting switches
    referenced by the enabled areas.

    Matches the shape templater's helpers/circadian_switches.erb.yaml
    produced: one switch entry per CircadianSwitchConfig, with lights
    grouped by circadian_type into lights_ct / lights_brightness / lights_rgb.
    """
    lines: list[str] = ["switch:"]
    for area in config.enabled_areas:
        for cs in area.circadian_switches:
            lines.append(f"  - name: {cs.full_name}")
            lines.append("    platform: circadian_lighting")
            if cs.max_brightness is not None:
                lines.append(f"    max_brightness: {cs.max_brightness}")
            if cs.min_brightness is not None:
                lines.append(f"    min_brightness: {cs.min_brightness}")
            # Group lights by circadian_type, matching the templater
            by_type: dict[str, list[str]] = {}
            for light in area.all_lights:
                if light.circadian_switch != cs.short_name:
                    continue
                if not light.circadian_type:
                    continue
                by_type.setdefault(light.circadian_type, []).append(light.id)
            for ctype in ("ct", "brightness", "rgb"):
                if ctype in by_type:
                    lines.append(f"    lights_{ctype}:")
                    lines.extend(f"      - {light_id}" for light_id in by_type[ctype])
    return "\n".join(lines) if len(lines) > 1 else ""


def _build_bootstrap_yaml(
    missing: set[str],
    zones: set[str],
    config: AreaLightingConfig,
) -> str:
    """Generate a copy-pasteable YAML block for whichever helpers are missing."""
    input_select_lines: list[str] = []
    if "input_select.holiday_mode" in missing:
        input_select_lines.append(
            "  holiday_mode:\n"
            "    name: Holiday Mode\n"
            "    options: [none, christmas, halloween]\n"
            "    initial: none\n"
            "    icon: mdi:party-popper"
        )
    if "input_select.ambient_scene" in missing:
        input_select_lines.append(
            "  ambient_scene:\n"
            "    name: Ambient Scene\n"
            "    options: [ambient, holiday]\n"
            "    initial: ambient"
        )

    input_boolean_lines: list[str] = []
    if "input_boolean.lighting_circadian_daylight_lights_enabled" in missing:
        input_boolean_lines.append(
            "  lighting_circadian_daylight_lights_enabled:\n"
            "    name: Circadian Daylight Lights Enabled"
        )
    if "input_boolean.motion_light_enabled" in missing:
        input_boolean_lines.append(
            "  motion_light_enabled:\n    name: Motion Lighting (Global)\n    initial: true"
        )
    for zone in sorted(zones):
        entity = f"input_boolean.lighting_{zone}_ambient"
        if entity in missing:
            input_boolean_lines.append(
                f"  lighting_{zone}_ambient:\n    name: {zone.title()} Ambient Zone"
            )

    blocks: list[str] = []
    if input_select_lines:
        blocks.append("input_select:\n" + "\n".join(input_select_lines))
    if input_boolean_lines:
        blocks.append("input_boolean:\n" + "\n".join(input_boolean_lines))

    # circadian_lighting top-level config (for the HACS integration itself)
    # — always include if sensor.circadian_values is missing.
    if "sensor.circadian_values" in missing:
        blocks.append(
            "# Requires the circadian_lighting HACS integration:\n"
            "# https://github.com/basnijholt/circadian_lighting\n"
            "circadian_lighting:\n"
            "  interval: 60\n"
            "  min_colortemp: 2700\n"
            "  max_colortemp: 5600"
        )

    # switch: block — include if ANY circadian switch is missing OR if
    # sensor.circadian_values is missing (because without the top-level
    # integration, no switches can exist either).
    circadian_switch_missing = any(
        entity.startswith("switch.circadian_lighting_") for entity in missing
    )
    if circadian_switch_missing or "sensor.circadian_values" in missing:
        switch_block = _build_circadian_switches_block(config)
        if switch_block:
            blocks.append(switch_block)

    return "\n\n".join(blocks) if blocks else "# (no YAML-creatable helpers missing)"


async def async_validate_external_entities(
    hass: HomeAssistant,
    config: AreaLightingConfig,
) -> list[str]:
    """Verify required external entities exist (D10).

    On failure: logs a single error, AND creates a persistent HA repairs
    issue containing a copy-paste YAML bootstrap block. On success:
    clears any previous repairs issue.

    Returns the list of missing entity IDs (empty on success).
    """
    from homeassistant.helpers import issue_registry as ir

    required: list[tuple[str, str]] = [
        (HOLIDAY_MODE_ENTITY, "global holiday mode (input_select)"),
        ("input_select.ambient_scene", "ambient scene mode (input_select)"),
        (
            "input_boolean.lighting_circadian_daylight_lights_enabled",
            "circadian sun-position proxy (input_boolean)",
        ),
        (
            "input_boolean.motion_light_enabled",
            "global motion lighting kill-switch (input_boolean)",
        ),
        (
            "sensor.circadian_values",
            "circadian_lighting integration sensor",
        ),
    ]

    seen_zones: set[str] = set()
    for area in config.enabled_areas:
        if area.ambient_lighting_zone and area.ambient_lighting_zone not in seen_zones:
            seen_zones.add(area.ambient_lighting_zone)
            required.append(
                (
                    f"input_boolean.lighting_{area.ambient_lighting_zone}_ambient",
                    f"ambient zone toggle for zone '{area.ambient_lighting_zone}'",
                )
            )
        for cs in area.circadian_switches:
            required.append(
                (
                    cs.entity_id,
                    f"circadian switch for {area.name}/{cs.name}",
                )
            )
            # Verify the lights this switch is supposed to control actually
            # exist in HA. area_lighting config drives the switch → lights
            # mapping; if a light is renamed or missing, the circadian_lighting
            # integration silently ignores it, which is a very confusing failure.
            for light in area.all_lights:
                if light.circadian_switch != cs.short_name:
                    continue
                required.append(
                    (
                        light.id,
                        f"light assigned to {cs.full_name} ({light.circadian_type or 'no type'})",
                    )
                )

    missing: list[str] = []
    missing_lines: list[str] = []
    seen: set[str] = set()
    for entity_id, description in required:
        if entity_id in seen:
            continue
        seen.add(entity_id)
        if hass.states.get(entity_id) is None:
            missing.append(entity_id)
            missing_lines.append(f"  - {entity_id}  ({description})")

    if missing:
        missing_set = set(missing)
        bootstrap_yaml = _build_bootstrap_yaml(missing_set, seen_zones, config)
        missing_list_text = "\n".join(missing_lines)

        _LOGGER.error(
            "area_lighting: %d required external entities are missing.\n"
            "These must exist in Home Assistant for the integration to "
            "function correctly:\n%s\n\n"
            "Copy-paste this into configuration.yaml (or a package file) "
            "to create the missing helpers:\n\n%s\n\n"
            "See custom_components/area_lighting/README.md "
            "'Required external entities' section for full setup instructions.",
            len(missing),
            missing_list_text,
            bootstrap_yaml,
        )

        ir.async_create_issue(
            hass,
            DOMAIN,
            _REPAIRS_ISSUE_ID,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=_REPAIRS_ISSUE_ID,
            translation_placeholders={
                "count": str(len(missing)),
                "missing_list": missing_list_text,
                "bootstrap_yaml": bootstrap_yaml,
            },
        )
    else:
        # All entities present → clear any previous issue
        ir.async_delete_issue(hass, DOMAIN, _REPAIRS_ISSUE_ID)

    return missing


async def async_setup_event_handlers(hass: HomeAssistant) -> list:
    """Register all event listeners. Returns list of unsub callbacks."""
    unsubs: list = []
    config = _config(hass)
    controllers = _controllers(hass)

    # ── Scene activation tracking (replaces global_last_scene_updater) ──
    unsubs.append(hass.bus.async_listen("call_service", _make_scene_tracker(hass, config)))

    # ── Per-area listeners ──────────────────────────────────────────────
    for area in config.enabled_areas:
        if not area.event_handlers:
            continue
        ctrl = controllers.get(area.id)
        if not ctrl:
            continue

        all_light_ids = [light.id for light in area.all_lights]

        # Lights-off detection — track individual lights; the handler
        # aggregates to "all off" itself.
        if all_light_ids:
            unsubs.append(
                async_track_state_change_event(
                    hass,
                    all_light_ids,
                    _make_lights_off_handler(hass, ctrl),
                )
            )

            # Manual light detection — same subscription list
            unsubs.append(
                async_track_state_change_event(
                    hass,
                    all_light_ids,
                    _make_manual_detection_handler(hass, ctrl),
                )
            )

        # Motion sensors
        if area.has_motion_lighting and area.motion_light_motion_sensor_ids:
            unsubs.append(
                async_track_state_change_event(
                    hass,
                    area.motion_light_motion_sensor_ids,
                    _make_motion_handler(hass, ctrl, area),
                )
            )

        # Occupancy sensors
        if area.has_occupancy_lighting and area.occupancy_light_sensor_ids:
            unsubs.append(
                async_track_state_change_event(
                    hass,
                    area.occupancy_light_sensor_ids,
                    _make_occupancy_handler(ctrl),
                )
            )
            # Track individual lights for occupancy timer management
            if all_light_ids:
                unsubs.append(
                    async_track_state_change_event(
                        hass,
                        all_light_ids,
                        _make_occupancy_light_handler(hass, ctrl),
                    )
                )

    # ── Ambient zone listeners ──────────────────────────────────────────
    ambient_entities = set()
    for area in config.enabled_areas:
        if area.event_handlers and area.ambient_lighting_zone:
            ambient_entities.add(f"input_boolean.lighting_{area.ambient_lighting_zone}_ambient")
    if ambient_entities:
        unsubs.append(
            async_track_state_change_event(
                hass,
                list(ambient_entities),
                _make_ambient_zone_handler(hass, config),
            )
        )

    # ── Holiday mode listener ───────────────────────────────────────────
    unsubs.append(
        async_track_state_change_event(
            hass,
            [HOLIDAY_MODE_ENTITY],
            _make_holiday_handler(hass, config),
        )
    )

    # ── Remote event listener ───────────────────────────────────────────
    unsubs.append(
        hass.bus.async_listen(
            "lutron_caseta_button_event",
            _make_remote_handler(hass, config),
        )
    )

    return unsubs


# ── Scene activation tracking ──────────────────────────────────────────


def _make_scene_tracker(hass: HomeAssistant, config: AreaLightingConfig):
    """Track scene.turn_on calls to update last_scene."""

    @callback
    def _handler(event: Event) -> None:
        if event.data.get("domain") != "scene" or event.data.get("service") != "turn_on":
            return

        service_data = event.data.get("service_data", {})
        entity_id = service_data.get("entity_id", "")
        if isinstance(entity_id, list):
            entity_id = entity_id[0] if entity_id else ""

        if not entity_id.startswith("scene."):
            return

        # Parse area_id and scene_slug from entity_id
        # Format: scene.{area_id}_{scene_slug}
        short = entity_id.removeprefix("scene.")
        controllers = _controllers(hass)

        for area_id, ctrl in controllers.items():
            prefix = f"{area_id}_"
            if short.startswith(prefix):
                scene_slug = short[len(prefix) :]
                if scene_slug:
                    hass.async_create_task(ctrl.handle_scene_activated(scene_slug))
                break

    return _handler


# ── Lights-off detection ───────────────────────────────────────────────


def _make_lights_off_handler(hass: HomeAssistant, ctrl: AreaLightingController):
    """Detect 'all lights in the area are now off' without relying on any
    pre-existing light group entity.

    Subscribed to every individual light in the area. On each off event,
    we check whether the remaining lights are ALL off (or unavailable);
    only then do we fire the all-off handler.
    """
    area = ctrl.area

    @callback
    def _handler(event: Event) -> None:
        new_state = event.data.get("new_state")
        if not new_state or new_state.state != STATE_OFF:
            return
        # Any light turning off → check if all lights are now off.
        for light in area.all_lights:
            st = hass.states.get(light.id)
            if st is not None and st.state == STATE_ON:
                return  # some light is still on
        ctrl.hass.async_create_task(ctrl.handle_lights_all_off())

    return _handler


# ── Manual light detection ─────────────────────────────────────────────


def _make_manual_detection_handler(hass: HomeAssistant, ctrl: AreaLightingController):
    """Handle light state change events for manual detection.

    Fires a transition to `manual` when the controller believes the
    area is in a scene/circadian but a light's brightness or color
    changed by more than the threshold outside of the integration.

    Intentionally does NOT consult any templater-generated light group
    entity — the component must work without the legacy templater. The
    gate is `ctrl._state.is_off`: if the controller thinks the area is
    off, any incoming 'on' state is treated as external scene activation
    (and ignored by manual detection), not as a manual override.
    """
    import time as _time

    area_id = ctrl.area.id

    def _skip(reason: str, entity_id: str) -> None:
        _LOGGER.debug(
            "area_lighting[%s] manual detection skipped (%s) for %s",
            area_id,
            reason,
            entity_id,
        )

    @callback
    def _handler(event: Event) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if not new_state or not old_state:
            return

        entity_id = new_state.entity_id

        if new_state.state != STATE_ON:
            _skip("new state not on", entity_id)
            return

        if ctrl.current_scene == "manual":
            _skip("already manual", entity_id)
            return

        if ctrl.dimmed:
            _skip("dimmed (raise/lower in progress)", entity_id)
            return

        # If the controller believes the area is off, treat any incoming
        # 'on' event as external scene activation, not a manual override.
        if ctrl._state.is_off:
            _skip("area state is off", entity_id)
            return

        # While the area is in circadian state, circadian_lighting is
        # actively firing light.turn_on calls every interval to adjust
        # brightness and color temperature. Those are NOT manual
        # overrides. Skip entirely — if the user wants to 'go manual'
        # from circadian, they should press a remote button or activate
        # a different scene, which transitions state away from circadian
        # and then this gate no longer applies.
        if ctrl._state.is_circadian:
            _skip("area state is circadian", entity_id)
            return

        # Grace period check (D5): ignore events inside the window after
        # a scene transition. 4 seconds by default.
        last_change = ctrl._state.last_scene_change_monotonic
        if last_change is not None:
            age = _time.monotonic() - last_change
            if age < MANUAL_DETECTION_GRACE_SECONDS:
                _skip(
                    f"within grace window ({age:.1f}s < {MANUAL_DETECTION_GRACE_SECONDS}s)",
                    entity_id,
                )
                return

        was_off = old_state.state == STATE_OFF
        old_brightness = old_state.attributes.get("brightness", 0) or 0
        new_brightness = new_state.attributes.get("brightness", 0) or 0
        brightness_change = abs(int(new_brightness) - int(old_brightness))

        color_changed = False
        changed_color_attr: str | None = None
        for attr in ("color_temp_kelvin", "hs_color", "rgb_color", "xy_color"):
            if old_state.attributes.get(attr) != new_state.attributes.get(attr):
                color_changed = True
                changed_color_attr = attr
                break

        if not (
            was_off or color_changed or brightness_change > MANUAL_DETECTION_BRIGHTNESS_THRESHOLD
        ):
            _skip(
                f"delta too small (brightness_change={brightness_change} "
                f"<= {MANUAL_DETECTION_BRIGHTNESS_THRESHOLD}, color unchanged)",
                entity_id,
            )
            return

        reason = (
            "was off"
            if was_off
            else f"color changed ({changed_color_attr})"
            if color_changed
            else f"brightness delta {brightness_change}"
        )
        _LOGGER.info(
            "area_lighting[%s] manual detection fired for %s: %s",
            area_id,
            entity_id,
            reason,
        )
        hass.async_create_task(ctrl.handle_manual_light_change())

    return _handler


# ── Motion handling ────────────────────────────────────────────────────


def _make_motion_handler(hass: HomeAssistant, ctrl: AreaLightingController, area):
    """Handle motion sensor state changes."""

    def _check_conditions() -> bool:
        """Check global + area motion conditions."""
        # Global motion enabled
        global_state = hass.states.get(GLOBAL_MOTION_LIGHT_ENABLED_ENTITY)
        if not global_state or global_state.state != STATE_ON:
            return False

        # Area motion enabled
        if not ctrl.motion_light_enabled:
            return False

        # Gate by ownership: motion may activate lighting when the area
        # is OFF, or retrigger an existing motion-owned scene (so the
        # timer countdown resets). It MUST NOT hijack a user-owned
        # scene — pressing evening on the remote should not get
        # auto-faded by some passing motion.
        #
        # motion_override_ambient widens the set of "overridable"
        # scenes to include the ambient-like ones.
        source_is_motion = ctrl._state.source == ActivationSource.MOTION
        if ctrl.motion_override_ambient:
            # Allow from off, motion-owned scenes, AND ambient-like
            if not (
                ctrl.current_scene == "off"
                or source_is_motion
                or ctrl.current_scene in ("ambient", "christmas", "halloween")
            ):
                return False
        else:
            if not (ctrl.current_scene == "off" or source_is_motion):
                return False

        # Area-specific conditions
        for cond in area.motion_light_conditions:
            state = hass.states.get(cond.entity_id)
            if not state:
                return False
            if cond.state is not None and state.state != cond.state:
                return False
            if cond.attribute is not None:
                attr_val = state.attributes.get(cond.attribute)
                if attr_val is None:
                    return False
                attr_val = float(attr_val)
                if cond.above is not None and attr_val <= cond.above:
                    return False
                if cond.below is not None and attr_val >= cond.below:
                    return False

        return True

    @callback
    def _handler(event: Event) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if not new_state or not old_state:
            return

        if new_state.state == STATE_ON and old_state.state != STATE_ON:
            if _check_conditions():
                hass.async_create_task(ctrl.handle_motion_on())
        elif new_state.state != STATE_ON and old_state.state == STATE_ON:
            # Motion transitioned OFF (or to unknown/unavailable from ON).
            # We consider the area "motion-ended" when NO sensor is
            # actively reporting 'on' — unavailable/unknown/None states
            # count as 'not reporting motion', otherwise one flaky
            # sensor would block the timer from ever starting.
            any_on = any(
                (s := hass.states.get(sid)) is not None and s.state == STATE_ON
                for sid in area.motion_light_motion_sensor_ids
            )
            if not any_on:
                hass.async_create_task(ctrl.handle_motion_off())

    return _handler


# ── Occupancy handling ─────────────────────────────────────────────────


def _make_occupancy_handler(ctrl: AreaLightingController):
    @callback
    def _handler(event: Event) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if not new_state or not old_state:
            return

        if new_state.state == STATE_ON and old_state.state != STATE_ON:
            ctrl.hass.async_create_task(ctrl.handle_occupancy_on())
        elif new_state.state != STATE_ON and old_state.state == STATE_ON:
            # Same unavailable/unknown tolerance as the motion handler:
            # "no sensor is reporting on" is the trigger.
            any_on = any(
                (s := ctrl.hass.states.get(sid)) is not None and s.state == STATE_ON
                for sid in ctrl.area.occupancy_light_sensor_ids
            )
            if not any_on:
                ctrl.hass.async_create_task(ctrl.handle_occupancy_off())

    return _handler


def _make_occupancy_light_handler(hass: HomeAssistant, ctrl: AreaLightingController):
    """Track aggregate on/off transitions of the area's lights without
    relying on any pre-existing light group entity.

    Uses a closure-local flag to remember whether the aggregate was
    previously on; fires occupancy_lights_on/off only on edges.
    """
    area = ctrl.area

    def _any_on() -> bool:
        for light in area.all_lights:
            st = hass.states.get(light.id)
            if st is not None and st.state == STATE_ON:
                return True
        return False

    state = {"was_on": _any_on()}

    @callback
    def _handler(event: Event) -> None:
        is_on = _any_on()
        if is_on == state["was_on"]:
            return
        state["was_on"] = is_on
        if is_on:
            ctrl.hass.async_create_task(ctrl.handle_occupancy_lights_on())
        else:
            ctrl.hass.async_create_task(ctrl.handle_occupancy_lights_off())

    return _handler


# ── Ambient zone handling ──────────────────────────────────────────────


def _make_ambient_zone_handler(hass: HomeAssistant, config: AreaLightingConfig):
    @callback
    def _handler(event: Event) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if not new_state or not old_state:
            return

        entity_id = new_state.entity_id
        # Extract zone name: input_boolean.lighting_{zone}_ambient
        zone = entity_id.removeprefix("input_boolean.lighting_").removesuffix("_ambient")

        controllers = _controllers(hass)
        for area in config.enabled_areas:
            if not area.event_handlers or area.ambient_lighting_zone != zone:
                continue
            ctrl = controllers.get(area.id)
            if not ctrl or not ctrl.ambience_enabled:
                continue

            if new_state.state == STATE_ON and old_state.state != STATE_ON:
                hass.async_create_task(ctrl.handle_ambient_enabled())
            elif new_state.state == STATE_OFF and old_state.state != STATE_OFF:
                hass.async_create_task(ctrl.handle_ambient_disabled())

    return _handler


# ── Holiday mode handling ──────────────────────────────────────────────


def _make_holiday_handler(hass: HomeAssistant, config: AreaLightingConfig):
    @callback
    def _handler(event: Event) -> None:
        new_state = event.data.get("new_state")
        if not new_state:
            return

        mode = new_state.state
        controllers = _controllers(hass)
        for area in config.enabled_areas:
            if not area.event_handlers or not area.has_holiday_scenes:
                continue
            ctrl = controllers.get(area.id)
            if ctrl:
                hass.async_create_task(ctrl.handle_holiday_changed(mode))

    return _handler


# ── Remote handling ────────────────────────────────────────────────────

# Maps Lutron Caseta `button_type` field values to our internal button slugs.
# Reference: homeassistant.components.lutron_caseta emits events with a
# `button_type` of "on"/"off"/"raise"/"lower"/"stop" (Pico scene button).
LUTRON_BUTTON_MAP = {
    "on": "on",
    "off": "off",
    "raise": "raise",
    "lower": "lower",
    "stop": "favorite",
}

# Only react to press events — release and multi_tap would double-fire.
LUTRON_ACTION_PRESS = "press"


def _make_remote_handler(hass: HomeAssistant, config: AreaLightingConfig):
    """Handle Lutron Caseta button events."""

    # Build a lookup: device_id → (area_id, remote_config)
    device_map: dict[str, list[tuple[str, Any]]] = {}
    for area in config.enabled_areas:
        if not area.event_handlers:
            continue
        for remote in area.lutron_remotes:
            device_map.setdefault(remote.id, []).append((area.id, remote))

    @callback
    def _handler(event: Event) -> None:
        # Only react to 'press' — not 'release' or 'multi_tap' — otherwise
        # each button invocation fires twice.
        if event.data.get("action") != LUTRON_ACTION_PRESS:
            return

        device_id = event.data.get("device_id", "")
        # The button subtype is in `button_type`, NOT `action`. This was
        # the long-standing bug: we were reading `action` as the subtype.
        button_type = event.data.get("button_type", "")
        button_slug = LUTRON_BUTTON_MAP.get(button_type)
        if not button_slug:
            _LOGGER.debug("Unknown Lutron button_type: %s", button_type)
            return

        matches = device_map.get(device_id, [])
        if not matches:
            return

        controllers = _controllers(hass)
        for area_id, remote in matches:
            ctrl = controllers.get(area_id)
            if not ctrl:
                continue

            method = {
                "on": ctrl.lighting_on,
                "off": ctrl.lighting_off,
                "favorite": ctrl.lighting_favorite,
                "raise": ctrl.lighting_raise,
                "lower": ctrl.lighting_lower,
            }.get(button_slug)

            if method:
                hass.async_create_task(method())

            # Handle additional actions
            for svc_call in remote.additional_actions.get(button_slug, []):
                service = svc_call.get("service", "")
                data = svc_call.get("data", {})
                target = svc_call.get("target", {})
                if service:
                    domain, svc = service.split(".", 1)
                    call_data = {**data, **target}
                    hass.async_create_task(hass.services.async_call(domain, svc, call_data))

    return _handler
