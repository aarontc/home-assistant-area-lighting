"""Area Lighting Controller - per-area state machine and action methods."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .area_state import (
    ActivationSource,
    AreaState,
)
from .const import (
    AMBIENT_ZONE_ENTITY_PREFIX,
    AMBIENT_ZONE_ENTITY_SUFFIX,
    BRIGHTNESS_STEP_DEFAULT,
    CIRCADIAN_DAYLIGHT_ENABLED_ENTITY,
    DOMAIN,
    HOLIDAY_MODE_ENTITY,
    HOLIDAY_MODE_NONE,
    HOLIDAY_SCENES,
    SCENE_CIRCADIAN,
    SCENE_OFF_INTERNAL,
)
from .models import AreaConfig, AreaLightingConfig, SceneConfig
from .scene_machine import (
    ActionType,
    SceneAction,
    determine_favorite_action,
    determine_off_action,
    determine_off_fade_action,
    determine_on_action,
    resolve_sun_position,
    resolve_sun_position_inverted,
)
from .timer_manager import TimerHandle, parse_duration_to_seconds

_LOGGER = logging.getLogger(__name__)


class AreaLightingController:
    """State machine and action handler for a single area."""

    def __init__(
        self,
        hass: HomeAssistant,
        area: AreaConfig,
        global_config: AreaLightingConfig,
    ) -> None:
        self.hass = hass
        self.area = area
        self._global_config = global_config

        # First-class state machine
        self._state = AreaState()

        # User-toggle state (orthogonal to lighting state machine)
        self._motion_light_enabled: bool = False
        self._ambience_enabled: bool = True
        self._night_mode: bool = False
        self._motion_override_ambient: bool = True
        self._occupancy_timeout_enabled: bool = True
        self._alert_active: bool = False
        self._state_was_persisted: bool = False
        # Fadeout durations, split (T19):
        # - manual_fadeout_seconds: used by lighting_off (remote off
        #   button, off-scene activation)
        # - motion_fadeout_seconds: used by motion timer expiry AND
        #   occupancy timer expiry (shared)
        self._manual_fadeout_seconds: float = 1.5
        self._motion_fadeout_seconds: float = 60.0
        # Most recent motion sensor event kind: "motion_on" | "motion_off" | None
        # Exposed in diagnostic_snapshot so users can see at a glance
        # whether the event handler is firing on their motion events.
        self._last_motion_event: str | None = None

        # Timer durations — user-tunable via number entities. Initialized
        # from the area config at construction; the user can change them
        # later via number.{area.id}_{motion,occupancy}[_night]_timeout_minutes
        # and the new value takes effect on the next timer start().
        self._motion_off_duration_seconds: float = parse_duration_to_seconds(
            area.motion_light_timer_durations.get("off", "00:08:00")
        )
        self._motion_night_off_duration_seconds: float = parse_duration_to_seconds(
            area.motion_light_timer_durations.get("night_off", "00:05:00")
        )
        self._occupancy_off_duration_seconds: float = parse_duration_to_seconds(
            area.occupancy_light_timer_durations.get("off", "00:30:00")
        )
        # Night occupancy: fall back to normal occupancy if not configured.
        night_occ_raw = area.occupancy_light_timer_durations.get("night_off")
        self._occupancy_night_off_duration_seconds: float = (
            parse_duration_to_seconds(night_occ_raw)
            if night_occ_raw
            else self._occupancy_off_duration_seconds
        )

        self._motion_timer = TimerHandle(
            hass,
            f"{area.id}_motion_off",
            self._motion_off_duration_seconds,
            self._on_motion_timer,
        )
        self._motion_night_timer = TimerHandle(
            hass,
            f"{area.id}_motion_night_off",
            self._motion_night_off_duration_seconds,
            self._on_motion_timer,
        )
        self._occupancy_timer = TimerHandle(
            hass,
            f"{area.id}_occupancy_off",
            self._occupancy_off_duration_seconds,
            self._on_occupancy_timer,
        )

        # Linked motion state: tracks which remote scenes were activated
        # so cleanup knows what to restore. Maps remote_area_id → scene_slug.
        self._linked_activated_scenes: dict[str, str] = {}

        # Callbacks for entity state updates
        self._state_listeners: list = []

        # Resolved per-light target states for the active scene.
        # Populated by _activate_scene; used by manual detection to compare
        # incoming HA state updates against what the scene instructed.
        self._active_scene_targets: dict[str, dict] = {}

        # Seed the manual-detection grace clock so the first 10s after
        # controller construction (including post-restart) are protected
        # from spurious manual detection (D15).
        self._state.last_scene_change_monotonic = time.monotonic()

        # Deadlines to be restored by restore_timers() after HA startup (D4).
        self._pending_timer_restore: dict[str, str | None] = {}

        # Leader/follower wiring (populated by __init__.py after every
        # controller exists). Leaderless areas keep `leader = None`;
        # leaders without followers keep `followers == []`.
        self.leader: AreaLightingController | None = None
        self.followers: list[AreaLightingController] = []

    # ── Persistence ────────────────────────────────────────────────────

    def load_persisted_state(self, data: dict) -> None:
        """Restore state from a persistence dict."""
        if not data:
            return
        if "area_state" in data:
            self._state = AreaState.from_dict(data["area_state"])
            self._state_was_persisted = True
        if "motion_light_enabled" in data:
            self._motion_light_enabled = bool(data["motion_light_enabled"])
        if "ambience_enabled" in data:
            self._ambience_enabled = bool(data["ambience_enabled"])
        if "night_mode" in data:
            self._night_mode = bool(data["night_mode"])
        # Read the new key first; fall back to the legacy "override_ambient"
        # key so existing persisted state migrates cleanly.
        if "motion_override_ambient" in data:
            self._motion_override_ambient = bool(data["motion_override_ambient"])
        elif "override_ambient" in data:
            self._motion_override_ambient = bool(data["override_ambient"])
        if "occupancy_timeout_enabled" in data:
            self._occupancy_timeout_enabled = bool(data["occupancy_timeout_enabled"])
        # Fadeout: prefer new split keys; fall back to the legacy single
        # key (which was used for motion timer expiry, so migrate to the
        # motion_fadeout value).
        if "manual_fadeout_seconds" in data:
            self._manual_fadeout_seconds = float(data["manual_fadeout_seconds"])
        if "motion_fadeout_seconds" in data:
            self._motion_fadeout_seconds = float(data["motion_fadeout_seconds"])
        elif "fadeout_seconds" in data:
            self._motion_fadeout_seconds = float(data["fadeout_seconds"])
        # User-tunable timer durations (new in T18)
        if "motion_off_duration_seconds" in data:
            self._motion_off_duration_seconds = float(data["motion_off_duration_seconds"])
        if "motion_night_off_duration_seconds" in data:
            self._motion_night_off_duration_seconds = float(
                data["motion_night_off_duration_seconds"]
            )
        if "occupancy_off_duration_seconds" in data:
            self._occupancy_off_duration_seconds = float(data["occupancy_off_duration_seconds"])
        if "occupancy_night_off_duration_seconds" in data:
            self._occupancy_night_off_duration_seconds = float(
                data["occupancy_night_off_duration_seconds"]
            )
        if "timer_deadlines" in data:
            self._pending_timer_restore = data["timer_deadlines"] or {}
        # Re-seed the monotonic grace clock on every load (D15)
        self._state.last_scene_change_monotonic = time.monotonic()
        _LOGGER.debug("Area %s: restored state %s", self.area.id, data)

    def state_dict(self) -> dict:
        """Return current persistent state as a dict."""
        return {
            "area_state": self._state.to_dict(),
            "motion_light_enabled": self._motion_light_enabled,
            "ambience_enabled": self._ambience_enabled,
            "night_mode": self._night_mode,
            "motion_override_ambient": self._motion_override_ambient,
            "occupancy_timeout_enabled": self._occupancy_timeout_enabled,
            "manual_fadeout_seconds": self._manual_fadeout_seconds,
            "motion_fadeout_seconds": self._motion_fadeout_seconds,
            "motion_off_duration_seconds": self._motion_off_duration_seconds,
            "motion_night_off_duration_seconds": self._motion_night_off_duration_seconds,
            "occupancy_off_duration_seconds": self._occupancy_off_duration_seconds,
            "occupancy_night_off_duration_seconds": self._occupancy_night_off_duration_seconds,
            "timer_deadlines": {
                "motion_off": (
                    self._motion_timer.deadline_utc.isoformat()
                    if self._motion_timer.deadline_utc
                    else None
                ),
                "motion_night_off": (
                    self._motion_night_timer.deadline_utc.isoformat()
                    if self._motion_night_timer.deadline_utc
                    else None
                ),
                "occupancy_off": (
                    self._occupancy_timer.deadline_utc.isoformat()
                    if self._occupancy_timer.deadline_utc
                    else None
                ),
            },
        }

    async def restore_timers(self) -> None:
        """Re-arm or fire timers based on persisted deadlines (D4).

        Called once during _on_started after HA and its integrations
        are fully loaded. Past-due timers fire immediately through
        their normal callbacks (lighting_off_fade path).

        After restoring persisted timers, enforces the occupancy
        invariant so areas that are active on startup get a timer
        even if one wasn't running before shutdown.
        """
        if self._pending_timer_restore:
            mapping = {
                "motion_off": self._motion_timer,
                "motion_night_off": self._motion_night_timer,
                "occupancy_off": self._occupancy_timer,
            }
            for key, timer in mapping.items():
                iso = self._pending_timer_restore.get(key)
                if not iso:
                    continue
                deadline = dt_util.parse_datetime(iso)
                if deadline is None:
                    _LOGGER.warning(
                        "Area %s: could not parse timer deadline %r for %s",
                        self.area.id,
                        iso,
                        key,
                    )
                    continue
                timer.restore(deadline)
            self._pending_timer_restore = {}
        self._enforce_occupancy_timer()

    def reconcile_startup_state(self) -> None:
        """Reconcile persisted state with actual light state on startup.

        If persisted state is OFF but any physical light is on, transition
        to MANUAL so the tracked state matches reality.  Only runs when
        state was actually loaded from persistence (not on first-ever
        startup with default OFF).
        """
        if not self._state_was_persisted or not self._state.is_off:
            return
        for light in self.area.lights:
            state = self.hass.states.get(light.id)
            if state is not None and state.state == "on":
                _LOGGER.info(
                    "Area %s: persisted state is OFF but %s is on — transitioning to manual",
                    self.area.id,
                    light.id,
                )
                self._state.transition_to_manual()
                self._notify_state_change()
                return

    @staticmethod
    def _timer_remaining_seconds(timer: TimerHandle) -> float | None:
        """Seconds until the timer fires, or None if inactive."""
        if not timer.is_active or timer.deadline_utc is None:
            return None
        now = dt_util.utcnow()
        remaining = (timer.deadline_utc - now).total_seconds()
        # Round to 1 decimal for readability
        return float(round(max(0.0, remaining), 1))

    def _motion_sensor_states(self) -> dict[str, dict[str, str | None]]:
        """Return current state and last_changed for all motion/occupancy sensors."""
        sensor_ids: list[str] = []
        if self.area.motion_light_motion_sensor_ids:
            sensor_ids.extend(self.area.motion_light_motion_sensor_ids)
        if self.area.occupancy_light_sensor_ids:
            sensor_ids.extend(self.area.occupancy_light_sensor_ids)
        result: dict[str, dict[str, str | None]] = {}
        for entity_id in sensor_ids:
            state_obj = self.hass.states.get(entity_id)
            if state_obj is not None:
                result[entity_id] = {
                    "state": state_obj.state,
                    "last_changed": state_obj.last_changed.isoformat(),
                }
            else:
                result[entity_id] = {"state": None, "last_changed": None}
        return result

    def diagnostic_snapshot(self) -> dict:
        """Return full state including transient flags for diagnostics."""
        return {
            "state": self._state.state.value,
            "scene_slug": self._state.scene_slug,
            "source": self._state.source.value,
            "dimmed": self._state.dimmed,
            "previous_scene": self._state.previous_scene,
            "motion_light_enabled": self._motion_light_enabled,
            "ambience_enabled": self._ambience_enabled,
            "night_mode": self._night_mode,
            "motion_override_ambient": self._motion_override_ambient,
            "occupancy_timeout_enabled": self._occupancy_timeout_enabled,
            "alert_active": self._alert_active,
            "manual_fadeout_seconds": self._manual_fadeout_seconds,
            "motion_fadeout_seconds": self._motion_fadeout_seconds,
            "motion_off_duration_seconds": self._motion_off_duration_seconds,
            "motion_night_off_duration_seconds": self._motion_night_off_duration_seconds,
            "occupancy_off_duration_seconds": self._occupancy_off_duration_seconds,
            "occupancy_night_off_duration_seconds": self._occupancy_night_off_duration_seconds,
            "last_motion_event": self._last_motion_event,
            "motion_timer_active": self._motion_timer.is_active,
            "motion_night_timer_active": self._motion_night_timer.is_active,
            "occupancy_timer_active": self._occupancy_timer.is_active,
            "motion_timer_remaining_seconds": self._timer_remaining_seconds(self._motion_timer),
            "motion_night_timer_remaining_seconds": self._timer_remaining_seconds(
                self._motion_night_timer
            ),
            "occupancy_timer_remaining_seconds": self._timer_remaining_seconds(
                self._occupancy_timer
            ),
            "motion_sensors": self._motion_sensor_states(),
        }

    def _schedule_save(self) -> None:
        """Schedule a state save."""
        storage = self.hass.data.get(DOMAIN, {}).get("state_storage")
        if storage is None:
            return
        self.hass.async_create_task(storage.async_save_area_state(self.area.id, self.state_dict()))

    # ── Backwards-compat properties for entity platforms ───────────────

    @property
    def is_occupied(self) -> bool:
        """True when lights are on due to a human-triggered action.

        Uses active lighting state as a proxy for physical presence:
        someone pressed a button, triggered motion lighting, etc.
        Automated activations (ambient zone, holiday mode) are excluded.
        """
        return self._state.is_on and self._state.source not in (
            ActivationSource.AMBIENCE,
            ActivationSource.HOLIDAY,
        )

    @property
    def current_scene(self) -> str:
        """Best-effort current scene slug for the select entity."""
        if self._state.is_off:
            return "off"
        if self._state.is_manual:
            return "manual"
        return self._state.scene_slug

    @current_scene.setter
    def current_scene(self, value: str) -> None:
        """Allow the select entity to manually set the scene."""
        if value == "off":
            self._state.transition_to_off(ActivationSource.USER)
        elif value == "manual":
            self._state.transition_to_manual()
        elif value == "circadian":
            self._state.transition_to_circadian(ActivationSource.USER)
        else:
            self._state.transition_to_scene(value, ActivationSource.USER)
        self._notify_state_change()

    def current_on_scene_slug(self) -> str | None:
        """Return the concrete on-scene slug the area is showing, or None.

        Returns None when the area is off, in an ambient-like scene
        (ambient/christmas/halloween), or in manual mode. Otherwise returns
        the active scene slug (e.g. "evening", "circadian").

        Consumed by follower areas to decide whether to mirror the leader.
        """
        if self._state.is_off:
            return None
        if self._state.is_manual:
            return None
        if self._state.is_ambient_like:
            return None
        return self._state.scene_slug

    def _resolve_leader_on_slug(self) -> str | None:
        """Return the scene slug the follower should mirror, or None.

        Returns None when:
        - this controller has no leader
        - the leader is off, ambient, or manual
        - the leader's scene slug is not defined on this follower

        The caller (lighting_on) interprets None as "no hint — fall back to
        the area's own default on-scene logic".
        """
        if self.leader is None:
            return None
        leader_slug = self.leader.current_on_scene_slug()
        if leader_slug is None:
            return None
        if leader_slug not in self.area.scene_slugs:
            return None
        return leader_slug

    @property
    def dimmed(self) -> bool:
        return self._state.dimmed

    @property
    def circadian_active(self) -> bool:
        return self._state.is_circadian

    @property
    def motion_light_enabled(self) -> bool:
        return self._motion_light_enabled

    @motion_light_enabled.setter
    def motion_light_enabled(self, value: bool) -> None:
        self._motion_light_enabled = value
        self._notify_state_change()

    @property
    def ambience_enabled(self) -> bool:
        return self._ambience_enabled

    @ambience_enabled.setter
    def ambience_enabled(self, value: bool) -> None:
        self._ambience_enabled = value
        self._notify_state_change()

    async def async_set_ambience_enabled(self, value: bool) -> None:
        """Set ambience_enabled and immediately apply ambient/off transition."""
        was_enabled = self._ambience_enabled
        self._ambience_enabled = value
        self._notify_state_change()
        if value == was_enabled:
            return
        if value:
            if self._is_ambient_zone_enabled():
                await self.handle_ambient_enabled()
        else:
            await self.handle_ambient_disabled()

    async def async_set_occupancy_timeout_enabled(self, enabled: bool) -> None:
        """Set occupancy_timeout_enabled and apply timer side effects.

        On→Off: cancels any running occupancy timer without firing the
        lights-off callback. Off→On: re-arms the timer if the area is
        currently in an on-scene with all occupancy sensors clear
        (via _enforce_occupancy_timer, which encodes those preconditions).
        Idempotent.
        """
        if self._occupancy_timeout_enabled == enabled:
            return
        self._occupancy_timeout_enabled = enabled
        if enabled:
            self._enforce_occupancy_timer()
        else:
            self._occupancy_timer.cancel()
        self._notify_state_change()  # also schedules the persistence save

    @property
    def night_mode(self) -> bool:
        return self._night_mode

    @night_mode.setter
    def night_mode(self, value: bool) -> None:
        self._night_mode = value
        self._notify_state_change()

    @property
    def motion_override_ambient(self) -> bool:
        return self._motion_override_ambient

    @motion_override_ambient.setter
    def motion_override_ambient(self, value: bool) -> None:
        self._motion_override_ambient = value
        self._notify_state_change()

    @property
    def occupancy_timeout_enabled(self) -> bool:
        return self._occupancy_timeout_enabled

    @property
    def manual_fadeout_seconds(self) -> float:
        return self._manual_fadeout_seconds

    @manual_fadeout_seconds.setter
    def manual_fadeout_seconds(self, value: float) -> None:
        self._manual_fadeout_seconds = float(value)
        self._notify_state_change()

    @property
    def motion_fadeout_seconds(self) -> float:
        return self._motion_fadeout_seconds

    @motion_fadeout_seconds.setter
    def motion_fadeout_seconds(self, value: float) -> None:
        self._motion_fadeout_seconds = float(value)
        self._notify_state_change()

    @property
    def motion_off_duration_seconds(self) -> float:
        return self._motion_off_duration_seconds

    @motion_off_duration_seconds.setter
    def motion_off_duration_seconds(self, value: float) -> None:
        self._motion_off_duration_seconds = float(value)
        self._notify_state_change()

    @property
    def motion_night_off_duration_seconds(self) -> float:
        return self._motion_night_off_duration_seconds

    @motion_night_off_duration_seconds.setter
    def motion_night_off_duration_seconds(self, value: float) -> None:
        self._motion_night_off_duration_seconds = float(value)
        self._notify_state_change()

    @property
    def occupancy_off_duration_seconds(self) -> float:
        return self._occupancy_off_duration_seconds

    @occupancy_off_duration_seconds.setter
    def occupancy_off_duration_seconds(self, value: float) -> None:
        self._occupancy_off_duration_seconds = float(value)
        self._notify_state_change()

    @property
    def occupancy_night_off_duration_seconds(self) -> float:
        return self._occupancy_night_off_duration_seconds

    @occupancy_night_off_duration_seconds.setter
    def occupancy_night_off_duration_seconds(self, value: float) -> None:
        self._occupancy_night_off_duration_seconds = float(value)
        self._notify_state_change()

    def add_state_listener(self, callback) -> None:
        self._state_listeners.append(callback)

    def remove_state_listener(self, callback) -> None:
        self._state_listeners = [c for c in self._state_listeners if c is not callback]

    def _notify_state_change(self) -> None:
        self._log_state_snapshot_if_changed()
        for callback in self._state_listeners:
            callback()
        self._schedule_save()

    def _log_state_snapshot_if_changed(self) -> None:
        """Log a single line when primary state/scene/source changes.

        Avoids log spam from setters that don't change lighting state
        (e.g., ambience_enabled toggled to the same value).
        """
        snapshot = (
            self._state.state.value,
            self._state.scene_slug,
            self._state.source.value,
            self._state.dimmed,
        )
        if snapshot == getattr(self, "_last_logged_snapshot", None):
            return
        self._last_logged_snapshot = snapshot
        _LOGGER.debug(
            "Area %s: state state=%s scene=%s source=%s dimmed=%s",
            self.area.id,
            *snapshot,
        )

    # ── HA state helpers ───────────────────────────────────────────────

    def _get_state(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        return state.state if state else "unknown"

    def _get_holiday_mode(self) -> str:
        return self._get_state(HOLIDAY_MODE_ENTITY)

    def _is_ambient_zone_enabled(self) -> bool:
        if not self.area.ambient_lighting_zone:
            return False
        entity = f"{AMBIENT_ZONE_ENTITY_PREFIX}{self.area.ambient_lighting_zone}{AMBIENT_ZONE_ENTITY_SUFFIX}"
        return self._get_state(entity) == "on"

    def _get_ambient_scene_mode(self) -> str:
        return self._get_state("input_select.ambient_scene")

    def _is_daylight_enabled(self) -> bool:
        return self._get_state(CIRCADIAN_DAYLIGHT_ENABLED_ENTITY) == "on"

    # ── Service call helpers ───────────────────────────────────────────

    async def _call_service(self, service: str, **kwargs: Any) -> None:
        domain, svc = service.split(".", 1)
        _LOGGER.debug(
            "Area %s: service_call %s %s",
            self.area.id,
            service,
            self._fmt_service_kwargs(service, kwargs),
        )
        await self.hass.services.async_call(domain, svc, kwargs, blocking=True)

    @staticmethod
    def _fmt_service_kwargs(service: str, kwargs: dict[str, Any]) -> str:
        """Format service kwargs for logging — only the keys that matter.

        Keeps log lines grep-friendly by dropping noise. The selected keys
        are the ones a troubleshooter actually wants to see at a glance.
        """
        relevant_keys = (
            "entity_id",
            "brightness",
            "brightness_step_pct",
            "color_temp_kelvin",
            "color_temp",
            "rgb_color",
            "hs_color",
            "xy_color",
            "effect",
            "transition",
        )
        parts = [f"{k}={kwargs[k]}" for k in relevant_keys if k in kwargs]
        return " ".join(parts) if parts else "(no args)"

    def _propagate_to_followers(
        self,
        new_slug: str | None,
        reason,
    ) -> None:
        """Notify every follower of this leader's state transition.

        Schedules each follower's handle_leader_change as a separate HA
        task so they run asynchronously and independently. The follower
        applies its own Scenario B rules.

        `reason` is a LeaderReason — imported locally inside _activate_scene
        and handle_manual_light_change callers to avoid top-level clutter.
        """
        if not self.followers:
            return
        for follower in self.followers:
            self.hass.async_create_task(follower.handle_leader_change(new_slug, reason))

    async def _activate_scene(
        self,
        scene_slug: str,
        source: ActivationSource = ActivationSource.USER,
        transition: float | None = None,
    ) -> None:
        """Activate a scene by slug, transitioning state with the given source.

        Behavioral scenes (off_internal, circadian) are handled directly.
        Any transition OUT of a circadian state disables the circadian
        switches first so they don't fight the new scene's light settings.
        """
        from .area_state import LeaderReason

        if scene_slug == SCENE_OFF_INTERNAL:
            await self._disable_circadian_switches()
            await self._turn_off_all_lights(transition)
            self._active_scene_targets = {}
            self._state.transition_to_off(source)
            self._enforce_occupancy_timer()
            self._notify_state_change()
            if source != ActivationSource.LEADER:
                self._propagate_to_followers(None, LeaderReason.OFF)
            return

        if scene_slug == SCENE_CIRCADIAN:
            self._active_scene_targets = {}
            await self._activate_circadian(source)
            if source != ActivationSource.LEADER:
                self._propagate_to_followers(
                    SCENE_CIRCADIAN,
                    LeaderReason.SCENE_ACTIVATED,
                )
            return

        # Visual scene → disable circadian first so the switches stop
        # overriding the scene's color/brightness, then apply + transition.
        await self._disable_circadian_switches()
        self._active_scene_targets = self._resolve_scene_targets(scene_slug)
        await self._apply_scene_data(scene_slug, transition)
        self._state.transition_to_scene(scene_slug, source)
        self._enforce_occupancy_timer()
        self._notify_state_change()

        if source != ActivationSource.LEADER:
            if scene_slug in ("ambient", "christmas", "halloween"):
                self._propagate_to_followers(scene_slug, LeaderReason.AMBIENT)
            else:
                self._propagate_to_followers(
                    scene_slug,
                    LeaderReason.SCENE_ACTIVATED,
                )

    async def _activate_circadian(
        self,
        source: ActivationSource = ActivationSource.USER,
    ) -> None:
        """Activate circadian mode."""
        self._state.transition_to_circadian(source)
        self._enforce_occupancy_timer()
        self._notify_state_change()

        await self._enable_circadian_switches()

        tasks: list = []
        for light in self.area.all_lights:
            if not light.circadian_switch:
                continue
            cs = self.area.circadian_switch_for_light(light)
            if not cs:
                continue
            switch_state = self.hass.states.get(cs.entity_id)
            if not switch_state:
                continue
            brightness_pct = switch_state.attributes.get("brightness")
            colortemp_state = self.hass.states.get("sensor.circadian_values")
            colortemp = colortemp_state.attributes.get("colortemp") if colortemp_state else None

            data: dict[str, Any] = {"entity_id": light.id}
            if brightness_pct is not None:
                data["brightness"] = int(float(brightness_pct) * 2.55)
            if colortemp is not None and light.circadian_type == "ct":
                data["color_temp_kelvin"] = int(colortemp)
            tasks.append(self._call_service("light.turn_on", **data))
        if tasks:
            await asyncio.gather(*tasks)

    async def _apply_scene_data(
        self,
        scene_slug: str,
        transition: float | None = None,
    ) -> None:
        """Apply light states for a visual scene from snapshot or config.

        When the area has Hue Zone-style light clusters with members,
        the dispatcher coalesces per-light commands into per-cluster
        commands wherever a cluster's members all share the same target
        state. This reduces per-scene command latency and avoids
        sequential-per-light flicker.
        """
        from .cluster_dispatch import select_dispatch_commands
        from .scene_storage import SceneStorage

        storage: SceneStorage = self.hass.data.get(DOMAIN, {}).get("scene_storage")
        stored = storage.get_scene_data(self.area.id, scene_slug) if storage else None

        entities: dict[str, Any] | None
        if stored:
            entities = stored
        else:
            scene_cfg = self._get_scene_config(scene_slug)
            entities = scene_cfg.entities if scene_cfg and scene_cfg.entities else None

        if entities:
            # Filter to light.* entities only (legacy templater scene
            # files sometimes include input_boolean flags too).
            light_entities = {
                eid: state for eid, state in entities.items() if eid.startswith("light.")
            }
            cluster_specs = [
                (light.id, list(light.members))
                for light in self.area.light_clusters
                if light.is_cluster
            ]
            commands = select_dispatch_commands(light_entities, cluster_specs)
            await asyncio.gather(
                *[
                    self._apply_light_state(entity_id, state_data, transition)
                    for entity_id, state_data in commands
                ]
            )
        else:
            # No snapshot → fall back to role-based on/off, no batching
            tasks: list = []
            for light in self.area.all_lights:
                svc_data: dict[str, Any] = {"entity_id": light.id}
                if transition is not None:
                    svc_data["transition"] = int(transition)
                if light.in_scene(scene_slug):
                    tasks.append(self._call_service("light.turn_on", **svc_data))
                else:
                    tasks.append(self._call_service("light.turn_off", **svc_data))
            if tasks:
                await asyncio.gather(*tasks)

    async def _apply_light_state(
        self,
        entity_id: str,
        state_data: dict,
        transition: float | None = None,
    ) -> None:
        target_state = state_data.get("state", "off")
        svc_data: dict[str, Any] = {"entity_id": entity_id}
        if transition is not None:
            svc_data["transition"] = int(transition)

        if target_state == "on":
            # Skip keys whose value is None — Hue's 2025 deprecation warns
            # when `effect=None` is passed to light.turn_on, and None is
            # never meaningful for any of these attributes anyway.
            for attr in (
                "brightness",
                "color_temp_kelvin",
                "color_temp",
                "hs_color",
                "rgb_color",
                "xy_color",
                "effect",
            ):
                if attr in state_data and state_data[attr] is not None:
                    svc_data[attr] = state_data[attr]
            await self._call_service("light.turn_on", **svc_data)
        else:
            await self._call_service("light.turn_off", **svc_data)

    def _get_scene_config(self, scene_slug: str) -> SceneConfig | None:
        for s in self.area.scenes:
            if s.slug == scene_slug:
                return s
        return None

    def _resolve_scene_targets(self, scene_slug: str) -> dict[str, dict]:
        """Resolve the per-light target states for a scene.

        Uses the same priority as _apply_scene_data: stored snapshot →
        inline config entities → role-based skeleton. Returns a dict
        mapping entity_id → target state dict for every light in the area.
        """
        from .scene_storage import SceneStorage

        storage: SceneStorage = self.hass.data.get(DOMAIN, {}).get("scene_storage")
        stored = storage.get_scene_data(self.area.id, scene_slug) if storage else None

        entities: dict[str, Any] | None
        if stored:
            entities = stored
        else:
            scene_cfg = self._get_scene_config(scene_slug)
            entities = scene_cfg.entities if scene_cfg and scene_cfg.entities else None

        if entities:
            return {eid: state for eid, state in entities.items() if eid.startswith("light.")}

        # Skeleton fallback: role-based on/off, no attribute targets
        return {
            light.id: {"state": "on" if light.in_scene(scene_slug) else "off"}
            for light in self.area.all_lights
        }

    def state_matches_scene_target(self, entity_id: str, ha_state) -> bool:
        """Check whether a light's HA state matches the active scene target.

        Returns True if the light's current attributes are consistent with
        what the scene instructed (within tolerance for Hue bridge jitter).
        Returns False if no target exists for this light or if the values
        diverge, indicating a genuine manual override.
        """
        target = self._active_scene_targets.get(entity_id)
        if target is None:
            return False

        target_on = target.get("state", "off") == "on"
        actual_on = ha_state.state == "on"

        if target_on != actual_on:
            return False

        if not target_on:
            return True  # both off, matches

        # Compare attributes with tolerances for Hue bridge jitter
        attrs = ha_state.attributes

        target_brightness = target.get("brightness")
        if target_brightness is not None:
            actual_brightness = attrs.get("brightness")
            if (
                actual_brightness is not None
                and abs(int(target_brightness) - int(actual_brightness)) > 10
            ):
                return False

        target_ct = target.get("color_temp_kelvin")
        if target_ct is not None:
            actual_ct = attrs.get("color_temp_kelvin")
            if actual_ct is not None and abs(int(target_ct) - int(actual_ct)) > 100:
                return False

        target_hs = target.get("hs_color")
        if target_hs is not None:
            actual_hs = attrs.get("hs_color")
            if actual_hs is not None:
                hue_diff = abs(float(target_hs[0]) - float(actual_hs[0]))
                hue_diff = min(hue_diff, 360 - hue_diff)
                sat_diff = abs(float(target_hs[1]) - float(actual_hs[1]))
                if hue_diff > 10 or sat_diff > 10:
                    return False

        return True

    async def _activate_holiday_scene(
        self,
        source: ActivationSource = ActivationSource.USER,
        transition: float | None = None,
    ) -> None:
        holiday = self._get_holiday_mode()
        if holiday != HOLIDAY_MODE_NONE:
            await self._activate_scene(holiday, source, transition)

    async def _turn_off_all_lights(self, transition: float | None = None) -> None:
        tasks: list = []
        for light in self.area.all_lights:
            data: dict[str, Any] = {"entity_id": light.id}
            if transition is not None:
                data["transition"] = int(transition)
            tasks.append(self._call_service("light.turn_off", **data))
        if tasks:
            await asyncio.gather(*tasks)

    async def _resolve_and_activate(
        self,
        action: SceneAction,
        source: ActivationSource = ActivationSource.USER,
        transition: float | None = None,
    ) -> None:
        if action.action == ActionType.NOOP:
            return
        if action.action == ActionType.ACTIVATE_SCENE:
            if action.scene_slug is not None:
                await self._activate_scene(action.scene_slug, source, transition)
        elif action.action == ActionType.ACTIVATE_HOLIDAY_SCENE:
            await self._activate_holiday_scene(source, transition)
        elif action.action == ActionType.SET_SUN_POSITION:
            slug = resolve_sun_position(self._is_daylight_enabled())
            await self._activate_scene(slug, source, transition)
        elif action.action == ActionType.SET_SUN_POSITION_INVERTED:
            slug = resolve_sun_position_inverted(self._is_daylight_enabled())
            await self._activate_scene(slug, source, transition)

    # ── Circadian helpers ──────────────────────────────────────────────

    async def _enable_circadian_switches(self) -> None:
        if self.area.circadian_switches:
            await asyncio.gather(
                *[
                    self._call_service("switch.turn_on", entity_id=cs.entity_id)
                    for cs in self.area.circadian_switches
                ]
            )

    async def _disable_circadian_switches(self) -> None:
        if self.area.circadian_switches:
            await asyncio.gather(
                *[
                    self._call_service("switch.turn_off", entity_id=cs.entity_id)
                    for cs in self.area.circadian_switches
                ]
            )

    # ── Brightness/dimming helpers (D2, D3) ────────────────────────────

    def _brightness_step_pct(self) -> int:
        """Return the per-area brightness step percentage."""
        return self.area.brightness_step_pct or BRIGHTNESS_STEP_DEFAULT

    def _on_light_entity_ids(self) -> list[str]:
        """Return IDs of lights in this area that are currently 'on'."""
        return [
            light.id
            for light in self.area.all_lights
            if (st := self.hass.states.get(light.id)) and st.state == "on"
        ]

    async def _step_on_lights_pct(self, delta_pct: int) -> None:
        """Apply brightness_step_pct to every currently-on light in the area."""
        entity_ids = self._on_light_entity_ids()
        if entity_ids:
            await asyncio.gather(
                *[
                    self._call_service(
                        "light.turn_on",
                        entity_id=entity_id,
                        brightness_step_pct=delta_pct,
                    )
                    for entity_id in entity_ids
                ]
            )

    async def _scale_on_lights_to_pct(self, pct: int) -> None:
        """Set every currently-on light to an absolute brightness percentage."""
        brightness = max(1, min(255, round(255 * pct / 100)))
        entity_ids = self._on_light_entity_ids()
        if entity_ids:
            await asyncio.gather(
                *[
                    self._call_service(
                        "light.turn_on",
                        entity_id=entity_id,
                        brightness=brightness,
                    )
                    for entity_id in entity_ids
                ]
            )

    # ── Night-mode timer/fade helpers (D6) ─────────────────────────────

    def _motion_off_timer(self) -> TimerHandle:
        """Return the motion-off timer appropriate to current night mode."""
        return self._motion_night_timer if self._night_mode else self._motion_timer

    def _occupancy_off_duration(self) -> float:
        """Return the occupancy timeout duration appropriate to night mode."""
        if self._night_mode:
            return self._occupancy_night_off_duration_seconds
        return self._occupancy_off_duration_seconds

    def _motion_fade_seconds(self) -> float:
        """Fadeout for motion/occupancy timer expiry (per D6 night override)."""
        if self._night_mode and self.area.night_fadeout_seconds is not None:
            return self.area.night_fadeout_seconds
        return self._motion_fadeout_seconds

    def _manual_fade_seconds(self) -> float:
        """Fadeout for explicit off (remote off, off scene)."""
        return self._manual_fadeout_seconds

    # ── Main action methods ───────────────────────────────────────────

    async def lighting_on(
        self,
        source: ActivationSource = ActivationSource.USER,
    ) -> None:
        """Handle 'on' action with scene cycling logic."""
        _LOGGER.debug(
            "Area %s: lighting_on source=%s current_scene=%s dimmed=%s",
            self.area.id,
            source.value,
            self._state.scene_slug,
            self._state.dimmed,
        )

        # Scenario A: if we're transitioning from off/ambient → on and
        # have a leader with a concrete on-scene we also define, mirror
        # the leader instead of running our own cycle logic.
        if self._state.is_off or self._state.is_ambient_like:
            hint = self._resolve_leader_on_slug()
            if hint is not None:
                _LOGGER.debug(
                    "Area %s: mirroring leader %s scene %s",
                    self.area.id,
                    self.leader.area.id if self.leader else "?",
                    hint,
                )
                await self._activate_scene(hint, source)
                return

        from_motion = source == ActivationSource.MOTION

        action = determine_on_action(
            current_scene=self._state.scene_slug,
            scene_slugs=self.area.scene_slugs,
            dimmed=self._state.dimmed,
            triggered_by_motion=from_motion,
            motion_override_ambient=self._motion_override_ambient,
            holiday_mode=self._get_holiday_mode(),
            night_mode=self._night_mode,
        )
        _LOGGER.debug(
            "Area %s: on_decision action=%s target_scene=%s",
            self.area.id,
            action.action.name,
            getattr(action, "scene_slug", None),
        )

        if action.action == ActionType.NOOP:
            return

        # If restoring from dimmed, restore the previous scene
        if self._state.dimmed:
            previous = self._state.clear_dimmed()
            if previous and previous in self.area.scene_slugs:
                await self._activate_scene(previous, source)
                return
            # Fall through to default cycling if no valid previous

        await self._resolve_and_activate(action, source)

    async def lighting_off(
        self,
        source: ActivationSource = ActivationSource.USER,
    ) -> None:
        """Handle 'off' action (remote off button, off scene activation).

        Applies the manual fade duration so the user gets a graceful
        turn-off rather than an abrupt snap. Distinct from
        lighting_off_fade which uses the motion fade.
        """
        _LOGGER.debug(
            "Area %s: lighting_off source=%s current_scene=%s",
            self.area.id,
            source.value,
            self._state.scene_slug,
        )
        action = determine_off_action(
            current_scene=self._state.scene_slug,
            source=self._state.source.value,
            ambient_zone_enabled=self._is_ambient_zone_enabled(),
            area_ambience_enabled=self._ambience_enabled,
            holiday_mode=self._get_holiday_mode(),
            ambient_scene_mode=self._get_ambient_scene_mode(),
        )
        _LOGGER.debug(
            "Area %s: off_decision action=%s target_scene=%s",
            self.area.id,
            action.action.name,
            getattr(action, "scene_slug", None),
        )
        ambient_fallback = (
            action.action == ActionType.ACTIVATE_SCENE and action.scene_slug == "ambient"
        ) or action.action == ActionType.ACTIVATE_HOLIDAY_SCENE
        effective_source = ActivationSource.AMBIENCE if ambient_fallback else source
        await self._resolve_and_activate(
            action,
            effective_source,
            transition=self._manual_fade_seconds(),
        )

    async def lighting_off_fade(
        self,
        source: ActivationSource = ActivationSource.USER,
    ) -> None:
        """Handle fading off (motion/occupancy timer)."""
        _LOGGER.debug(
            "Area %s: lighting_off_fade source=%s current_scene=%s fade=%.1fs",
            self.area.id,
            source.value,
            self._state.scene_slug,
            self._motion_fade_seconds(),
        )
        await self._disable_circadian_switches()

        action = determine_off_fade_action(
            current_scene=self._state.scene_slug,
            source=self._state.source.value,
            ambient_zone_enabled=self._is_ambient_zone_enabled(),
            area_ambience_enabled=self._ambience_enabled,
            holiday_mode=self._get_holiday_mode(),
            ambient_scene_mode=self._get_ambient_scene_mode(),
        )
        _LOGGER.debug(
            "Area %s: off_fade_decision action=%s target_scene=%s",
            self.area.id,
            action.action.name,
            getattr(action, "scene_slug", None),
        )
        ambient_fallback = (
            action.action == ActionType.ACTIVATE_SCENE and action.scene_slug == "ambient"
        ) or action.action == ActionType.ACTIVATE_HOLIDAY_SCENE
        effective_source = ActivationSource.AMBIENCE if ambient_fallback else source
        await self._resolve_and_activate(
            action,
            effective_source,
            transition=self._motion_fade_seconds(),
        )

    async def lighting_force_off(
        self,
        source: ActivationSource = ActivationSource.USER,
    ) -> None:
        """Force off, bypassing ambient/holiday fallback logic.

        Used by the global 'lights out' workflow: caller has already
        decided the area must go dark regardless of ambience configuration,
        per-area ambience_enabled, holiday mode, or ambient_scene_mode.
        """
        _LOGGER.debug(
            "Area %s: lighting_force_off source=%s current_scene=%s",
            self.area.id,
            source.value,
            self._state.scene_slug,
        )
        await self._activate_scene(SCENE_OFF_INTERNAL, source)

    async def lighting_favorite(
        self,
        source: ActivationSource = ActivationSource.USER,
        favorite_cycle: list[str] | None = None,
    ) -> None:
        """Handle 'favorite' action.

        When *favorite_cycle* is provided it overrides the default
        holiday/night cycling.  A single ``scene.`` entry calls
        ``scene.turn_on``; bare slugs cycle through the list on
        repeated presses.
        """
        _LOGGER.debug(
            "Area %s: lighting_favorite source=%s current_scene=%s cycle=%s",
            self.area.id,
            source.value,
            self._state.scene_slug,
            favorite_cycle,
        )

        if favorite_cycle:
            if len(favorite_cycle) == 1 and favorite_cycle[0].startswith("scene."):
                entity_id = favorite_cycle[0]
                _LOGGER.debug(
                    "Area %s: favorite override → scene.turn_on %s",
                    self.area.id,
                    entity_id,
                )
                await self._call_service("scene.turn_on", entity_id=entity_id)
                return

            try:
                idx = favorite_cycle.index(self._state.scene_slug)
                target = favorite_cycle[(idx + 1) % len(favorite_cycle)]
            except ValueError:
                target = favorite_cycle[0]

            _LOGGER.debug(
                "Area %s: favorite override → activate %s",
                self.area.id,
                target,
            )
            await self._activate_scene(target, source)
            return

        action = determine_favorite_action(
            current_scene=self._state.scene_slug,
            scene_slugs=self.area.scene_slugs,
            holiday_mode=self._get_holiday_mode(),
        )
        _LOGGER.debug(
            "Area %s: favorite_decision action=%s target_scene=%s",
            self.area.id,
            action.action.name,
            getattr(action, "scene_slug", None),
        )
        await self._resolve_and_activate(action, source)

    async def lighting_circadian(
        self,
        source: ActivationSource = ActivationSource.USER,
    ) -> None:
        """Activate circadian mode (public method, called by service)."""
        _LOGGER.debug(
            "Area %s: lighting_circadian source=%s",
            self.area.id,
            source.value,
        )
        await self._activate_circadian(source)

    async def lighting_raise(self) -> None:
        """Raise brightness of currently-on lights (D2)."""
        _LOGGER.debug(
            "Area %s: lighting_raise current_scene=%s dimmed=%s",
            self.area.id,
            self._state.scene_slug,
            self._state.dimmed,
        )
        step = self._brightness_step_pct()

        if self._state.is_off:
            # From off: restore the remembered previous scene (or default),
            # then scale those lights to the step percentage.
            target_scene = self._state.previous_scene
            if not target_scene or target_scene not in self.area.scene_slugs:
                await self.lighting_on()
                target_scene = self._state.scene_slug
            else:
                await self._activate_scene(target_scene, ActivationSource.USER)
            await self._scale_on_lights_to_pct(step)
            self._state.mark_dimmed()
            self._notify_state_change()
            return

        if self._state.is_circadian:
            await self._disable_circadian_switches()

        await self._step_on_lights_pct(+step)

        if not self._state.is_manual:
            self._state.mark_dimmed()
            self._notify_state_change()

    async def lighting_lower(self) -> None:
        """Lower brightness of currently-on lights (D2)."""
        _LOGGER.debug(
            "Area %s: lighting_lower current_scene=%s dimmed=%s",
            self.area.id,
            self._state.scene_slug,
            self._state.dimmed,
        )
        if self._state.is_off:
            return  # explicit no-op per README §"Remote `lower`" item 1

        step = self._brightness_step_pct()

        if self._state.is_circadian:
            await self._disable_circadian_switches()

        await self._step_on_lights_pct(-step)

        if not self._state.is_manual:
            self._state.mark_dimmed()
            self._notify_state_change()

    # ── Event handlers ────────────────────────────────────────────────

    async def handle_scene_activated(self, scene_slug: str) -> None:
        """External scene.turn_on detected. Track which scene is now active.

        Any transition away from circadian disables the circadian switches
        so they don't keep fighting the externally-activated scene.
        """
        _LOGGER.debug(
            "Area %s: handle_scene_activated scene=%s",
            self.area.id,
            scene_slug,
        )
        if scene_slug == SCENE_OFF_INTERNAL:
            return
        # External activation defaults to USER source
        if scene_slug == "circadian":
            self._active_scene_targets = {}
            self._state.transition_to_circadian(ActivationSource.USER)
        elif scene_slug == "off":
            self._active_scene_targets = {}
            await self._disable_circadian_switches()
            self._state.transition_to_off(ActivationSource.USER)
        else:
            await self._disable_circadian_switches()
            self._active_scene_targets = self._resolve_scene_targets(scene_slug)
            self._state.transition_to_scene(scene_slug, ActivationSource.USER)
        self._notify_state_change()

    async def handle_lights_all_off(self) -> None:
        """All lights in area turned off externally.

        Cancels any running timers so they can't fire into an already-off
        area (README §4 "All lights externally turned off" bullet 3).
        """
        _LOGGER.debug("Area %s: handle_lights_all_off", self.area.id)
        self._active_scene_targets = {}
        self._state.transition_to_off(ActivationSource.USER)
        await self._disable_circadian_switches()
        self._motion_timer.cancel()
        self._motion_night_timer.cancel()
        self._occupancy_timer.cancel()
        self._notify_state_change()

    async def handle_manual_light_change(self) -> None:
        """A light was manually adjusted outside the scene system.

        Disables circadian switches so they don't immediately overwrite
        the user's manual change with a circadian-calculated one.
        """
        _LOGGER.debug(
            "Area %s: handle_manual_light_change current_scene=%s dimmed=%s",
            self.area.id,
            self._state.scene_slug,
            self._state.dimmed,
        )
        if not self._state.dimmed:
            await self._disable_circadian_switches()
            self._state.transition_to_manual()
            self._enforce_occupancy_timer()
            self._notify_state_change()
            from .area_state import LeaderReason

            self._propagate_to_followers(None, LeaderReason.MANUAL)

    async def handle_motion_on(self) -> None:
        _LOGGER.debug(
            "Area %s: handle_motion_on current_scene=%s source=%s",
            self.area.id,
            self._state.scene_slug,
            self._state.source.value,
        )
        self._last_motion_event = "motion_on"
        self._motion_timer.cancel()
        self._motion_night_timer.cancel()

        if self.area.linked_motion:
            local_scene, remote_activations = self._resolve_linked_motion()
            await self._activate_scene(local_scene, ActivationSource.MOTION)
            await self._activate_linked_areas(remote_activations)
        else:
            await self.lighting_on(source=ActivationSource.MOTION)

        self._notify_state_change()

    async def handle_motion_off(self) -> None:
        """Start the motion-off timer appropriate to night mode (D6).

        Reads the CURRENT duration from the controller's mutable
        _motion_off_duration_seconds / _motion_night_off_duration_seconds
        fields (not the TimerHandle's construction-time default), so
        user edits to the corresponding number entity take effect on
        the next motion event.

        Only starts the timer if motion actually owns the current state.
        Without this guard, a sensor shared between motion and occupancy
        would unconditionally start the motion timer on any off event —
        even when motion lighting is disabled or the area is in a non-motion
        state (e.g. manual, user-activated scene).
        """
        _LOGGER.debug(
            "Area %s: handle_motion_off state_source=%s night_mode=%s",
            self.area.id,
            self._state.source.value,
            self._night_mode,
        )
        self._last_motion_event = "motion_off"
        if self._state.source != ActivationSource.MOTION:
            return
        if self._night_mode:
            self._motion_timer.cancel()
            self._motion_night_timer.start(
                duration=self._motion_night_off_duration_seconds,
            )
        else:
            self._motion_night_timer.cancel()
            self._motion_timer.start(
                duration=self._motion_off_duration_seconds,
            )
        self._notify_state_change()

    def _resolve_linked_motion(self) -> tuple[str, list[tuple[str, str]]]:
        """Resolve linked_motion mappings based on remote area states.

        Returns:
            (local_scene, [(remote_area_id, remote_scene), ...])
            where remote_scene entries with None are excluded.
        """
        if not self.area.linked_motion:
            return "circadian", []

        controllers = self.hass.data.get(DOMAIN, {}).get("controllers", {})
        local_scene = "circadian"
        remote_activations: list[tuple[str, str]] = []

        for link in self.area.linked_motion:
            remote_ctrl = controllers.get(link.remote_area)
            if remote_ctrl is None:
                _LOGGER.warning(
                    "Area %s: linked_motion references unknown area %s",
                    self.area.id,
                    link.remote_area,
                )
                continue

            remote_scene = remote_ctrl.current_scene
            mapping = link.resolve(remote_scene)
            local_scene = mapping.local_scene
            if mapping.remote_scene is not None:
                remote_activations.append((link.remote_area, mapping.remote_scene))

        return local_scene, remote_activations

    async def _activate_linked_areas(self, remote_activations: list[tuple[str, str]]) -> None:
        """Activate scenes in remote areas via their controllers."""
        controllers = self.hass.data.get(DOMAIN, {}).get("controllers", {})
        self._linked_activated_scenes.clear()

        for remote_area_id, remote_scene in remote_activations:
            remote_ctrl = controllers.get(remote_area_id)
            if remote_ctrl is None:
                continue
            await remote_ctrl._activate_scene(remote_scene, ActivationSource.LINKED)
            self._linked_activated_scenes[remote_area_id] = remote_scene

    async def _cleanup_linked_areas(self) -> None:
        """Deactivate remote areas that are still in the linked scene."""
        if not self._linked_activated_scenes:
            return

        controllers = self.hass.data.get(DOMAIN, {}).get("controllers", {})

        for remote_area_id, activated_scene in self._linked_activated_scenes.items():
            remote_ctrl = controllers.get(remote_area_id)
            if remote_ctrl is None:
                continue
            if remote_ctrl.current_scene == activated_scene:
                await remote_ctrl._activate_scene("off_internal", ActivationSource.LINKED)

        self._linked_activated_scenes.clear()

    def _enforce_occupancy_timer(self) -> None:
        """Enforce occupancy timer invariant.

        The timer runs whenever the area is active (not off/ambient) and
        no occupancy sensor is currently on. Only starts the timer if
        it's not already running — scene-to-scene transitions don't
        reset the countdown. Called after every scene transition.
        """
        if self._state.is_off or self._state.is_ambient_like:
            self._occupancy_timer.cancel()
            return
        if not self.area.has_occupancy_lighting:
            return
        if self._occupancy_timer.is_active:
            return  # Already running — don't reset on scene changes
        any_sensor_on = any(
            (s := self.hass.states.get(sid)) is not None and s.state == "on"
            for sid in (self.area.occupancy_light_sensor_ids or [])
        )
        if not any_sensor_on:
            self._start_occupancy_timer()

    def _start_occupancy_timer(self) -> None:
        """Arm the occupancy timer, respecting the enable flag.

        Single choke-point for every start so the `occupancy_timeout_enabled`
        gate lives in one place. Cancels remain independent of the flag.
        """
        if not self._occupancy_timeout_enabled:
            _LOGGER.debug(
                "Area %s: occupancy timer start suppressed (timeout disabled)",
                self.area.id,
            )
            return
        self._occupancy_timer.start(duration=self._occupancy_off_duration())

    async def handle_occupancy_on(self) -> None:
        _LOGGER.debug("Area %s: handle_occupancy_on", self.area.id)
        self._occupancy_timer.cancel()
        self._notify_state_change()

    async def handle_occupancy_off(self) -> None:
        _LOGGER.debug(
            "Area %s: handle_occupancy_off current_scene=%s",
            self.area.id,
            self._state.scene_slug,
        )
        if self._state.is_off or self._state.is_ambient_like:
            return
        # Restart timer with full duration (sensor cleared, countdown resets)
        self._start_occupancy_timer()
        self._notify_state_change()

    async def handle_occupancy_lights_on(self) -> None:
        _LOGGER.debug("Area %s: handle_occupancy_lights_on", self.area.id)
        self._enforce_occupancy_timer()

    async def handle_occupancy_lights_off(self) -> None:
        _LOGGER.debug("Area %s: handle_occupancy_lights_off", self.area.id)
        self._occupancy_timer.cancel()

    async def handle_ambient_enabled(self) -> None:
        """Ambient mode enabled (zone or area). Activate ambient scene if off."""
        _LOGGER.debug(
            "Area %s: handle_ambient_enabled current_scene=%s",
            self.area.id,
            self._state.scene_slug,
        )
        if not self._state.is_off:
            return
        holiday = self._get_holiday_mode()
        ambient_mode = self._get_ambient_scene_mode()
        if ambient_mode == "holiday" and holiday != HOLIDAY_MODE_NONE:
            await self._activate_holiday_scene(ActivationSource.AMBIENCE)
        else:
            await self._activate_scene("ambient", ActivationSource.AMBIENCE)

    async def handle_ambient_disabled(self) -> None:
        """Ambient mode disabled. Only turn off if it was activated by ambience."""
        _LOGGER.debug(
            "Area %s: handle_ambient_disabled was_ambient_activated=%s",
            self.area.id,
            self._state.was_ambient_activated,
        )
        if not self._state.was_ambient_activated:
            return
        await self._activate_scene(SCENE_OFF_INTERNAL, ActivationSource.AMBIENCE)

    async def handle_holiday_changed(self, mode: str) -> None:
        """Holiday mode changed."""
        _LOGGER.debug(
            "Area %s: handle_holiday_changed mode=%s current_scene=%s source=%s",
            self.area.id,
            mode,
            self._state.scene_slug,
            self._state.source.value,
        )
        if mode != HOLIDAY_MODE_NONE:
            # Activate the new holiday scene if:
            # - lights are off, OR
            # - we're already in a (different) holiday scene that was
            #   itself activated by the holiday handler (so we're tracking
            #   the active holiday rather than reverting a manual choice)
            if self._state.is_off or (
                self._state.scene_slug in HOLIDAY_SCENES
                and self._state.scene_slug != mode
                and self._state.source == ActivationSource.HOLIDAY
            ):
                await self._activate_scene(mode, ActivationSource.HOLIDAY)
        else:
            # Holiday disabled - turn off only if it was holiday-activated
            if (
                self._state.scene_slug in HOLIDAY_SCENES
                and self._state.source == ActivationSource.HOLIDAY
            ):
                await self._activate_scene(SCENE_OFF_INTERNAL, ActivationSource.HOLIDAY)

    async def handle_circadian_enabled(self) -> None:
        _LOGGER.debug("Area %s: handle_circadian_enabled", self.area.id)
        await self._activate_circadian(ActivationSource.USER)

    async def handle_leader_change(
        self,
        new_slug: str | None,
        reason,
    ) -> None:
        """Apply a leader's state transition to this follower (Scenario B).

        Follower is left alone when off, ambient-like, or in manual mode.
        For concrete scene activations, follower activates the same slug
        if it has one; missing slugs are logged and skipped.
        For OFF/AMBIENT leader transitions, follower only follows when
        configured via follow_leader_deactivation. MANUAL never propagates.

        `reason` is a LeaderReason enum; imported locally to avoid
        cluttering the top of the module with feature-specific types.
        """
        from .area_state import LeaderReason

        _LOGGER.debug(
            "Area %s: handle_leader_change leader=%s reason=%s slug=%s follower_state=%s",
            self.area.id,
            self.leader.area.id if self.leader else None,
            reason.value,
            new_slug,
            self._state.state.value,
        )

        if self._state.is_off:
            return
        if self._state.is_ambient_like:
            return
        if self._state.is_manual:
            return

        if reason is LeaderReason.SCENE_ACTIVATED:
            if new_slug is None or new_slug not in self.area.scene_slugs:
                _LOGGER.warning(
                    "Area %s: leader %s activated scene %s but follower has "
                    "no such scene; skipping",
                    self.area.id,
                    self.leader.area.id if self.leader else "?",
                    new_slug,
                )
                return
            await self._activate_scene(new_slug, ActivationSource.LEADER)
            return

        if reason is LeaderReason.OFF:
            if not self.area.follow_leader_deactivation:
                return
            await self._activate_scene(SCENE_OFF_INTERNAL, ActivationSource.LEADER)
            return

        if reason is LeaderReason.AMBIENT:
            if not self.area.follow_leader_deactivation:
                return
            await self._activate_scene("ambient", ActivationSource.LEADER)
            return

        # LeaderReason.MANUAL: always no-op

    # ── Timer callbacks ────────────────────────────────────────────────

    async def _on_motion_timer(self) -> None:
        _LOGGER.debug("Area %s: motion timer expired", self.area.id)
        await self._cleanup_linked_areas()
        await self.lighting_off_fade(source=ActivationSource.MOTION)

    async def _on_occupancy_timer(self) -> None:
        _LOGGER.debug("Area %s: occupancy timer expired", self.area.id)
        if self._state.is_off or self._state.is_ambient_like:
            return
        await self.lighting_off_fade(source=ActivationSource.OCCUPANCY)

    # ── Cleanup ────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        self._motion_timer.cancel()
        self._motion_night_timer.cancel()
        self._occupancy_timer.cancel()
