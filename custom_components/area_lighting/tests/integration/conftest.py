"""Integration-layer fixtures for area_lighting tests.

These fixtures pre-populate the external entities that area_lighting reads
(holiday_mode, ambient_scene, ambient zone booleans, circadian sensor/switch,
motion-light kill switch) so individual tests don't have to recreate them.
"""

from __future__ import annotations

import pytest

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.components.input_select import DOMAIN as INPUT_SELECT_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make area_lighting discoverable by the HA test harness."""
    yield


@pytest.fixture
async def helper_entities(hass: HomeAssistant) -> None:
    """Create every external entity area_lighting depends on."""
    # Load the light domain so light.turn_on / light.turn_off services exist.
    # Uses the demo light platform which creates a handful of mock lights we
    # don't actually touch — we override states directly via async_set.
    assert await async_setup_component(hass, LIGHT_DOMAIN, {"light": []})
    # Load binary_sensor so area_lighting can register occupancy sensors.
    assert await async_setup_component(
        hass, BINARY_SENSOR_DOMAIN, {"binary_sensor": []}
    )
    assert await async_setup_component(
        hass,
        INPUT_SELECT_DOMAIN,
        {
            "input_select": {
                "holiday_mode": {
                    "name": "Holiday Mode",
                    "options": ["none", "christmas", "halloween"],
                    "initial": "none",
                },
                "ambient_scene": {
                    "name": "Ambient Scene",
                    "options": ["ambient", "holiday"],
                    "initial": "ambient",
                },
            }
        },
    )
    assert await async_setup_component(
        hass,
        INPUT_BOOLEAN_DOMAIN,
        {
            "input_boolean": {
                "lighting_circadian_daylight_lights_enabled": {
                    "name": "Circadian daylight enabled",
                    "initial": True,
                },
                "motion_light_enabled": {
                    "name": "Motion light enabled (global)",
                    "initial": True,
                },
                "lighting_upstairs_ambient": {
                    "name": "Upstairs ambient zone",
                    "initial": False,
                },
                "lighting_downstairs_ambient": {
                    "name": "Downstairs ambient zone",
                    "initial": False,
                },
            }
        },
    )
    # Stub the circadian_lighting sensor that the controller reads for colortemp
    hass.states.async_set("sensor.circadian_values", "0", {"colortemp": 3500})
    # Stub circadian switch for the network_room fixture area
    hass.states.async_set(
        "switch.circadian_lighting_network_room_overhead_circadian",
        "off",
        {"brightness": 75.0, "colortemp": 3500},
    )
    # Stub the lights the network_room_config references so the validator's
    # light-assigned-to-circadian-switch check is satisfied in tests that
    # use the default fixture.
    hass.states.async_set(
        "light.network_room_overhead_1", "off", {}
    )
    hass.states.async_set(
        "light.network_room_overhead_2", "off", {}
    )
    await hass.async_block_till_done()


class _AggregatedCalls:
    """Aggregate ServiceCall objects from multiple mocked services.

    Exposes the same list-like surface the tests use: iteration, len,
    bool, and clear(). Backed by pytest-homeassistant-custom-component's
    async_mock_service helper.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        from pytest_homeassistant_custom_component.common import async_mock_service

        self._sources = [
            async_mock_service(hass, "light", "turn_on"),
            async_mock_service(hass, "light", "turn_off"),
            async_mock_service(hass, "switch", "turn_on"),
            async_mock_service(hass, "switch", "turn_off"),
        ]

    def __iter__(self):
        for src in self._sources:
            yield from src

    def __len__(self) -> int:
        return sum(len(s) for s in self._sources)

    def __bool__(self) -> bool:
        return any(s for s in self._sources)

    def clear(self) -> None:
        for src in self._sources:
            src.clear()


@pytest.fixture
def service_calls(hass: HomeAssistant) -> _AggregatedCalls:
    """Mock light.turn_on/turn_off and switch.turn_on/turn_off so tests
    can assert on captured service calls. Returns an aggregated iterable
    of ServiceCall objects with .domain, .service, and .data attributes.
    """
    return _AggregatedCalls(hass)


@pytest.fixture
def network_room_config() -> dict:
    """Minimal area_lighting YAML config for a single area with 2 lights + circadian."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "network_room",
                    "name": "Network Room",
                    "event_handlers": True,
                    "ambient_lighting_zone": "upstairs",
                    "circadian_switches": [
                        {"name": "Overhead", "max_brightness": 100, "min_brightness": 65},
                    ],
                    "lights": [
                        {
                            "id": "light.network_room_overhead_1",
                            "circadian_switch": "Overhead",
                            "circadian_type": "ct",
                            "roles": ["color", "dimming", "night", "white"],
                        },
                        {
                            "id": "light.network_room_overhead_2",
                            "circadian_switch": "Overhead",
                            "circadian_type": "ct",
                            "roles": ["color", "dimming", "night", "white"],
                        },
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "night", "name": "Night"},
                        {"id": "ambient", "name": "Ambient"},
                        {"id": "christmas", "name": "Christmas"},
                    ],
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.network_room_motion_sensor_motion",
                    ],
                    "motion_light_timer_durations": {
                        "off": "00:08:00",
                        "night_off": "00:05:00",
                    },
                    "occupancy_light_timer_durations": {
                        "off": "00:30:00",
                    },
                }
            ]
        }
    }
