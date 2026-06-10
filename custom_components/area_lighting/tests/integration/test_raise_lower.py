"""Scene-relative raise/lower tests (D2, D3)."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.const import BRIGHTNESS_STEP_DEFAULT

# Absolute brightness (0-255) a light is set to when a dark area is brought up
# to its minimum dimming level (brightness_step_pct). Mirrors the controller's
# _set_all_lights_to_pct conversion.
MIN_BRIGHTNESS = max(1, min(255, round(255 * BRIGHTNESS_STEP_DEFAULT / 100)))


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _light_turn_on_calls(service_calls: list) -> list:
    return [c for c in service_calls if c.domain == "light" and c.service == "turn_on"]


def _ids_set_to_min(service_calls: list) -> set[str]:
    """Entity IDs that received a turn_on at the minimum (step) brightness."""
    return {
        c.data.get("entity_id")
        for c in _light_turn_on_calls(service_calls)
        if c.data.get("brightness") == MIN_BRIGHTNESS
    }


@pytest.mark.integration
async def test_lighting_lower_from_dark_brings_all_lights_to_min(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """With no lights on, lower (like raise) brings every area light to min."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("light.network_room_overhead_1", "off", {})
    hass.states.async_set("light.network_room_overhead_2", "off", {})
    service_calls.clear()
    await ctrl.lighting_lower()
    assert _ids_set_to_min(service_calls) == {
        "light.network_room_overhead_1",
        "light.network_room_overhead_2",
    }
    assert ctrl._state.dimmed


@pytest.mark.integration
async def test_lighting_raise_from_dark_brings_all_lights_to_min(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """With no lights on, raise brings every area light to min."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("light.network_room_overhead_1", "off", {})
    hass.states.async_set("light.network_room_overhead_2", "off", {})
    service_calls.clear()
    await ctrl.lighting_raise()
    assert _ids_set_to_min(service_calls) == {
        "light.network_room_overhead_1",
        "light.network_room_overhead_2",
    }
    assert ctrl._state.dimmed


@pytest.mark.integration
async def test_lighting_lower_only_dims_currently_on_lights(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """One light on, one off → lower only steps the on light (none brought up)."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 200})
    hass.states.async_set("light.network_room_overhead_2", "off", {})
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    service_calls.clear()
    await ctrl.lighting_lower()

    stepped_ids = {
        c.data.get("entity_id")
        for c in _light_turn_on_calls(service_calls)
        if c.data.get("brightness_step_pct", 0) != 0
    }
    assert stepped_ids == {"light.network_room_overhead_1"}
    # The off light is left off, not brought up to min.
    assert "light.network_room_overhead_2" not in _ids_set_to_min(service_calls)


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


@pytest.fixture
def study_config() -> dict:
    """Area whose accent light only participates in the `evening` scene."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "study",
                    "name": "Study",
                    "event_handlers": False,
                    "lights": [
                        {"id": "light.study_main", "roles": ["color"]},
                        {
                            "id": "light.study_accent",
                            "roles": ["color"],
                            "scenes": ["evening"],
                        },
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                    ],
                }
            ]
        }
    }


@pytest.mark.integration
async def test_raise_from_dark_brings_non_scene_lights_to_min(
    hass: HomeAssistant, helper_entities, study_config, service_calls
) -> None:
    """A dark area lights up uniformly: lights outside the restored scene
    are still brought to the minimum dimming level."""
    await _setup(hass, study_config)
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    hass.states.async_set("light.study_main", "off", {})
    hass.states.async_set("light.study_accent", "off", {})
    # Area nominally in `daylight`, which excludes the accent light, but
    # physically dark.
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    service_calls.clear()
    await ctrl.lighting_raise()

    # Both the scene member and the excluded accent reach min brightness.
    assert _ids_set_to_min(service_calls) == {
        "light.study_main",
        "light.study_accent",
    }
