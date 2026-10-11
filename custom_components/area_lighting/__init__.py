"""Area Lighting integration for Home Assistant."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Sequence

import voluptuous as vol
from homeassistant.config import async_hass_config_yaml
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import ConfigType

from .binary_sensor import AreaOccupiedBinarySensor
from .circadian_clock import AreaLightingCircadianSensor, CircadianClock
from .config_schema import (
    ALERT_PATTERN_SCHEMA,
    AREA_SCHEMA,
    CIRCADIAN_SCHEMA,
    parse_config,
    validate_circadian_groups,
    validate_circadian_kelvin_routes,
    validate_leader_follower_graph,
)
from .const import DOMAIN
from .controller import AreaLightingController
from .diagnostics import AreaLightingDiagnosticSensor
from .event_handlers import async_setup_event_handlers
from .global_state import GlobalToggles
from .models import AreaConfig, AreaLightingConfig
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
from .switch import GLOBAL_SWITCH_DEFS, SWITCH_DEFS, AreaLightingGlobalSwitch, AreaLightingSwitch

_LOGGER = logging.getLogger(__name__)

# Registry option marking an entry the hidden default has been applied to.
HIDDEN_DEFAULT_APPLIED = "hidden_default_applied"
ENTITY_DOMAINS = ("binary_sensor", "number", "scene", "select", "sensor", "switch")
# Key in hass.data[DOMAIN]["entities"] for the entities no area owns.
GLOBAL_ENTITIES = "__global__"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("areas"): vol.All(cv.ensure_list, [AREA_SCHEMA]),
                vol.Optional("alert_patterns", default={}): {
                    cv.string: ALERT_PATTERN_SCHEMA,
                },
                vol.Optional("scene_self_heal", default=True): cv.boolean,
                vol.Optional("circadian", default={}): CIRCADIAN_SCHEMA,
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
    try:
        validate_circadian_kelvin_routes(area_config)
    except vol.Invalid as err:
        _LOGGER.error("Area Lighting: invalid circadian_kelvin_routes config: %s", err)
        return False
    try:
        validate_circadian_groups(area_config)
    except vol.Invalid as err:
        _LOGGER.error("Area Lighting: invalid circadian_groups config: %s", err)
        return False

    for area in area_config.areas:
        if area.circadian_kelvin_routes is None:
            continue
        if "circadian" not in area.scene_slugs:
            _LOGGER.warning(
                "Area Lighting: area '%s' has circadian_kelvin_routes "
                "but no `circadian` scene declared — routing will be "
                "inert until a circadian scene is added",
                area.id,
            )

    # Initialize scene storage
    scene_storage = SceneStorage(hass)
    await scene_storage.async_load()

    # Initialize state storage (per-area runtime state, persisted across reboots)
    state_storage = StateStorage(hass)
    await state_storage.async_load()

    # Global (all-area) master toggles, restored from persisted state
    global_toggles = GlobalToggles(hass, state_storage)
    global_toggles.load_persisted_state(state_storage.get_global_state())

    clock = CircadianClock(hass, area_config.circadian)

    hass.data[DOMAIN] = {
        "config": area_config,
        "controllers": {},
        "unsubs": [],
        "scene_storage": scene_storage,
        "state_storage": state_storage,
        "global": global_toggles,
        "circadian_clock": clock,
        "reload_lock": asyncio.Lock(),
        # Every entity added, by area id, for reload and teardown to find.
        "entities": {},
    }
    clock.async_start()

    # Create controllers for each enabled area, restoring persisted state
    controllers: dict[str, AreaLightingController] = {}
    for area in area_config.enabled_areas:
        ctrl = AreaLightingController(hass, area, area_config)
        ctrl.load_persisted_state(state_storage.get_area_state(area.id))
        controllers[area.id] = ctrl
    hass.data[DOMAIN]["controllers"] = controllers

    _wire_leaders(controllers)

    enabled = area_config.enabled_areas
    _LOGGER.info(
        "Area Lighting: %d areas (%s)",
        len(enabled),
        ", ".join(a.id for a in enabled),
    )

    # Register services
    await async_register_services(hass)

    async def _handle_reload(_call: ServiceCall) -> None:
        async with hass.data[DOMAIN]["reload_lock"]:
            await _async_reload(hass)

    hass.services.async_register(DOMAIN, "reload", _handle_reload)

    # Defer entity registration until HA is fully started to avoid
    # blocking startup.
    async def _on_started(event: Event) -> None:
        from .event_handlers import (
            async_validate_external_entities,
            async_validate_lutron_remotes,
        )

        # Removing a listen_once listener that already fired logs an error.
        hass.data[DOMAIN]["unsub_started"] = None
        hass.data[DOMAIN]["startup_task"] = asyncio.current_task()
        # A reload may have run before Home Assistant finished starting.
        config = hass.data[DOMAIN]["config"]
        try:
            await _register_scene_entities(hass, config.enabled_areas)
        except Exception:
            _LOGGER.exception("Failed to register scene entities")
        try:
            await _register_sensors(hass)
        except Exception:
            _LOGGER.exception("Failed to register sensors")
        try:
            await _register_helper_entities(
                hass, list(hass.data[DOMAIN]["controllers"].values()), global_toggles
            )
        except Exception:
            _LOGGER.exception("Failed to register helper entities")
        try:
            _apply_hidden_default(hass)
        except Exception:
            _LOGGER.exception("Failed to hide entities registered by an earlier release")
        # Validate external entities (D10) — non-fatal, logs on failure
        try:
            await async_validate_external_entities(hass, config)
        except Exception:
            _LOGGER.exception("Failed to validate external entities")
        try:
            await async_validate_lutron_remotes(hass, config)
        except Exception:
            _LOGGER.exception("Failed to validate Lutron remotes")
        # Restore persisted timer deadlines (D4)
        try:
            for ctrl in hass.data[DOMAIN]["controllers"].values():
                await ctrl.restore_timers()
        except Exception:
            _LOGGER.exception("Failed to restore timer deadlines")
        # Reconcile persisted state vs actual light state
        try:
            for ctrl in hass.data[DOMAIN]["controllers"].values():
                ctrl.reconcile_startup_state()
        except Exception:
            _LOGGER.exception("Failed to reconcile startup state")
        try:
            for ctrl in hass.data[DOMAIN]["controllers"].values():
                await ctrl.async_resume_circadian()
        except Exception:
            _LOGGER.exception("Failed to resume circadian areas")

    hass.data[DOMAIN]["unsub_started"] = hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STARTED, _on_started
    )

    # Set up event handlers
    hass.data[DOMAIN]["unsubs"] = await async_setup_event_handlers(hass, area_config)

    # Last, so a failed setup leaves no entity behind.
    try:
        await _register_circadian_sensor(hass)
    except Exception:
        _LOGGER.exception("Failed to register the circadian sensor")

    return True


def _wire_leaders(controllers: dict[str, AreaLightingController]) -> None:
    """Link each follower to its leader. Every enabled controller must be in
    `controllers`, and the leader/follower graph must already be validated."""
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


async def _async_read_config(hass: HomeAssistant) -> AreaLightingConfig:
    """Read and validate the YAML config, raising HomeAssistantError on any failure."""
    try:
        raw_conf = (await async_hass_config_yaml(hass)).get(DOMAIN)
        if raw_conf is None:
            raise HomeAssistantError(f"YAML key {DOMAIN!r} not found")
        config = parse_config(CONFIG_SCHEMA({DOMAIN: raw_conf})[DOMAIN])
        validate_leader_follower_graph(config)
        validate_circadian_kelvin_routes(config)
        validate_circadian_groups(config)
    except Exception as err:
        _LOGGER.error("area_lighting reload: invalid config: %s", err)
        raise HomeAssistantError(f"area_lighting reload failed: {err}") from err
    return config


async def _async_reload(hass: HomeAssistant) -> None:
    """Apply the YAML config to the running areas without a restart."""
    from .event_handlers import (
        async_validate_external_entities,
        async_validate_lutron_remotes,
    )

    new_config = await _async_read_config(hass)
    data = hass.data[DOMAIN]
    # Let a running _on_started finish adding entities, so none is added twice.
    if (startup_task := data.get("startup_task")) is not None:
        await asyncio.wait({startup_task})
    controllers: dict[str, AreaLightingController] = data["controllers"]
    new_areas = {area.id: area for area in new_config.enabled_areas}
    kept = [ctrl for area_id, ctrl in controllers.items() if area_id in new_areas]

    # Nothing from here to the new listeners may await, or a sensor that
    # changes in between goes unheard.
    for unsub in data["unsubs"]:
        unsub()
    data["circadian_clock"].async_stop()
    clock = CircadianClock(hass, new_config.circadian)
    data["circadian_clock"] = clock
    clock.async_start()

    for area_id in controllers.keys() - new_areas.keys():
        controllers.pop(area_id).shutdown(persist=True)
    for ctrl in kept:
        ctrl.apply_config(new_areas[ctrl.area.id], new_config)
    added = [
        AreaLightingController(hass, area, new_config)
        for area in new_config.enabled_areas
        if area.id not in controllers
    ]
    for ctrl in added:
        ctrl.load_persisted_state(data["state_storage"].get_area_state(ctrl.area.id))
        controllers[ctrl.area.id] = ctrl
    _wire_leaders(controllers)
    data["config"] = new_config
    data["unsubs"] = await async_setup_event_handlers(hass, new_config)

    await _remove_stale_entities(hass)
    _refresh_entities(hass)
    # Before Home Assistant has started, _on_started adds every entity and
    # starts the added areas.
    started = data["unsub_started"] is None
    if started:
        await _register_scene_entities(hass, new_config.enabled_areas)
        await _register_helper_entities(hass, added)
    for ctrl in kept:
        await ctrl.async_resume_circadian()
    if started:
        for ctrl in added:
            await ctrl.restore_timers()
            ctrl.reconcile_startup_state()
            await ctrl.async_resume_circadian()
    # Tick once the areas have their new config, so no light gets the old
    # group values. The tick skips the lights that resuming areas just sent.
    await clock.async_tick()

    await async_validate_external_entities(hass, new_config)
    await async_validate_lutron_remotes(hass, new_config)
    _LOGGER.info(
        "area_lighting: configuration reloaded (%d areas, %d alert patterns)",
        len(new_config.enabled_areas),
        len(new_config.alert_patterns),
    )


async def _remove_stale_entities(hass: HomeAssistant) -> None:
    """Remove the entities of areas and scenes the config no longer has.

    Their registry entries stay, so an area or scene added back keeps the
    user's customizations.
    """
    controllers: dict[str, AreaLightingController] = hass.data[DOMAIN]["controllers"]
    record: dict[str, list[Entity]] = hass.data[DOMAIN]["entities"]
    for key in list(record):
        if key == GLOBAL_ENTITIES:
            continue
        if key not in controllers:
            stale = record.pop(key)
        else:
            slugs = controllers[key].area.scene_slugs
            stale = [
                e
                for e in record[key]
                if isinstance(e, AreaLightingScene) and e._scene_cfg.slug not in slugs
            ]
            record[key] = [e for e in record[key] if e not in stale]
        for entity in stale:
            await entity.async_remove()


def _refresh_entities(hass: HomeAssistant) -> None:
    """Have every recorded entity re-read its config-derived attributes."""
    controllers: dict[str, AreaLightingController] = hass.data[DOMAIN]["controllers"]
    for key, entities in hass.data[DOMAIN]["entities"].items():
        for entity in entities:
            if isinstance(entity, AreaLightingDiagnosticSensor):
                entity.sync_controller_listeners()
            elif key == GLOBAL_ENTITIES:
                continue
            elif isinstance(entity, AreaLightingScene):
                area = controllers[key].area
                scene_cfg = next(s for s in area.scenes if s.slug == entity._scene_cfg.slug)
                entity.refresh_from_config(area, scene_cfg)
            elif isinstance(entity, BehavioralScene):
                area = controllers[key].area
                entity.refresh_from_config(area, _behavioral_scene_name(area, entity._slug))
            else:
                entity.refresh_from_config()


def _behavioral_scene_name(area: AreaConfig, slug: str) -> str:
    """The configured name of a behavioral scene, or one made from its slug."""
    return next((s.name for s in area.scenes if s.slug == slug), slug.title())


async def async_teardown(hass: HomeAssistant) -> None:
    """Stop everything async_setup started. Safe to call more than once."""
    data = hass.data.get(DOMAIN)
    if data is None:
        return
    if (unsub_started := data.get("unsub_started")) is not None:
        data["unsub_started"] = None
        unsub_started()
    if (startup_task := data.pop("startup_task", None)) is not None:
        startup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await startup_task
    for unsub in data["unsubs"]:
        unsub()
    data["unsubs"].clear()
    data["circadian_clock"].async_stop()
    for entities in data["entities"].values():
        for entity in entities:
            await entity.async_remove()
    data["entities"].clear()
    for ctrl in data["controllers"].values():
        ctrl.shutdown()


async def _register_scene_entities(hass: HomeAssistant, areas: list[AreaConfig]) -> None:
    """Register the scene entities of `areas` that are not registered yet,
    directly with HA's scene EntityComponent.

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

    scene_storage: SceneStorage = hass.data[DOMAIN]["scene_storage"]
    entities: list = []
    for area in areas:
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
            name = _behavioral_scene_name(area, slug)
            entities.append(BehavioralScene(hass, area, slug, name, method))

    # Helpers share the scenes' unique-id pattern, so compare scenes only.
    registered = {
        e.unique_id
        for recorded in hass.data[DOMAIN]["entities"].values()
        for e in recorded
        if isinstance(e, AreaLightingScene | BehavioralScene)
    }
    entities = [e for e in entities if e.unique_id not in registered]
    if not entities:
        return

    await component.async_add_entities(entities)
    _record_entities(hass, entities)
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


async def _register_helper_entities(
    hass: HomeAssistant,
    controllers: list[AreaLightingController],
    toggles: GlobalToggles | None = None,
) -> None:
    """Register switch/select/number helper entities for each controller,
    and the global switches when `toggles` is given."""
    from homeassistant.components.number import (  # type: ignore[attr-defined]
        DATA_COMPONENT as NUMBER_COMPONENT,
    )
    from homeassistant.components.select import (  # type: ignore[attr-defined]
        DATA_COMPONENT as SELECT_COMPONENT,
    )
    from homeassistant.components.switch import DATA_COMPONENT as SWITCH_COMPONENT

    if not controllers and toggles is None:
        return

    switch_component = hass.data.get(SWITCH_COMPONENT)
    select_component = hass.data.get(SELECT_COMPONENT)
    number_component = hass.data.get(NUMBER_COMPONENT)

    switches: list = []
    selects: list = []
    numbers: list = []
    binary_sensors: list = []

    for ctrl in controllers:
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

    if toggles is not None:
        for flag, name, icon, uid, eid in GLOBAL_SWITCH_DEFS:
            switches.append(AreaLightingGlobalSwitch(toggles, flag, name, icon, uid, eid))

    if switch_component is not None and switches:
        await switch_component.async_add_entities(switches)
        _record_entities(hass, switches)
        _LOGGER.info("Registered %d switch entities", len(switches))
    elif switches:
        _LOGGER.warning("Switch component not loaded; skipping %d switches", len(switches))

    if select_component is not None and selects:
        await select_component.async_add_entities(selects)
        _record_entities(hass, selects)
        _LOGGER.info("Registered %d select entities", len(selects))
    elif selects:
        _LOGGER.warning("Select component not loaded; skipping %d selects", len(selects))

    if number_component is not None and numbers:
        await number_component.async_add_entities(numbers)
        _record_entities(hass, numbers)
        _LOGGER.info("Registered %d number entities", len(numbers))
    elif numbers:
        _LOGGER.warning("Number component not loaded; skipping %d numbers", len(numbers))

    from homeassistant.components.binary_sensor import (
        DATA_COMPONENT as BINARY_SENSOR_COMPONENT,
    )

    bs_component = hass.data.get(BINARY_SENSOR_COMPONENT)
    if bs_component is not None and binary_sensors:
        await bs_component.async_add_entities(binary_sensors)
        _record_entities(hass, binary_sensors)
        _LOGGER.info("Registered %d binary_sensor entities", len(binary_sensors))
    elif binary_sensors:
        _LOGGER.warning(
            "Binary sensor component not loaded; skipping %d binary sensors",
            len(binary_sensors),
        )

    # Assign every helper entity to the matching HA area. They register
    # hidden, so auto-generated dashboards show only the ones a user
    # makes visible.
    await _assign_entities_to_ha_areas(
        hass, controllers, switches, selects, numbers, binary_sensors
    )


async def _assign_entities_to_ha_areas(
    hass: HomeAssistant,
    controllers: list[AreaLightingController],
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
    requires a real ConfigEntry ID we don't have, so HA areas do the
    grouping instead.
    """
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er

    area_reg = ar.async_get(hass)
    entity_reg = er.async_get(hass)

    for ctrl in controllers:
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


def _apply_hidden_default(hass: HomeAssistant) -> None:
    """Hide the entities an earlier release registered visible.

    Every entity except the global switches sets
    entity_registry_visible_default = False, but Home Assistant applies
    that only when it first creates a registry entry. Each entry is
    handled once and marked in its registry options, which are saved in
    the same registry write as hidden_by, so an entity the user un-hides
    afterwards stays visible.
    """
    from homeassistant.helpers import entity_registry as er

    entity_reg = er.async_get(hass)
    global_switch_ids = {uid for _flag, _name, _icon, uid, _eid in GLOBAL_SWITCH_DEFS}
    hidden = 0
    for entry in list(entity_reg.entities.values()):
        if (
            entry.domain not in ENTITY_DOMAINS
            # Entities added straight to a domain's EntityComponent get a
            # platform named after the domain.
            or entry.platform != entry.domain
            or not entry.unique_id.startswith("area_lighting_")
            or (entry.domain == "switch" and entry.unique_id in global_switch_ids)
            or HIDDEN_DEFAULT_APPLIED in entry.options.get(DOMAIN, {})
        ):
            continue
        if entry.hidden_by is None:
            entity_reg.async_update_entity(
                entry.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
            )
            hidden += 1
        entity_reg.async_update_entity_options(
            entry.entity_id, DOMAIN, {HIDDEN_DEFAULT_APPLIED: True}
        )
    if hidden:
        _LOGGER.info("Hid %d entities registered by an earlier release", hidden)


async def _register_sensors(hass: HomeAssistant) -> None:
    """Register the diagnostic sensor once Home Assistant has started."""
    if await _add_sensors(hass, [AreaLightingDiagnosticSensor(hass)]):
        _LOGGER.info("Registered diagnostic sensor")


async def _register_circadian_sensor(hass: HomeAssistant) -> None:
    """Register the circadian sensor during setup, so automations whose
    triggers attach at startup can find it."""
    if await _add_sensors(hass, [AreaLightingCircadianSensor(hass)]):
        _LOGGER.info("Registered circadian sensor")


async def _add_sensors(hass: HomeAssistant, sensors: Sequence[Entity]) -> bool:
    """Add sensors via the sensor EntityComponent."""
    from homeassistant.components.sensor import (  # type: ignore[attr-defined]
        DATA_COMPONENT as SENSOR_DATA_COMPONENT,
    )

    component = hass.data.get(SENSOR_DATA_COMPONENT)
    if component is None:
        _LOGGER.error("Sensor component not loaded; cannot register sensors")
        return False

    await component.async_add_entities(sensors)
    _record_entities(hass, sensors)
    return True


def _record_entities(hass: HomeAssistant, entities: Sequence[Entity]) -> None:
    """Record the entities a component added in hass.data, by area id."""
    record: dict[str, list[Entity]] = hass.data[DOMAIN]["entities"]
    for entity in entities:
        # A failed add leaves the entity without a platform.
        if entity.platform is None:
            continue
        if isinstance(entity, AreaLightingScene | BehavioralScene):
            key = entity._area.id
        elif (ctrl := getattr(entity, "_controller", None)) is not None:
            key = ctrl.area.id
        else:
            key = GLOBAL_ENTITIES
        record.setdefault(key, []).append(entity)
