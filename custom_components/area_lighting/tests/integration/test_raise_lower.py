"""Scene-relative raise/lower tests (D2, D3)."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _light_turn_on_calls(service_calls: list) -> list:
    return [c for c in service_calls if c.domain == "light" and c.service == "turn_on"]


@pytest.mark.integration
async def test_lighting_lower_from_off_is_noop(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    service_calls.clear()
    await ctrl.lighting_lower()
    assert not _light_turn_on_calls(service_calls)
    assert ctrl._state.is_off


@pytest.mark.integration
async def test_lighting_raise_from_scene_steps_on_lights(
    hass: HomeAssistant,
    helper_entities,
    network_room_config,
    service_calls,
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 128})
    hass.states.async_set("light.network_room_overhead_2", "on", {"brightness": 128})
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    service_calls.clear()
    await ctrl.lighting_raise()

    on_calls = _light_turn_on_calls(service_calls)
    stepped_ids = {
        c.data.get("entity_id") for c in on_calls if c.data.get("brightness_step_pct", 0) > 0
    }
    assert "light.network_room_overhead_1" in stepped_ids
    assert "light.network_room_overhead_2" in stepped_ids
    assert ctrl._state.dimmed
    assert ctrl._state.previous_scene == "evening"


@pytest.mark.integration
async def test_lighting_raise_only_dims_currently_on_lights(
    hass: HomeAssistant,
    helper_entities,
    network_room_config,
    service_calls,
) -> None:
    """One light on, one off → only the on light gets stepped."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 200})
    hass.states.async_set("light.network_room_overhead_2", "off", {})
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    service_calls.clear()
    await ctrl.lighting_raise()

    stepped_ids = {
        c.data.get("entity_id")
        for c in _light_turn_on_calls(service_calls)
        if c.data.get("brightness_step_pct", 0) != 0
    }
    assert stepped_ids == {"light.network_room_overhead_1"}


@pytest.mark.integration
async def test_lighting_raise_from_off_with_previous_scene_restores_it(
    hass: HomeAssistant,
    helper_entities,
    network_room_config,
    service_calls,
) -> None:
    """From off with a remembered previous_scene, raise restores that scene."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    await ctrl.lighting_lower()  # dims evening, previous_scene=evening
    ctrl._state.transition_to_off(ActivationSource.USER)
    ctrl._state.previous_scene = "evening"  # simulate remembered state
    service_calls.clear()
    await ctrl.lighting_raise()
    assert ctrl._state.scene_slug == "evening"
    assert ctrl._state.dimmed


@pytest.mark.integration
async def test_lighting_raise_disables_circadian_switches(
    hass: HomeAssistant,
    helper_entities,
    network_room_config,
) -> None:
    """When raising from circadian, the circadian switches get disabled.

    Spies on _disable_circadian_switches directly because the HA target
    resolver rejects switch service calls against unregistered entity IDs
    before they can be captured by async_mock_service.
    """
    from unittest.mock import AsyncMock

    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 150})
    ctrl._state.transition_to_circadian(ActivationSource.USER)
    spy = AsyncMock(wraps=ctrl._disable_circadian_switches)
    ctrl._disable_circadian_switches = spy
    await ctrl.lighting_raise()
    assert spy.await_count >= 1


@pytest.mark.integration
async def test_brightness_step_pct_respects_area_override(
    hass: HomeAssistant,
    helper_entities,
) -> None:
    """An area with brightness_step_pct: 25 uses 25; default area uses default."""
    from custom_components.area_lighting.const import BRIGHTNESS_STEP_DEFAULT

    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "override_area",
                    "name": "Override Area",
                    "event_handlers": False,
                    "brightness_step_pct": 25,
                    "lights": [{"id": "light.override_area_a", "roles": ["color"]}],
                    "scenes": [{"id": "circadian", "name": "Circadian"}],
                },
                {
                    "id": "default_area",
                    "name": "Default Area",
                    "event_handlers": False,
                    "lights": [{"id": "light.default_area_a", "roles": ["color"]}],
                    "scenes": [{"id": "circadian", "name": "Circadian"}],
                },
            ]
        }
    }
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    ctrls = hass.data["area_lighting"]["controllers"]
    assert ctrls["override_area"]._brightness_step_pct() == 25
    assert ctrls["default_area"]._brightness_step_pct() == BRIGHTNESS_STEP_DEFAULT
