"""The component's entities register hidden, off auto-generated dashboards.

Every area gets a dozen or more scenes, switches, numbers, a select and
a binary sensor. Home Assistant's auto-generated dashboards skip
entities whose registry entry has `hidden_by` set, so these and the
diagnostics sensor register hidden and users un-hide the ones they want.
The global master switches stay visible.

Home Assistant applies the hidden default only when it first creates a
registry entry, so startup also hides entries an earlier release
registered visible, once per entry.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.area_lighting import HIDDEN_DEFAULT_APPLIED
from custom_components.area_lighting.const import DOMAIN

# Everything network_room_config creates. `off` is a behavioral scene
# registered for every area whether or not the config declares it.
NETWORK_ROOM_ENTITY_IDS = {
    "binary_sensor.network_room_occupied",
    "number.network_room_manual_fadeout_seconds",
    "number.network_room_motion_fadeout_seconds",
    "number.network_room_motion_night_timeout_minutes",
    "number.network_room_motion_timeout_minutes",
    "number.network_room_occupancy_night_timeout_minutes",
    "number.network_room_occupancy_timeout_minutes",
    "scene.network_room_ambient",
    "scene.network_room_christmas",
    "scene.network_room_circadian",
    "scene.network_room_daylight",
    "scene.network_room_evening",
    "scene.network_room_night",
    "scene.network_room_off",
    "select.network_room_last_scene",
    "switch.network_room_ambience_enabled",
    "switch.network_room_motion_light_enabled",
    "switch.network_room_motion_override_ambient",
    "switch.network_room_night_mode",
    "switch.network_room_occupancy_timeout_enabled",
}

GLOBAL_SWITCH_ENTITY_IDS = (
    "switch.area_lighting_motion_lights_enabled",
    "switch.area_lighting_occupancy_timeout_enabled",
    "switch.area_lighting_demand_response_active",
)


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


async def _setup_without_migration(hass: HomeAssistant, cfg: dict) -> None:
    """Set up with the startup migration disabled, so a hidden entry can
    only come from the entity's own hidden default."""
    with patch("custom_components.area_lighting._apply_hidden_default"):
        await _setup(hass, cfg)


def _preregister(hass: HomeAssistant, domain: str, platform: str, unique_id: str) -> str:
    """A visible registry entry, as an earlier release (or another
    integration) left it."""
    entity_reg = er.async_get(hass)
    return entity_reg.async_get_or_create(domain, platform, unique_id).entity_id


def _hidden_by(hass: HomeAssistant, entity_id: str) -> er.RegistryEntryHider | None:
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None, entity_id
    return entry.hidden_by


@pytest.mark.integration
async def test_per_area_entities_register_hidden(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup_without_migration(hass, network_room_config)

    entity_reg = er.async_get(hass)
    area_entries = [
        entry
        for entry in entity_reg.entities.values()
        if entry.unique_id.startswith("area_lighting_network_room_")
    ]
    assert {entry.entity_id for entry in area_entries} == NETWORK_ROOM_ENTITY_IDS
    for entry in area_entries:
        assert entry.hidden_by is er.RegistryEntryHider.INTEGRATION, entry.entity_id


@pytest.mark.integration
async def test_diagnostics_sensor_registers_hidden(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup_without_migration(hass, network_room_config)

    assert _hidden_by(hass, "sensor.area_lighting_diagnostics") is er.RegistryEntryHider.INTEGRATION


@pytest.mark.integration
async def test_global_switches_stay_visible(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)

    for entity_id in GLOBAL_SWITCH_ENTITY_IDS:
        assert _hidden_by(hass, entity_id) is None, entity_id


@pytest.mark.integration
async def test_startup_marks_every_entry_it_handles(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)

    entity_reg = er.async_get(hass)
    for entity_id in (*NETWORK_ROOM_ENTITY_IDS, "sensor.area_lighting_diagnostics"):
        entry = entity_reg.async_get(entity_id)
        assert entry is not None
        assert entry.options.get(DOMAIN, {}).get(HIDDEN_DEFAULT_APPLIED), entity_id


@pytest.mark.integration
async def test_startup_hides_entries_from_an_earlier_release(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    night_mode = _preregister(hass, "switch", "switch", "area_lighting_network_room_night_mode")
    diagnostics = _preregister(hass, "sensor", "sensor", "area_lighting_diagnostics")
    global_switch = _preregister(
        hass, "switch", "switch", "area_lighting_global_motion_lights_enabled"
    )
    # A scene can share a global switch's unique id (an area with id
    # `global`); only the switch is exempt.
    lookalike_scene = _preregister(
        hass, "scene", "scene", "area_lighting_global_motion_lights_enabled"
    )
    other_integration = _preregister(hass, "switch", "template", "area_lighting_elsewhere")

    await _setup(hass, network_room_config)

    entry = er.async_get(hass).async_get(night_mode)
    assert entry is not None
    # The component adopted the pre-existing entry rather than creating its own.
    assert entry.original_name == "Network Room Night Mode"
    assert entry.hidden_by is er.RegistryEntryHider.INTEGRATION
    assert _hidden_by(hass, diagnostics) is er.RegistryEntryHider.INTEGRATION
    assert _hidden_by(hass, lookalike_scene) is er.RegistryEntryHider.INTEGRATION
    assert _hidden_by(hass, global_switch) is None
    assert _hidden_by(hass, other_integration) is None


@pytest.mark.integration
async def test_an_entry_is_handled_only_once(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Once an entry is marked, a user's un-hide sticks across restarts."""
    night_mode = _preregister(hass, "switch", "switch", "area_lighting_network_room_night_mode")
    er.async_get(hass).async_update_entity_options(
        night_mode, DOMAIN, {HIDDEN_DEFAULT_APPLIED: True}
    )

    await _setup(hass, network_room_config)

    entry = er.async_get(hass).async_get(night_mode)
    assert entry is not None
    assert entry.original_name == "Network Room Night Mode"
    assert entry.hidden_by is None
