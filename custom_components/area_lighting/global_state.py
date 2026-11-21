"""Global master toggles and scene animation choices for area_lighting.

Owns three booleans: motion-triggered lights-on, occupancy-timeout
lights-off, and demand-response shedding, each gating every area. Also
owns the percentage by which every circadian group's minimum brightness
is lowered and each scene's animation choice. Persisted via the StateStorage
reserved global key. Reaches controllers through hass.data.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .models import AreaLightingConfig
from .state_storage import StateStorage

_LOGGER = logging.getLogger(__name__)


class GlobalToggles:
    """Persist global settings and notify their listeners and controllers."""

    def __init__(self, hass: HomeAssistant, state_storage: StateStorage) -> None:
        self._hass = hass
        self._state_storage = state_storage
        self._motion_lights_enabled = True
        self._occupancy_timeout_enabled = True
        self._demand_response_active = False
        self._circadian_min_brightness_reduction = 0.0
        self._scene_animations: dict[str, str] = {}
        self._scene_animation_options: dict[str, list[str]] = {}
        self._scene_animation_names: dict[str, str] = {}
        self._listeners: list[Callable[[], None]] = []

    @property
    def motion_lights_enabled(self) -> bool:
        return self._motion_lights_enabled

    @property
    def occupancy_timeout_enabled(self) -> bool:
        return self._occupancy_timeout_enabled

    @property
    def demand_response_active(self) -> bool:
        return self._demand_response_active

    @property
    def circadian_min_brightness_reduction(self) -> float:
        return self._circadian_min_brightness_reduction

    @property
    def animated_scene_ids(self) -> list[str]:
        return list(self._scene_animation_options)

    def scene_animation(self, scene_id: str) -> str | None:
        return self._scene_animations.get(scene_id)

    def scene_animation_options(self, scene_id: str) -> list[str]:
        return self._scene_animation_options[scene_id]

    def scene_animation_name(self, scene_id: str) -> str:
        return self._scene_animation_names[scene_id]

    def configure_scene_animations(self, config: AreaLightingConfig) -> None:
        names: dict[str, str] = {}
        options: dict[str, list[str]] = {}
        for area in config.enabled_areas:
            for scene in area.scenes:
                names.setdefault(scene.slug, scene.name)
                if not scene.animations:
                    continue
                choices = options.setdefault(scene.slug, ["off"])
                choices.extend(name for name in scene.animations if name not in choices)
        selections: dict[str, str] = {}
        for scene_id, choices in options.items():
            selected = self._scene_animations.get(scene_id, choices[1])
            selections[scene_id] = selected if selected in choices else choices[1]
        changed = selections != self._scene_animations
        self._scene_animation_names = names
        self._scene_animation_options = options
        self._scene_animations = selections
        if changed:
            self._schedule_save()

    # ── listener plumbing (mirrors controller.add_state_listener) ──
    def add_state_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def remove_state_listener(self, cb: Callable[[], None]) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def _schedule_save(self) -> None:
        self._hass.async_create_task(self._state_storage.async_save_global_state(self.state_dict()))

    # ── persistence ──
    def state_dict(self) -> dict:
        return {
            "motion_lights_enabled": self._motion_lights_enabled,
            "occupancy_timeout_enabled": self._occupancy_timeout_enabled,
            "demand_response_active": self._demand_response_active,
            "circadian_min_brightness_reduction": self._circadian_min_brightness_reduction,
            "scene_animations": dict(self._scene_animations),
        }

    def load_persisted_state(self, data: dict) -> None:
        if not data:
            return
        if "motion_lights_enabled" in data:
            self._motion_lights_enabled = bool(data["motion_lights_enabled"])
        if "occupancy_timeout_enabled" in data:
            self._occupancy_timeout_enabled = bool(data["occupancy_timeout_enabled"])
        if "demand_response_active" in data:
            self._demand_response_active = bool(data["demand_response_active"])
        if "circadian_min_brightness_reduction" in data:
            self._circadian_min_brightness_reduction = _clamp_percent(
                data["circadian_min_brightness_reduction"]
            )
        self._scene_animations = dict(data.get("scene_animations", {}))

    async def async_set_scene_animation(self, scene_id: str, option: str) -> None:
        if option not in self._scene_animation_options[scene_id]:
            raise ValueError(f"Unknown animation {option!r} for scene {scene_id!r}")
        if self._scene_animations[scene_id] == option:
            return
        previous_option = self._scene_animations[scene_id]
        self._scene_animations[scene_id] = option
        self._notify()
        self._schedule_save()
        controllers = self._hass.data.get(DOMAIN, {}).get("controllers", {})
        await asyncio.gather(
            *(
                ctrl.async_scene_animation_changed(scene_id, previous_option=previous_option)
                for ctrl in controllers.values()
            )
        )

    # ── setters (called by the switch entities) ──
    async def async_set_motion_lights_enabled(self, enabled: bool) -> None:
        if self._motion_lights_enabled == enabled:
            return
        self._motion_lights_enabled = enabled
        # No cross-controller side effect: _check_conditions reads the flag
        # live on the next motion edge.
        self._notify()
        self._schedule_save()

    async def async_set_occupancy_timeout_enabled(self, enabled: bool) -> None:
        if self._occupancy_timeout_enabled == enabled:
            return
        self._occupancy_timeout_enabled = enabled
        controllers = self._hass.data.get(DOMAIN, {}).get("controllers", {})
        for ctrl in controllers.values():
            if enabled:
                ctrl.enforce_occupancy_timer()
            else:
                ctrl.cancel_occupancy_timer()
        self._notify()
        self._schedule_save()

    async def async_set_demand_response_active(self, enabled: bool) -> None:
        if self._demand_response_active == enabled:
            return
        self._demand_response_active = enabled
        controllers = self._hass.data.get(DOMAIN, {}).get("controllers", {})
        for ctrl in controllers.values():
            # Re-drive each area through its NORMAL activation path, which
            # applies the flipped filter at target resolution. These are
            # plain activations with no shared lock, so a flip can never
            # block (or be blocked by) normal light control.
            self._hass.async_create_task(ctrl.reactivate_for_demand_response())
        self._notify()
        self._schedule_save()

    async def async_set_circadian_min_brightness_reduction(self, reduction: float) -> None:
        reduction = _clamp_percent(reduction)
        if self._circadian_min_brightness_reduction == reduction:
            return
        self._circadian_min_brightness_reduction = reduction
        controllers = self._hass.data.get(DOMAIN, {}).get("controllers", {})
        for ctrl in controllers.values():
            ctrl.refresh_circadian_brightness()
        self._notify()
        self._schedule_save()


def _clamp_percent(value: float) -> float:
    return min(100.0, max(0.0, float(value)))
