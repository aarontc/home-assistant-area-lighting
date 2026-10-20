"""Fade times reach the lights as given, fractions included."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.alert import execute_alert
from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.models import AlertPattern, AlertStep

NETWORK_LIGHTS = ("light.network_room_overhead_1", "light.network_room_overhead_2")


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _transitions(service_calls, service: str) -> dict[str, float]:
    return {
        c.data["entity_id"]: c.data.get("transition")
        for c in service_calls
        if c.domain == "light" and c.service == service
    }


@pytest.mark.integration
async def test_off_fades_over_the_default_one_and_a_half_seconds(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    for eid in NETWORK_LIGHTS:
        hass.states.async_set(eid, "on")
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("evening", ActivationSource.USER)

    service_calls.clear()
    await ctrl.lighting_off()
    await hass.async_block_till_done()

    assert _transitions(service_calls, "turn_off") == dict.fromkeys(NETWORK_LIGHTS, 1.5)


@pytest.mark.integration
async def test_scene_and_motion_fades_keep_their_fraction(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    ctrl.motion_fadeout_seconds = 2.5

    service_calls.clear()
    await ctrl._activate_scene("evening", ActivationSource.USER, transition=0.75)
    await hass.async_block_till_done()
    assert set(_transitions(service_calls, "turn_on").values()) == {0.75}

    service_calls.clear()
    await ctrl.lighting_off_fade(source=ActivationSource.MOTION)
    await hass.async_block_till_done()
    assert _transitions(service_calls, "turn_off") == dict.fromkeys(NETWORK_LIGHTS, 2.5)


@pytest.mark.integration
async def test_scene_without_a_transition_fades_in_over_half_a_second(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    service_calls.clear()
    await ctrl._activate_scene("evening", ActivationSource.USER)
    await hass.async_block_till_done()

    assert _transitions(service_calls, "turn_on") == dict.fromkeys(NETWORK_LIGHTS, 0.5)


@pytest.mark.integration
async def test_scene_from_the_app_fades_in_over_half_a_second(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    await _setup(hass, network_room_config)

    service_calls.clear()
    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.network_room_evening"}, blocking=True
    )
    await hass.async_block_till_done()

    assert _transitions(service_calls, "turn_on") == dict.fromkeys(NETWORK_LIGHTS, 0.5)


@pytest.mark.integration
async def test_off_without_a_transition_uses_the_area_off_fade(
    hass: HomeAssistant, helper_entities, network_room_config, service_calls
) -> None:
    """Force-off, ambience, holiday, leader and linked offs name no fade."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    await ctrl._activate_scene("evening", ActivationSource.USER)

    service_calls.clear()
    await ctrl.lighting_force_off()
    await hass.async_block_till_done()

    assert _transitions(service_calls, "turn_off") == dict.fromkeys(NETWORK_LIGHTS, 1.5)


@pytest.mark.integration
@pytest.mark.parametrize(("step_transition", "sent"), [(None, 0), (0.25, 0.25)])
async def test_alert_off_steps_send_their_transition(
    hass: HomeAssistant,
    helper_entities,
    network_room_config,
    service_calls,
    step_transition,
    sent,
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]

    service_calls.clear()
    await execute_alert(
        hass,
        ctrl,
        AlertPattern(
            steps=[AlertStep(target="all", state="off", transition=step_transition)],
            restore=False,
        ),
    )
    await hass.async_block_till_done()

    assert set(_transitions(service_calls, "turn_off").values()) == {sent}
