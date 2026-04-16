"""Canary tests for area_lighting logging coverage and conventions.

These are deliberately thin: they guard against wholesale regressions
(logs silently disappearing) and against the `Area {area_id}:` prefix
convention regressing on the service-call log. They do NOT assert
exact wording of any line — that is documentation, not behavior.
"""

from __future__ import annotations

import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource

LOGGER_NAME = "custom_components.area_lighting"


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_debug_logs_emit_during_motion_event(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    helper_entities,
    network_room_config,
) -> None:
    """End-to-end motion event should produce >=1 DEBUG record from the
    component that mentions the area id. Guards against logging silently
    disappearing.
    """
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    # Trigger a motion-on event through the controller directly
    # (the sensor-event plumbing is covered elsewhere).
    await ctrl.handle_motion_on()
    await hass.async_block_till_done()

    records = [
        r for r in caplog.records if r.name.startswith(LOGGER_NAME) and r.levelno == logging.DEBUG
    ]
    assert records, "expected at least one DEBUG record from area_lighting"

    area_mentions = [r for r in records if "network_room" in r.getMessage()]
    assert area_mentions, (
        f"expected at least one DEBUG record to mention 'network_room'; "
        f"got {[r.getMessage() for r in records]}"
    )


@pytest.mark.integration
async def test_service_call_log_has_area_prefix(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    helper_entities,
    service_calls,
    network_room_config,
) -> None:
    """A service call triggered by the controller should log a line that
    starts with `Area {area_id}:` and names the HA service. Guards against
    the `Area X:` prefix convention regressing on the highest-traffic log.
    """
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    # Activate circadian, which fans out light.turn_on / switch.turn_on calls.
    await ctrl.lighting_circadian(ActivationSource.USER)
    await hass.async_block_till_done()

    service_logs = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith(LOGGER_NAME)
        and r.levelno == logging.DEBUG
        and "service_call" in r.getMessage()
    ]
    assert service_logs, (
        f"expected at least one DEBUG service_call log; "
        f"got messages: {[r.getMessage() for r in caplog.records if r.name.startswith(LOGGER_NAME)]}"
    )
    # Every service_call log must follow the Area {id}: convention.
    for msg in service_logs:
        assert msg.startswith("Area network_room:"), (
            f"service_call log missing 'Area X:' prefix: {msg!r}"
        )


@pytest.mark.integration
async def test_unknown_lutron_button_logs_at_info(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    helper_entities,
    network_room_config,
) -> None:
    """Unknown Lutron button_type should log at INFO (not DEBUG) so it
    surfaces without requiring debug mode to be on. Regression guard for
    the one level promotion in this work.
    """
    # Augment the fixture config with a remote attached to network_room.
    cfg = network_room_config
    cfg["area_lighting"]["areas"][0]["lutron_remotes"] = [
        {"id": "test_remote_device", "name": "Test Remote"},
    ]

    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    await _setup(hass, cfg)

    hass.bus.async_fire(
        "lutron_caseta_button_event",
        {
            "serial": "12345",
            "type": "SunnataDimmer",
            "button_number": 1,
            "leap_button_number": 0,
            "device_name": "Test Remote",
            "device_id": "test_remote_device",
            "area_name": "Network Room",
            "button_type": "nonsense_button",
            "action": "press",
        },
    )
    await hass.async_block_till_done()

    info_logs = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith(LOGGER_NAME)
        and r.levelno == logging.INFO
        and "nonsense_button" in r.getMessage()
    ]
    assert info_logs, (
        f"expected INFO log about unknown button_type 'nonsense_button'; "
        f"got: {[(r.levelname, r.getMessage()) for r in caplog.records if r.name.startswith(LOGGER_NAME)]}"
    )
