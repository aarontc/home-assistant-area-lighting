"""Startup validation for configured Lutron remote device ids."""

from __future__ import annotations

import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.event_handlers import async_validate_lutron_remotes
from custom_components.area_lighting.models import (
    AreaConfig,
    AreaLightingConfig,
    LutronRemoteConfig,
)


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, DOMAIN, cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _register_remote(hass: HomeAssistant) -> str:
    entry = MockConfigEntry(domain="lutron_caseta")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("lutron_caseta", "x")},
    )
    return device.id


@pytest.mark.integration
async def test_missing_remotes_create_issue_and_single_warning(
    hass: HomeAssistant, helper_entities, network_room_config, caplog: pytest.LogCaptureFixture
) -> None:
    network_room_config[DOMAIN]["areas"][0]["lutron_remotes"] = [
        {"id": "missing_entry", "name": "Entry Remote"},
        {"id": "missing_desk", "name": "Desk Remote"},
    ]
    caplog.set_level(logging.WARNING)
    await _setup(hass, network_room_config)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, "lutron_remotes_missing")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.is_persistent is False
    assert issue.translation_key == "lutron_remotes_missing"
    assert issue.translation_placeholders == {
        "count": "2",
        "remote_list": (
            "  - Network Room: Entry Remote (missing_entry)\n"
            "  - Network Room: Desk Remote (missing_desk)"
        ),
    }
    warnings = [
        record.message
        for record in caplog.records
        if record.levelno == logging.WARNING and "Lutron remotes are missing" in record.message
    ]
    assert len(warnings) == 1
    assert "Network Room: Entry Remote (missing_entry)" in warnings[0]
    assert "Network Room: Desk Remote (missing_desk)" in warnings[0]
    assert "button presses are being ignored" in warnings[0]


@pytest.mark.integration
async def test_registered_remote_creates_no_issue(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    network_room_config[DOMAIN]["areas"][0]["lutron_remotes"] = [
        {"id": _register_remote(hass), "name": "Entry Remote"},
    ]
    await _setup(hass, network_room_config)

    assert ir.async_get(hass).async_get_issue(DOMAIN, "lutron_remotes_missing") is None
    assert await async_validate_lutron_remotes(hass, hass.data[DOMAIN]["config"]) == []


@pytest.mark.integration
async def test_correcting_remote_id_clears_issue(hass: HomeAssistant) -> None:
    remote = LutronRemoteConfig(id="old_device", name="Entry Remote")
    config = AreaLightingConfig(
        areas=[AreaConfig(id="network_room", name="Network Room", lutron_remotes=[remote])]
    )
    assert await async_validate_lutron_remotes(hass, config) == ["old_device"]
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, "lutron_remotes_missing") is not None

    remote.id = _register_remote(hass)
    assert await async_validate_lutron_remotes(hass, config) == []
    assert registry.async_get_issue(DOMAIN, "lutron_remotes_missing") is None


@pytest.mark.integration
@pytest.mark.parametrize("disabled_field", ["enabled", "event_handlers"])
async def test_remote_validation_ignores_disabled_areas(
    hass: HomeAssistant, disabled_field: str
) -> None:
    area = AreaConfig(
        id="network_room",
        name="Network Room",
        lutron_remotes=[LutronRemoteConfig(id="missing_device", name="Entry Remote")],
    )
    setattr(area, disabled_field, False)

    assert await async_validate_lutron_remotes(hass, AreaLightingConfig(areas=[area])) == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, "lutron_remotes_missing") is None
