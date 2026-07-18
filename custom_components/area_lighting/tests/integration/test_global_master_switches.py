"""Global master switches: motion-on and occupancy-timeout kill switches."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _config_with_occupancy() -> dict:
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "media_room",
                    "name": "Media Room",
                    "event_handlers": True,
                    "lights": [
                        {"id": "light.media_room_overhead", "roles": ["dimming"]},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "ambient", "name": "Ambient"},
                    ],
                    "occupancy_light_sensor_ids": [
                        "binary_sensor.media_room_presence",
                    ],
                    "occupancy_light_timer_durations": {"off": "00:30:00"},
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.media_room_presence",
                    ],
                    "motion_light_timer_durations": {"off": "00:08:00"},
                }
            ]
        }
    }


@pytest.mark.integration
async def test_holder_created_and_defaults_on(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())

    toggles = _toggles(hass)
    assert isinstance(toggles, GlobalToggles)
    assert toggles.motion_lights_enabled is True
    assert toggles.occupancy_timeout_enabled is True


@pytest.mark.integration
async def test_global_occupancy_off_suppresses_arm(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())
    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]

    await _toggles(hass).async_set_occupancy_timeout_enabled(False)
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_global_occupancy_off_cancels_running_timer(
    hass: HomeAssistant, helper_entities
) -> None:
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())
    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]

    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active

    await _toggles(hass).async_set_occupancy_timeout_enabled(False)
    await hass.async_block_till_done()

    assert not ctrl._occupancy_timer.is_active
    assert ctrl._state.is_on  # lights stayed on; no lights-off callback fired
    assert ctrl._state.scene_slug == "circadian"


@pytest.mark.integration
async def test_global_occupancy_off_expiry_is_noop(hass: HomeAssistant, helper_entities) -> None:
    """A restored/past-due timer firing while globally disabled is a no-op."""
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())
    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]

    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._state.is_on

    # Disable the flag WITHOUT going through the setter (so no cancel happens),
    # simulating a timer that outlived the flag flip / was restored at startup.
    _toggles(hass)._occupancy_timeout_enabled = False
    await ctrl._on_occupancy_timer()
    await hass.async_block_till_done()

    assert ctrl._state.is_on
    assert ctrl._state.scene_slug == "circadian"


@pytest.mark.integration
async def test_global_occupancy_off_then_on_rearms(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())
    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]

    await _toggles(hass).async_set_occupancy_timeout_enabled(False)
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert not ctrl._occupancy_timer.is_active

    await _toggles(hass).async_set_occupancy_timeout_enabled(True)
    await hass.async_block_till_done()

    assert ctrl._occupancy_timer.is_active


_MOTION_SENSOR = "binary_sensor.network_room_motion_sensor_motion"


@pytest.mark.integration
async def test_global_motion_on_allows_motion_activation(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.motion_light_enabled = True

    hass.states.async_set(_MOTION_SENSOR, "off")
    await hass.async_block_till_done()
    hass.states.async_set(_MOTION_SENSOR, "on")
    await hass.async_block_till_done()

    assert ctrl._state.source == ActivationSource.MOTION
    assert not ctrl._state.is_off


@pytest.mark.integration
async def test_global_motion_off_blocks_motion_activation(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.motion_light_enabled = True  # per-area on: only the GLOBAL gate blocks

    await _toggles(hass).async_set_motion_lights_enabled(False)

    hass.states.async_set(_MOTION_SENSOR, "off")
    await hass.async_block_till_done()
    hass.states.async_set(_MOTION_SENSOR, "on")
    await hass.async_block_till_done()

    assert ctrl._state.is_off  # motion did not turn lights on


@pytest.mark.integration
async def test_global_switches_registered_default_on_with_icons(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)

    motion = hass.states.get("switch.area_lighting_motion_lights_enabled")
    occ = hass.states.get("switch.area_lighting_occupancy_timeout_enabled")
    assert motion is not None
    assert motion.state == "on"
    assert occ is not None
    assert occ.state == "on"
    assert motion.attributes["friendly_name"] == "Area Lighting Motion Lights (Global)"
    assert occ.attributes["friendly_name"] == "Area Lighting Occupancy Timeout (Global)"
    assert motion.attributes["icon"] == "mdi:motion-sensor"
    assert occ.attributes["icon"] == "mdi:timer-cog-outline"


@pytest.mark.integration
async def test_global_motion_switch_service_call_blocks_and_restores(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.motion_light_enabled = True

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.area_lighting_motion_lights_enabled"},
        blocking=True,
    )
    assert _toggles(hass).motion_lights_enabled is False
    hass.states.async_set(_MOTION_SENSOR, "off")
    await hass.async_block_till_done()
    hass.states.async_set(_MOTION_SENSOR, "on")
    await hass.async_block_till_done()
    assert ctrl._state.is_off

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.area_lighting_motion_lights_enabled"},
        blocking=True,
    )
    hass.states.async_set(_MOTION_SENSOR, "off")
    await hass.async_block_till_done()
    hass.states.async_set(_MOTION_SENSOR, "on")
    await hass.async_block_till_done()
    assert ctrl._state.source == ActivationSource.MOTION


@pytest.mark.integration
async def test_global_occupancy_switch_service_call(hass: HomeAssistant, helper_entities) -> None:
    hass.states.async_set("light.media_room_overhead", "off")
    hass.states.async_set("binary_sensor.media_room_presence", "off")
    await _setup(hass, _config_with_occupancy())
    ctrl = hass.data["area_lighting"]["controllers"]["media_room"]

    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert ctrl._occupancy_timer.is_active

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.area_lighting_occupancy_timeout_enabled"},
        blocking=True,
    )
    assert _toggles(hass).occupancy_timeout_enabled is False
    assert not ctrl._occupancy_timer.is_active
    assert ctrl._state.is_on

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.area_lighting_occupancy_timeout_enabled"},
        blocking=True,
    )
    assert _toggles(hass).occupancy_timeout_enabled is True
    assert ctrl._occupancy_timer.is_active
