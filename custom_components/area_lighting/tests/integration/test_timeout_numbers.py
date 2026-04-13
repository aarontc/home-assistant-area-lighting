"""Number entities for motion/occupancy timeout durations.

Four new number entities per area let users tune the timeouts from the
HA UI without editing YAML:

- number.{area}_motion_timeout_minutes
- number.{area}_motion_night_timeout_minutes
- number.{area}_occupancy_timeout_minutes
- number.{area}_occupancy_night_timeout_minutes

Values persist across restart and take effect on the next timer start.
"""

from __future__ import annotations

from datetime import UTC

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


EXPECTED_NUMBER_IDS = {
    "number.network_room_motion_timeout_minutes",
    "number.network_room_motion_night_timeout_minutes",
    "number.network_room_occupancy_timeout_minutes",
    "number.network_room_occupancy_night_timeout_minutes",
}


@pytest.mark.integration
async def test_four_timeout_numbers_registered(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """All four timeout number entities exist after setup."""
    await _setup(hass, network_room_config)
    for entity_id in EXPECTED_NUMBER_IDS:
        assert hass.states.get(entity_id) is not None, (
            f"expected {entity_id} to exist; got entities: "
            + ", ".join(
                sorted(
                    e
                    for e in hass.states.async_entity_ids("number")
                    if e.startswith("number.network_room_")
                )
            )
        )


@pytest.mark.integration
async def test_timeout_numbers_default_to_config_values(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Initial values come from the area's timer_durations config.

    network_room_config has:
        motion_light_timer_durations: {off: 00:08:00, night_off: 00:05:00}
        occupancy_light_timer_durations: {off: 00:30:00}  # no night_off
    """
    await _setup(hass, network_room_config)
    assert float(hass.states.get("number.network_room_motion_timeout_minutes").state) == 8.0
    assert float(hass.states.get("number.network_room_motion_night_timeout_minutes").state) == 5.0
    assert float(hass.states.get("number.network_room_occupancy_timeout_minutes").state) == 30.0
    # No night_off configured → falls back to the normal off value
    assert (
        float(hass.states.get("number.network_room_occupancy_night_timeout_minutes").state) == 30.0
    )


@pytest.mark.integration
async def test_setting_motion_timeout_affects_next_timer_start(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Changing the number entity's value must affect the duration of
    the NEXT motion timer started (not a currently-running one)."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    # Set motion timeout to 15 minutes via the number entity service
    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": "number.network_room_motion_timeout_minutes",
            "value": 15,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    ctrl._state.transition_to_scene("daylight", ActivationSource.MOTION)
    await ctrl.handle_motion_off()
    assert ctrl._motion_timer.is_active
    # Deadline should be ~15 minutes = 900 seconds in the future
    from datetime import datetime

    deadline = ctrl._motion_timer.deadline_utc
    assert deadline is not None
    remaining = (deadline - datetime.now(UTC)).total_seconds()
    assert 890 < remaining < 910, f"expected ~900s remaining, got {remaining}"


@pytest.mark.integration
async def test_setting_occupancy_night_timeout_affects_night_mode_timer(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Night-mode occupancy timer uses the night_timeout number value."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": "number.network_room_occupancy_night_timeout_minutes",
            "value": 10,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    ctrl.night_mode = True
    assert ctrl._occupancy_off_duration() == 600.0  # 10 minutes


@pytest.mark.integration
async def test_setting_motion_night_timeout_picks_night_timer(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Night-mode motion timer uses the motion_night_timeout number value."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": "number.network_room_motion_night_timeout_minutes",
            "value": 3,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    ctrl.night_mode = True
    ctrl._state.transition_to_scene("night", ActivationSource.MOTION)
    await ctrl.handle_motion_off()
    assert ctrl._motion_night_timer.is_active

    from datetime import datetime

    deadline = ctrl._motion_night_timer.deadline_utc
    assert deadline is not None
    remaining = (deadline - datetime.now(UTC)).total_seconds()
    assert 170 < remaining < 190, f"expected ~180s remaining (3 minutes), got {remaining}"


@pytest.mark.integration
async def test_timeout_values_persist_across_restart(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Values set via the number entities must round-trip through state_dict."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": "number.network_room_motion_timeout_minutes",
            "value": 12,
        },
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": "number.network_room_occupancy_timeout_minutes",
            "value": 45,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    saved = ctrl.state_dict()
    # Motion: 12 minutes = 720 seconds
    assert saved.get("motion_off_duration_seconds") == 720.0
    # Occupancy: 45 minutes = 2700 seconds
    assert saved.get("occupancy_off_duration_seconds") == 2700.0

    # Load into a fresh controller and verify values come back
    from custom_components.area_lighting.controller import AreaLightingController

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    fresh.load_persisted_state(saved)
    assert fresh.motion_off_duration_seconds == 720.0
    assert fresh.occupancy_off_duration_seconds == 2700.0
