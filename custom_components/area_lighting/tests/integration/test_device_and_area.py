"""HA area assignment for auto-generated dashboards.

Every entity the component creates for an area must be assigned to the
matching HA area in the area registry so the auto-generated area
dashboard (Settings → Areas → <Area> → Create dashboard) includes them.

Note: device registration (Settings → Devices) would also be nice, but
requires the component to be a ConfigEntry-based integration — today
we load via YAML which has no config_entry_id to anchor a device.
Tracked as a stretch goal.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _area_entity_ids(hass: HomeAssistant, area_id: str) -> list[str]:
    """Entity IDs the component created for the given area."""
    entities = hass.states.async_entity_ids()
    prefix_suffixes = [
        ("switch", area_id),
        ("select", area_id),
        ("number", area_id),
        ("scene", area_id),
    ]
    result = []
    for eid in entities:
        domain, local = eid.split(".", 1)
        for d, a in prefix_suffixes:
            if domain == d and local.startswith(f"{a}_"):
                result.append(eid)
                break
    return result


@pytest.mark.integration
async def test_all_area_entities_assigned_to_ha_area(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Every entity the component created must be assigned to the HA area
    matching the area's name so HA's area dashboard includes them."""
    area_reg = ar.async_get(hass)
    # Pre-create the HA area so the component's registration can find it
    ha_area = area_reg.async_get_or_create("Network Room")

    await _setup(hass, network_room_config)

    entity_reg = er.async_get(hass)
    for entity_id in _area_entity_ids(hass, "network_room"):
        entry = entity_reg.async_get(entity_id)
        assert entry is not None
        assert entry.area_id == ha_area.id, (
            f"{entity_id} not assigned to HA area (got area_id={entry.area_id!r}, "
            f"expected {ha_area.id!r})"
        )


@pytest.mark.integration
async def test_ha_area_auto_created_if_missing(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """If the HA area doesn't exist yet, setup should create it so fresh
    installs pick up the assignment automatically."""
    await _setup(hass, network_room_config)

    area_reg = ar.async_get(hass)
    ha_area = area_reg.async_get_area_by_name("Network Room")
    assert ha_area is not None, "expected setup to create the HA area 'Network Room'"
