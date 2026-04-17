"""Area Lighting integration for Home Assistant."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config import async_hass_config_yaml
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .binary_sensor import AreaOccupiedBinarySensor
from .config_schema import (
    ALERT_PATTERN_SCHEMA,
    AREA_SCHEMA,
    parse_config,
    validate_leader_follower_graph,
)
from .const import DOMAIN
from .controller import AreaLightingController
from .diagnostics import AreaLightingDiagnosticSensor
from .event_handlers import async_setup_event_handlers
from .number import (
    AreaManualFadeoutNumber,
    AreaMotionFadeoutNumber,
    AreaMotionNightTimeoutNumber,
    AreaMotionTimeoutNumber,
    AreaOccupancyNightTimeoutNumber,
    AreaOccupancyTimeoutNumber,
)
from .scene import (
    BEHAVIORAL_SCENE_HANDLERS,
    HIDDEN_SCENES,
    AreaLightingScene,
    BehavioralScene,
)
from .scene_storage import SceneStorage
from .select import AreaLastSceneSelect
from .services import async_register_services
from .state_storage import StateStorage
from .switch import SWITCH_DEFS, AreaLightingSwitch

_LOGGER = logging.getLogger(__name__)


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("areas"): vol.All(cv.ensure_list, [AREA_SCHEMA]),
                vol.Optional("alert_patterns", default={}): {
                    cv.string: ALERT_PATTERN_SCHEMA,
                },
                # Ignored fields from templater.yaml kept for config compat
                vol.Optional("base_url"): str,
            },
            extra=vol.PREVENT_EXTRA,
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Area Lighting integration."""
    if DOMAIN not in config:
        return True

    conf = config[DOMAIN]
    area_config = parse_config(conf)
    try:
        validate_leader_follower_graph(area_config)
    except vol.Invalid as err:
        _LOGGER.error("Area Lighting: invalid leader/follower config: %s", err)
        return False

    # Initialize scene storage
    scene_storage = SceneStorage(hass)
    await scene_storage.async_load()

    # Initialize state storage (per-area runtime state, persisted across reboots)
    state_storage = StateStorage(hass)
    await state_storage.async_load()

    hass.data[DOMAIN] = {
        "config": area_config,
        "controllers": {},
        "unsubs": [],
        "scene_storage": scene_storage,
        "state_storage": state_storage,
    }

    # Create controllers for each enabled area, restoring persisted state
    controllers: dict[str, AreaLightingController] = {}
    for area in area_config.enabled_areas:
        ctrl = AreaLightingController(hass, area, area_config)
        ctrl.load_persisted_state(state_storage.get_area_state(area.id))
        controllers[area.id] = ctrl
    hass.data[DOMAIN]["controllers"] = controllers

    # Wire leader/follower references. Safe because every enabled
    # controller is in `controllers` by this point; graph invariants were
    # already validated above.
    for ctrl in controllers.values():
        leader_id = ctrl.area.leader_area_id
        if leader_id is None:
            continue
        leader = controllers.get(leader_id)
        if leader is None:
            _LOGGER.warning(
                "Area %s: configured leader %s is not an enabled area; "
                "follower will run standalone",
                ctrl.area.id,
                leader_id,
            )
            continue
        ctrl.leader = leader
        leader.followers.append(ctrl)

    enabled = area_config.enabled_areas
    _LOGGER.info(
        "Area Lighting: %d areas (%s)",
        len(enabled),
        ", ".join(a.id for a in enabled),
    )

    # Register services
    await async_register_services(hass)

    # Reload service — re-reads YAML and updates the parsed config so
    # new/changed alert patterns (and other top-level config) take effect
    # without restarting HA.  Controllers and entities stay alive; only
    # the config object is swapped.
    async def _handle_reload(_call: ServiceCall) -> None:
        raw_yaml = await async_hass_config_yaml(hass)
        raw_conf = raw_yaml.get(DOMAIN)
        if raw_conf is None:
            _LOGGER.warning("area_lighting: YAML key %r not found during reload", DOMAIN)
            return
        validated = CONFIG_SCHEMA({DOMAIN: raw_conf})[DOMAIN]
        new_config = parse_config(validated)
        try:
            validate_leader_follower_graph(new_config)
        except vol.Invalid as err:
            _LOGGER.error("area_lighting reload: invalid config: %s", err)
            return
        hass.data[DOMAIN]["config"] = new_config
        _LOGGER.info(
            "area_lighting: configuration reloaded (%d areas, %d alert patterns)",
            len(new_config.enabled_areas),
            len(new_config.alert_patterns),
        )

    hass.services.async_register(DOMAIN, "reload", _handle_reload)

    # Defer entity registration until HA is fully started to avoid
    # blocking startup.
    async def _on_started(event: Event) -> None:
        from .event_handlers import async_validate_external_entities

        try:
            await _register_scene_entities(hass, area_config, scene_storage)
        except Exception:
            _LOGGER.exception("Failed to register scene entities")
        try:
            await _register_diagnostic_sensor(hass)
        except Exception:
            _LOGGER.exception("Failed to register diagnostic sensor")
        try:
            await _register_helper_entities(hass)
        except Exception:
            _LOGGER.exception("Failed to register helper entities")
        # Validate external entities (D10) — non-fatal, logs on failure
        try:
            await async_validate_external_entities(hass, area_config)
        except Exception:
            _LOGGER.exception("Failed to validate external entities")
        # Restore persisted timer deadlines (D4)
        try:
            for ctrl in hass.data[DOMAIN]["controllers"].values():
                await ctrl.restore_timers()
        except Exception:
            _LOGGER.exception("Failed to restore timer deadlines")

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)

    # Set up event handlers
    unsubs = await async_setup_event_handlers(hass)
    hass.data[DOMAIN]["unsubs"] = unsubs

    return True


async def _register_scene_entities(
    hass: HomeAssistant,
    area_config,
    scene_storage: SceneStorage,
) -> None:
    """Register scene entities directly with HA's scene EntityComponent.

    Also assigns each scene to its HA area in the entity registry.
    """
    from homeassistant.components.scene import DATA_COMPONENT
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er

    component = hass.data.get(DATA_COMPONENT)
    if component is None:
        _LOGGER.error("Scene component not loaded; cannot register scene entities")
        return

    area_reg = ar.async_get(hass)
    entity_reg = er.async_get(hass)

    entities: list = []
    for area in area_config.enabled_areas:
        {s.slug for s in area.scenes}

        # Visual scenes from config
        for scene_cfg in area.scenes:
            if scene_cfg.slug in HIDDEN_SCENES:
                continue
            if scene_cfg.slug in BEHAVIORAL_SCENE_HANDLERS:
                # Handled below as a BehavioralScene
                continue
            entities.append(AreaLightingScene(hass, area, scene_cfg, scene_storage))

        # Behavioral scenes (off, circadian) - always registered for every area
        # so external integrations can trigger them via scene.turn_on
        for slug, method in BEHAVIORAL_SCENE_HANDLERS.items():
            # Use a friendly name from config if defined, otherwise generate one
            name = slug.title()
            for s in area.scenes:
                if s.slug == slug:
                    name = s.name
                    break
            entities.append(BehavioralScene(hass, area, slug, name, method))

    if not entities:
        return

    await component.async_add_entities(entities)
    _LOGGER.info("Registered %d scene entities", len(entities))

    # Assign each scene to its HA area (creating the area if needed).
    for entity in entities:
        ha_area = area_reg.async_get_area_by_name(entity._area.name)
        if ha_area is None:
            ha_area = area_reg.async_get_or_create(entity._area.name)

        registry_entry = entity_reg.async_get(entity.entity_id)
        if registry_entry and registry_entry.area_id != ha_area.id:
            entity_reg.async_update_entity(
                entity.entity_id,
                area_id=ha_area.id,
            )
            _LOGGER.debug("Assigned %s to area %s", entity.entity_id, ha_area.name)


async def _register_helper_entities(hass: HomeAssistant) -> None:
    """Register switch/select/number helper entities for each area."""
    from homeassistant.components.number import (  # type: ignore[attr-defined]
        DATA_COMPONENT as NUMBER_COMPONENT,
    )
    from homeassistant.components.select import (  # type: ignore[attr-defined]
        DATA_COMPONENT as SELECT_COMPONENT,
    )
    from homeassistant.components.switch import DATA_COMPONENT as SWITCH_COMPONENT

    controllers: dict[str, AreaLightingController] = hass.data.get(DOMAIN, {}).get(
        "controllers", {}
    )
    if not controllers:
        return

    switch_component = hass.data.get(SWITCH_COMPONENT)
    select_component = hass.data.get(SELECT_COMPONENT)
    number_component = hass.data.get(NUMBER_COMPONENT)

    switches: list = []
    selects: list = []
    numbers: list = []
    binary_sensors: list = []

    for ctrl in controllers.values():
        # Switches: motion_light_enabled, ambience_enabled, night_mode, motion_override_ambient
        for attr, name_suffix, icon, _default in SWITCH_DEFS:
            switches.append(AreaLightingSwitch(ctrl, attr, name_suffix, icon))
        selects.append(AreaLastSceneSelect(ctrl))
        numbers.append(AreaManualFadeoutNumber(ctrl))
        numbers.append(AreaMotionFadeoutNumber(ctrl))
        numbers.append(AreaMotionTimeoutNumber(ctrl))
        numbers.append(AreaMotionNightTimeoutNumber(ctrl))
        numbers.append(AreaOccupancyTimeoutNumber(ctrl))
        numbers.append(AreaOccupancyNightTimeoutNumber(ctrl))
        binary_sensors.append(AreaOccupiedBinarySensor(ctrl))

    if switch_component is not None and switches:
        await switch_component.async_add_entities(switches)
        _LOGGER.info("Registered %d switch entities", len(switches))
    else:
        _LOGGER.warning("Switch component not loaded; skipping %d switches", len(switches))

    if select_component is not None and selects:
        await select_component.async_add_entities(selects)
        _LOGGER.info("Registered %d select entities", len(selects))
    else:
        _LOGGER.warning("Select component not loaded; skipping %d selects", len(selects))

    if number_component is not None and numbers:
        await number_component.async_add_entities(numbers)
        _LOGGER.info("Registered %d number entities", len(numbers))
    else:
        _LOGGER.warning("Number component not loaded; skipping %d numbers", len(numbers))

    from homeassistant.components.binary_sensor import (
        DATA_COMPONENT as BINARY_SENSOR_COMPONENT,
    )

    bs_component = hass.data.get(BINARY_SENSOR_COMPONENT)
    if bs_component is not None and binary_sensors:
        await bs_component.async_add_entities(binary_sensors)
        _LOGGER.info("Registered %d binary_sensor entities", len(binary_sensors))
    else:
        _LOGGER.warning(
            "Binary sensor component not loaded; skipping %d binary sensors",
            len(binary_sensors),
        )

    # Assign every helper entity + its device to the matching HA area so
    # HA's auto-generated area dashboard picks them up.
    await _assign_entities_to_ha_areas(
        hass, controllers, switches, selects, numbers, binary_sensors
    )


async def _assign_entities_to_ha_areas(
    hass: HomeAssistant,
    controllers: dict[str, AreaLightingController],
    switches: list,
    selects: list,
    numbers: list,
    binary_sensors: list | None = None,
) -> None:
    """Assign every area_lighting helper entity to the matching HA area
    in the area_registry. Creates the HA area if missing so fresh
    installs pick up the assignment automatically.

    Note: we don't also register an HA "device" per area because
    area_lighting is YAML-based — device_registry.async_get_or_create
    requires a real ConfigEntry ID we don't have. Grouping via HA
    areas is sufficient for the user's dashboard goal (Settings →
    Areas → <Area> → Create dashboard).
    """
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er

    area_reg = ar.async_get(hass)
    entity_reg = er.async_get(hass)

    for ctrl in controllers.values():
        area = ctrl.area
        ha_area = area_reg.async_get_area_by_name(area.name)
        if ha_area is None:
            ha_area = area_reg.async_get_or_create(area.name)

        # Assign every helper entity belonging to this area
        for entity in (*switches, *selects, *numbers, *(binary_sensors or [])):
            ctrl_attr = getattr(entity, "_controller", None)
            if ctrl_attr is None or ctrl_attr is not ctrl:
                continue
            registry_entry = entity_reg.async_get(entity.entity_id)
            if registry_entry is None:
                continue
            if registry_entry.area_id != ha_area.id:
                entity_reg.async_update_entity(
                    entity.entity_id,
                    area_id=ha_area.id,
                )


async def _register_diagnostic_sensor(hass: HomeAssistant) -> None:
    """Register the diagnostic sensor via the sensor EntityComponent."""
    from homeassistant.components.sensor import (  # type: ignore[attr-defined]
        DATA_COMPONENT as SENSOR_DATA_COMPONENT,
    )

    component = hass.data.get(SENSOR_DATA_COMPONENT)
    if component is None:
        _LOGGER.error("Sensor component not loaded; cannot register diagnostic sensor")
        return

    sensor = AreaLightingDiagnosticSensor(hass)
    await component.async_add_entities([sensor])
    _LOGGER.info("Registered diagnostic sensor")
