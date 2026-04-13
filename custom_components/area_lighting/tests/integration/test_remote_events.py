"""Lutron Caseta remote button event tests.

These tests simulate the exact event shape that
homeassistant.components.lutron_caseta fires at runtime — crucially
the subtype lives in `button_type` and `action` holds "press"/"release".
"""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


_REMOTE_DEVICE_ID = "97051ec8ae44dc6414792646470f6c69"


@pytest.fixture
def network_room_config_with_remote() -> dict:
    """Network room config with a remote attached to the area."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "network_room",
                    "name": "Network Room",
                    "event_handlers": True,
                    "ambient_lighting_zone": "upstairs",
                    "circadian_switches": [
                        {"name": "Overhead", "max_brightness": 100, "min_brightness": 65}
                    ],
                    "lights": [
                        {
                            "id": "light.network_room_overhead_1",
                            "circadian_switch": "Overhead",
                            "circadian_type": "ct",
                            "roles": ["color", "dimming", "night", "white"],
                        },
                    ],
                    "lutron_remotes": [
                        {"id": _REMOTE_DEVICE_ID, "name": "Entry Remote"}
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "night", "name": "Night"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _fire_lutron_button(
    hass: HomeAssistant, device_id: str, button_type: str, action: str = "press"
) -> None:
    """Fire the exact event shape homeassistant.components.lutron_caseta uses."""
    hass.bus.async_fire(
        "lutron_caseta_button_event",
        {
            "serial": "12345",
            "type": "SunnataDimmer",
            "button_number": 1,
            "leap_button_number": 0,
            "device_name": "Entry Remote",
            "device_id": device_id,
            "area_name": "Network Room",
            "button_type": button_type,  # "on" / "off" / "raise" / "lower" / "stop"
            "action": action,              # "press" / "release" / "multi_tap"
        },
    )


@pytest.mark.integration
async def test_remote_on_press_activates_lighting(
    hass: HomeAssistant, helper_entities, network_room_config_with_remote
) -> None:
    """Pressing the 'on' button on a Pico should activate circadian from off."""
    await _setup(hass, network_room_config_with_remote)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    assert ctrl._state.is_off

    _fire_lutron_button(hass, _REMOTE_DEVICE_ID, "on")
    await hass.async_block_till_done()

    assert ctrl._state.is_circadian


@pytest.mark.integration
async def test_remote_off_press_turns_off(
    hass: HomeAssistant, helper_entities, network_room_config_with_remote
) -> None:
    await _setup(hass, network_room_config_with_remote)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)

    _fire_lutron_button(hass, _REMOTE_DEVICE_ID, "off")
    await hass.async_block_till_done()

    assert ctrl._state.is_off


@pytest.mark.integration
async def test_remote_favorite_press_picks_night(
    hass: HomeAssistant, helper_entities, network_room_config_with_remote
) -> None:
    """Pico's favorite button (subtype 'stop') should trigger lighting_favorite."""
    await _setup(hass, network_room_config_with_remote)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    _fire_lutron_button(hass, _REMOTE_DEVICE_ID, "stop")
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "night"


@pytest.mark.integration
async def test_remote_release_is_ignored(
    hass: HomeAssistant, helper_entities, network_room_config_with_remote
) -> None:
    """Release events must not re-fire the action (it already ran on press)."""
    await _setup(hass, network_room_config_with_remote)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    _fire_lutron_button(hass, _REMOTE_DEVICE_ID, "on", action="press")
    await hass.async_block_till_done()
    assert ctrl._state.is_circadian

    # Now the release fires. It must NOT cycle out of circadian.
    _fire_lutron_button(hass, _REMOTE_DEVICE_ID, "on", action="release")
    await hass.async_block_till_done()
    assert ctrl._state.is_circadian


@pytest.mark.integration
async def test_remote_raise_press_dims(
    hass: HomeAssistant, helper_entities, network_room_config_with_remote
) -> None:
    await _setup(hass, network_room_config_with_remote)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 150}
    )
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)

    _fire_lutron_button(hass, _REMOTE_DEVICE_ID, "raise")
    await hass.async_block_till_done()

    assert ctrl._state.dimmed
    assert ctrl._state.previous_scene == "evening"
