"""Round-trip tests for the StateStorage reserved global-state key."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.area_lighting.state_storage import StateStorage


@pytest.mark.integration
async def test_global_state_roundtrips(hass: HomeAssistant) -> None:
    s1 = StateStorage(hass)
    await s1.async_load()
    assert s1.get_global_state() == {}

    await s1.async_save_global_state(
        {"motion_lights_enabled": False, "occupancy_timeout_enabled": True}
    )

    s2 = StateStorage(hass)
    await s2.async_load()
    assert s2.get_global_state() == {
        "motion_lights_enabled": False,
        "occupancy_timeout_enabled": True,
    }


@pytest.mark.integration
async def test_global_key_does_not_shadow_area_state(hass: HomeAssistant) -> None:
    s = StateStorage(hass)
    await s.async_load()
    await s.async_save_area_state("living_room", {"current_scene": "evening"})
    await s.async_save_global_state({"motion_lights_enabled": False})

    assert s.get_area_state("living_room") == {"current_scene": "evening"}
    assert s.get_global_state() == {"motion_lights_enabled": False}
