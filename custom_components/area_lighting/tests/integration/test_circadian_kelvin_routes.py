"""Integration tests for circadian_kelvin_routes (router + controller wiring).

The kitchen fixture has one fluorescent (banded 4500-5500K) and three
lightstrips (fallback). Tests assert which entities `light.turn_on` and
`light.turn_off` get called for, in response to source state changes.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.fixture
def kitchen_with_routes_config() -> dict:
    """Kitchen with one fluorescent banded [4500, 5500] and 3 strips as fallback."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "event_handlers": True,
                    "circadian_switches": [
                        {"name": "Kitchen", "max_brightness": 100, "min_brightness": 20},
                    ],
                    "lights": [
                        {
                            "id": "light.kitchen_fluorescent",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_1",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_2",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_3",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                    "circadian_kelvin_routes": {
                        "crossfade_seconds": 1.0,
                        "routes": [
                            {
                                "kelvin_range": [4500, 5500],
                                "lights": ["light.kitchen_fluorescent"],
                            },
                            {
                                "lights": [
                                    "light.kitchen_strip_1",
                                    "light.kitchen_strip_2",
                                    "light.kitchen_strip_3",
                                ]
                            },
                        ],
                    },
                }
            ]
        }
    }


@pytest.fixture
def _stub_kitchen_entities(hass: HomeAssistant):
    """Pre-populate light + switch states the validator expects."""
    hass.states.async_set("light.kitchen_fluorescent", "off", {})
    hass.states.async_set("light.kitchen_strip_1", "off", {})
    hass.states.async_set("light.kitchen_strip_2", "off", {})
    hass.states.async_set("light.kitchen_strip_3", "off", {})
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "off",
        {"brightness": 75.0, "colortemp": 3000},
    )


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_entering_circadian_activates_route_for_current_colortemp(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
) -> None:
    """colortemp=5000 → fluorescent on, strips off."""
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 5000},
    )
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    on_targets = {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == "turn_on"
    }
    off_targets = {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == "turn_off"
    }
    assert "light.kitchen_fluorescent" in on_targets
    # Strips were already "off" in stubbed state, so no turn_off calls are needed.
    assert "light.kitchen_strip_1" not in off_targets
    assert "light.kitchen_strip_2" not in off_targets
    assert "light.kitchen_strip_3" not in off_targets


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_colortemp_change_swaps_active_route(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
) -> None:
    """Start at colortemp=5000 (fluorescent), then move to 3000 (strips)."""
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 5000},
    )
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    # Simulate the result of the turn_on call so the state-diff sees it as on.
    hass.states.async_set("light.kitchen_fluorescent", "on", {})

    service_calls.clear()
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 3000},
    )
    await hass.async_block_till_done()

    on_targets = {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == "turn_on"
    }
    off_targets = {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == "turn_off"
    }
    # Fluorescent is "on", so the state-diff issues a turn_off for it.
    assert "light.kitchen_fluorescent" in off_targets
    # Strips are still "off" from stub, so all three need turn_on.
    assert {
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    } <= on_targets


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_hysteresis_suppresses_flap_at_boundary(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
) -> None:
    """Once banded, small overshoots within hysteresis must not swap."""
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 5000},
    )
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    service_calls.clear()
    # Nudge to 5510 (within +25K) — still banded.
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 5510},
    )
    await hass.async_block_till_done()
    nudge_targets = {
        (call.domain, call.service, call.data.get("entity_id")) for call in service_calls
    }
    # No fluorescent off, no strip on
    assert ("light", "turn_off", "light.kitchen_fluorescent") not in nudge_targets
    assert ("light", "turn_on", "light.kitchen_strip_1") not in nudge_targets


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_source_unavailable_selects_fallback(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
) -> None:
    """If colortemp attribute is missing, fallback (strips) is selected."""
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "unavailable",
        {},
    )
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    on_targets = {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == "turn_on"
    }
    off_targets = {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == "turn_off"
    }
    assert {
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    } <= on_targets
    # Fluorescent was already "off" in stubbed state, so no turn_off call is needed.
    assert "light.kitchen_fluorescent" not in off_targets


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_listener_inactive_outside_circadian(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
) -> None:
    """colortemp changes while in 'off' must not trigger turn_on/off calls."""
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 5000},
    )
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    await ctrl.lighting_off()
    await hass.async_block_till_done()

    service_calls.clear()
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 3000},
    )
    await hass.async_block_till_done()

    # No light service calls fired as a result of the colortemp change.
    routed_targets = {
        "light.kitchen_fluorescent",
        "light.kitchen_strip_1",
        "light.kitchen_strip_2",
        "light.kitchen_strip_3",
    }
    triggered = [
        call
        for call in service_calls
        if call.domain == "light" and call.data.get("entity_id") in routed_targets
    ]
    assert triggered == []


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_crossfade_passed_as_transition(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
) -> None:
    """The configured crossfade_seconds is passed as `transition`."""
    hass.states.async_set(
        "switch.circadian_lighting_kitchen_kitchen_circadian",
        "on",
        {"brightness": 100.0, "colortemp": 5000},
    )
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    # After state-diff, only the fluorescent (off -> turn_on) fires; strips are
    # already off so no turn_off is issued. Filter to the one call that does fire.
    routed_calls = [
        call
        for call in service_calls
        if call.domain == "light" and call.data.get("entity_id") == "light.kitchen_fluorescent"
    ]
    assert routed_calls
    assert all(call.data.get("transition") == 1.0 for call in routed_calls)


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_routes_without_circadian_scene_logs_warning(
    hass: HomeAssistant,
    helper_entities,
    caplog,
) -> None:
    """An area with routes but no `circadian` scene logs a warning at startup."""
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "event_handlers": True,
                    "circadian_switches": [{"name": "Kitchen"}],
                    "lights": [
                        {
                            "id": "light.kitchen_fluorescent",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_1",
                            "circadian_switch": "Kitchen",
                            "circadian_type": "ct",
                        },
                    ],
                    "scenes": [
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "off", "name": "Off"},
                    ],
                    "circadian_kelvin_routes": {
                        "routes": [
                            {
                                "kelvin_range": [4500, 5500],
                                "lights": ["light.kitchen_fluorescent"],
                            },
                            {"lights": ["light.kitchen_strip_1"]},
                        ]
                    },
                }
            ]
        }
    }
    import logging

    caplog.set_level(logging.WARNING, logger="custom_components.area_lighting")
    await _setup(hass, cfg)
    assert any(
        "circadian_kelvin_routes" in record.message
        and "no `circadian` scene" in record.message
        and "kitchen" in record.message
        for record in caplog.records
    )
