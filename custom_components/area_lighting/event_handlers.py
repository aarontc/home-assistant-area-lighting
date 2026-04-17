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
    MANUAL_DETECTION_GRACE_SECONDS,
)
from .controller import AreaLightingController
from .models import AreaLightingConfig
from .motion_condition import evaluate_motion_condition

_LOGGER = logging.getLogger(__name__)


def _format_motion_condition(cond) -> str:
    """Produce a short human-readable description of a motion condition
    for log lines. Reads only the fields MotionLightCondition exposes.
    """
    parts: list[str] = []
    if cond.entity_ids:
        parts.append(f"entity_ids={list(cond.entity_ids)}")
    elif cond.entity_id:
        parts.append(f"entity_id={cond.entity_id}")
    if cond.aggregate:
        parts.append(f"aggregate={cond.aggregate}")
    if cond.attribute:
        parts.append(f"attribute={cond.attribute}")
    if cond.state is not None:
        parts.append(f"state={cond.state}")
    if cond.above is not None:
        parts.append(f"above={cond.above}")
    if cond.below is not None:
        parts.append(f"below={cond.below}")
    return " ".join(parts) if parts else "(empty)"


def _controllers(hass: HomeAssistant) -> dict[str, AreaLightingController]:
    return hass.data[DOMAIN]["controllers"]  # type: ignore[no-any-return]


def _config(hass: HomeAssistant) -> AreaLightingConfig:
    return hass.data[DOMAIN]["config"]  # type: ignore[no-any-return]


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
                    _LOGGER.debug(
                        "Area %s: scene_activated entity=%s slug=%s",
                        area_id,
                        entity_id,
                        scene_slug,
                    )
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

    Compares incoming HA state updates against the active scene's
    target states. Only fires a manual transition when a light's
    attributes diverge from what the scene instructed — late Hue
    bridge reports that converge toward the target are not overrides.

    A short grace window after scene transitions still protects
    against intermediate states (e.g., a Hue bulb briefly reporting
    old brightness before reaching the new target).
    """
    import time as _time

    area_id = ctrl.area.id

    def _skip(reason: str, entity_id: str) -> None:
        _LOGGER.debug(
            "Area %s: manual detection skipped reason=%s entity=%s",
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

        if ctrl._state.is_off:
            _skip("area state is off", entity_id)
            return

        if ctrl._state.is_circadian:
            _skip("area state is circadian", entity_id)
            return

        if ctrl._alert_active:
            _skip("alert pattern active", entity_id)
            return

        # Grace period: ignore all events in the window immediately
        # after a scene transition (covers intermediate Hue states
        # like old-brightness-before-new-target).
        last_change = ctrl._state.last_scene_change_monotonic
        if last_change is not None:
            age = _time.monotonic() - last_change
            if age < MANUAL_DETECTION_GRACE_SECONDS:
                _skip(
                    f"within grace window ({age:.1f}s < {MANUAL_DETECTION_GRACE_SECONDS}s)",
                    entity_id,
                )
                return

        # Compare against scene targets: if the light's current state
        # matches what the scene instructed, this is a late bridge
        # report or convergence, not a manual override.
        if ctrl.state_matches_scene_target(entity_id, new_state):
            _skip("matches scene target", entity_id)
            return

        _LOGGER.info(
            "Area %s: manual detection fired entity=%s",
            area_id,
            entity_id,
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
            passed = evaluate_motion_condition(cond, hass.states.get)
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Area %s: motion_condition %s → %s",
                    ctrl.area.id,
                    _format_motion_condition(cond),
                    "pass" if passed else "fail",
                )
            if not passed:
                return False

        return True

    @callback
    def _handler(event: Event) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if not new_state or not old_state:
            return

        entity_id = new_state.entity_id
        _LOGGER.debug(
            "Area %s: motion sensor %s %s→%s",
            ctrl.area.id,
            entity_id,
            old_state.state,
            new_state.state,
        )

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

        entity_id = new_state.entity_id
        _LOGGER.debug(
            "Area %s: occupancy sensor %s %s→%s",
            ctrl.area.id,
            entity_id,
            old_state.state,
            new_state.state,
        )

        if new_state.state == STATE_ON and old_state.state != STATE_ON:
            ctrl.hass.async_create_task(ctrl.handle_occupancy_on())
        elif new_state.state != STATE_ON and old_state.state == STATE_ON:
            # Same unavailable/unknown tolerance as the motion handler:
            # "no sensor is reporting on" is the trigger.
            any_on = any(
                (s := ctrl.hass.states.get(sid)) is not None and s.state == STATE_ON
                for sid in (ctrl.area.occupancy_light_sensor_ids or [])
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
        _LOGGER.debug(
            "ambient_zone %s %s→%s",
            zone,
            old_state.state,
            new_state.state,
        )

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
        _LOGGER.debug("holiday_mode changed to %s", mode)
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
        button_type = event.data.get("button_type", "")
        button_slug = LUTRON_BUTTON_MAP.get(button_type)

        matches = device_map.get(device_id, [])

        if not button_slug:
            # Unknown button types are rare and worth surfacing at INFO
            # (visible without enabling DEBUG). Include area/remote for
            # context when the device is one of ours.
            if matches:
                for area_id, remote in matches:
                    _LOGGER.info(
                        "Area %s: unknown Lutron button_type=%s remote=%s device=%s",
                        area_id,
                        button_type,
                        remote.name,
                        device_id,
                    )
            else:
                _LOGGER.info(
                    "Unknown Lutron button_type=%s from unregistered device=%s",
                    button_type,
                    device_id,
                )
            return

        if not matches:
            return

        controllers = _controllers(hass)
        for area_id, remote in matches:
            _LOGGER.debug(
                "Area %s: remote button=%s remote=%s device=%s",
                area_id,
                button_slug,
                remote.name,
                device_id,
            )
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
                    _LOGGER.debug(
                        "Area %s: remote additional_action service=%s data=%s target=%s",
                        area_id,
                        service,
                        data,
                        target,
                    )
                    domain, svc = service.split(".", 1)
                    call_data = {**data, **target}
                    hass.async_create_task(hass.services.async_call(domain, svc, call_data))

    return _handler
