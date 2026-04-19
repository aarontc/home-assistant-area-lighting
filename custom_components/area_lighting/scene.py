"""Scene platform for Area Lighting.

Generates HA scene entities for visual scenes (daylight, evening, night,
ambient, christmas, etc.). Uses stored snapshot data for light states
when available, falling back to skeleton defaults (on/off by role).

Behavioral scenes (circadian, off, off_internal) are NOT created here -
they are handled as internal controller actions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DOMAIN
from .models import AreaConfig, SceneConfig
from .scene_storage import SceneStorage

_LOGGER = logging.getLogger(__name__)

# Scene slugs that should NOT be exposed as HA scene entities at all.
# off_internal is a private state-tracking scene only used internally.
HIDDEN_SCENES = frozenset({"off_internal"})

# Scene slugs that are behavioral - they call controller methods rather than
# applying static light states. They ARE exposed as HA scenes so external
# integrations can trigger them via scene.turn_on.
BEHAVIORAL_SCENE_HANDLERS = {
    "off": "lighting_off",
    "circadian": "lighting_circadian",
}

# Backwards-compat alias for code that imports BEHAVIORAL_SCENES
BEHAVIORAL_SCENES = HIDDEN_SCENES | frozenset(BEHAVIORAL_SCENE_HANDLERS.keys())


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Area Lighting scene entities."""
    if discovery_info is None:
        return

    storage: SceneStorage = hass.data[DOMAIN]["scene_storage"]
    area_config = hass.data[DOMAIN]["config"]

    entities = []
    for area in area_config.enabled_areas:
        for scene_cfg in area.scenes:
            if scene_cfg.slug in BEHAVIORAL_SCENES:
                continue
            entities.append(AreaLightingScene(hass, area, scene_cfg, storage))

    _LOGGER.info("Creating %d scene entities", len(entities))
    async_add_entities(entities)


class AreaLightingScene(Scene):
    """A scene entity backed by stored snapshot data or skeleton defaults."""

    def __init__(
        self,
        hass: HomeAssistant,
        area: AreaConfig,
        scene_cfg: SceneConfig,
        storage: SceneStorage,
    ) -> None:
        self.hass = hass
        self._area = area
        self._scene_cfg = scene_cfg
        self._storage = storage
        self._attr_name = f"{area.name} {scene_cfg.name}"
        self._attr_unique_id = f"area_lighting_{area.id}_{scene_cfg.slug}"
        self._attr_icon = scene_cfg.icon or _scene_icon(scene_cfg.slug)
        # Explicit entity_id to match the pattern used by the controller
        self.entity_id = f"scene.{area.id}_{scene_cfg.slug}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the data source for this scene."""
        if self._storage.get_scene_data(self._area.id, self._scene_cfg.slug):
            source = "snapshot"
        elif self._scene_cfg.entities:
            source = "config"
        else:
            source = "skeleton"
        return {"scene_source": source}

    async def async_activate(self, **kwargs: Any) -> None:
        """Activate this scene.

        Priority: stored snapshot > config entities > skeleton defaults.
        """
        transition = kwargs.get("transition")
        _LOGGER.debug(
            "Area %s: activating scene %s (has_stored=%s, has_config=%s)",
            self._area.id,
            self._scene_cfg.slug,
            self._storage.get_scene_data(self._area.id, self._scene_cfg.slug) is not None,
            self._scene_cfg.entities is not None,
        )

        # 1. Stored snapshot (from snapshot_scene service)
        stored = self._storage.get_scene_data(self._area.id, self._scene_cfg.slug)
        if stored:
            await self._apply_stored(stored, transition)
            return

        # 2. Inline config entities (from YAML config)
        if self._scene_cfg.entities:
            await self._apply_stored(self._scene_cfg.entities, transition)
            return

        # 3. Skeleton: all lights on/off by role membership
        await self._apply_skeleton(transition)

    async def _apply_stored(
        self,
        stored: dict[str, Any],
        transition: float | None,
    ) -> None:
        """Apply stored snapshot data to lights."""
        _LOGGER.debug(
            "Area %s: applying scene data (%d entities)",
            self._area.id,
            len(stored),
        )
        tasks: list = []
        for entity_id, state_data in stored.items():
            if not entity_id.startswith("light."):
                continue

            target_state = state_data.get("state", "off")
            service_data: dict[str, Any] = {"entity_id": entity_id}

            if transition is not None:
                service_data["transition"] = transition

            if target_state == "on":
                # Apply stored attributes. Skip keys whose value is None —
                # Hue's 2025 deprecation warns when `effect=None` is passed
                # to light.turn_on, and None is never meaningful for any of
                # these attributes anyway.
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
                        service_data[attr] = state_data[attr]
                _LOGGER.debug(
                    "Area %s: scene_apply light.turn_on %s",
                    self._area.id,
                    service_data,
                )
                tasks.append(
                    self.hass.services.async_call(
                        "light",
                        "turn_on",
                        service_data,
                        blocking=True,
                    )
                )
            else:
                _LOGGER.debug(
                    "Area %s: scene_apply light.turn_off entity=%s",
                    self._area.id,
                    entity_id,
                )
                tasks.append(
                    self.hass.services.async_call(
                        "light",
                        "turn_off",
                        service_data,
                        blocking=True,
                    )
                )
        if tasks:
            await asyncio.gather(*tasks)

    async def _apply_skeleton(self, transition: float | None) -> None:
        """Apply skeleton defaults: lights on/off based on role membership."""
        scene_slug = self._scene_cfg.slug
        excluded = set(self._scene_cfg.group_exclude)

        tasks: list = []
        for light in self._area.all_lights:
            if light.id in excluded:
                continue

            service_data: dict[str, Any] = {"entity_id": light.id}
            if transition is not None:
                service_data["transition"] = transition

            if light.in_scene(scene_slug):
                tasks.append(
                    self.hass.services.async_call(
                        "light",
                        "turn_on",
                        service_data,
                        blocking=True,
                    )
                )
            else:
                tasks.append(
                    self.hass.services.async_call(
                        "light",
                        "turn_off",
                        service_data,
                        blocking=True,
                    )
                )
        if tasks:
            await asyncio.gather(*tasks)


def _scene_icon(slug: str) -> str:
    return {
        "ambient": "mdi:television-ambient-light",
        "christmas": "mdi:pine-tree",
        "halloween": "mdi:ghost",
        "daylight": "mdi:white-balance-sunny",
        "evening": "mdi:weather-sunset",
        "night": "mdi:weather-night",
        "movie": "mdi:movie-open",
        "circadian": "mdi:theme-light-dark",
        "off": "mdi:lightbulb-off",
    }.get(slug, "mdi:palette")


class BehavioralScene(Scene):
    """A scene that, when activated, calls a controller method.

    Used for off and circadian scenes so they can be triggered via scene.turn_on
    by external integrations.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        area: AreaConfig,
        slug: str,
        name: str,
        controller_method: str,
    ) -> None:
        self.hass = hass
        self._area = area
        self._slug = slug
        self._controller_method = controller_method
        self._attr_name = f"{area.name} {name}"
        self._attr_unique_id = f"area_lighting_{area.id}_{slug}"
        self._attr_icon = _scene_icon(slug)
        self.entity_id = f"scene.{area.id}_{slug}"

    async def async_activate(self, **kwargs: Any) -> None:
        """Call the controller method for this behavioral scene."""
        controllers = self.hass.data.get(DOMAIN, {}).get("controllers", {})
        ctrl = controllers.get(self._area.id)
        if ctrl is None:
            _LOGGER.warning(
                "Area %s: BehavioralScene %s no controller registered",
                self._area.id,
                self.entity_id,
            )
            return
        method = getattr(ctrl, self._controller_method, None)
        if method is None:
            _LOGGER.warning(
                "Area %s: BehavioralScene %s controller has no method %s",
                self._area.id,
                self.entity_id,
                self._controller_method,
            )
            return
        await method()
