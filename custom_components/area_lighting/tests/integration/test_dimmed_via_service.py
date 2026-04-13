"""Dimmed flag behavior through the service layer.

Reproduces the reported bug where calling lighting_raise/lighting_lower
as a service does not set the dimmed flag, and calling lighting_on does
not restore the scene after dimming.
"""

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


async def _call_service(hass: HomeAssistant, service: str, area_id: str) -> None:
    await hass.services.async_call(
        "area_lighting",
        service,
        {"area_id": area_id},
        blocking=True,
    )


@pytest.mark.integration
async def test_lighting_lower_service_sets_dimmed(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Calling the area_lighting.lighting_lower service must set dimmed."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 200}
    )
    hass.states.async_set(
        "light.network_room_overhead_2", "on", {"brightness": 200}
    )
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    assert not ctrl._state.dimmed

    await _call_service(hass, "lighting_lower", "network_room")
    await hass.async_block_till_done()

    assert ctrl._state.dimmed
    assert ctrl._state.previous_scene == "evening"
    assert ctrl._state.scene_slug == "evening"  # still same scene, just dimmed


@pytest.mark.integration
async def test_lighting_raise_service_sets_dimmed(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 100}
    )
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)

    await _call_service(hass, "lighting_raise", "network_room")
    await hass.async_block_till_done()

    assert ctrl._state.dimmed
    assert ctrl._state.previous_scene == "daylight"


@pytest.mark.integration
async def test_lighting_on_service_restores_scene_when_dimmed(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Dimmed evening → calling lighting_on service restores evening (not cycles)."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 200}
    )
    hass.states.async_set(
        "light.network_room_overhead_2", "on", {"brightness": 200}
    )
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)

    # Dim via the service
    await _call_service(hass, "lighting_lower", "network_room")
    await hass.async_block_till_done()
    assert ctrl._state.dimmed
    assert ctrl._state.previous_scene == "evening"

    # Now press "on" via the service — should restore evening, not cycle
    await _call_service(hass, "lighting_on", "network_room")
    await hass.async_block_till_done()

    assert ctrl._state.scene_slug == "evening"
    assert not ctrl._state.dimmed


@pytest.mark.integration
async def test_select_entity_exposes_dimmed_attribute(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The select entity must expose `dimmed` and `previous_scene` as
    state attributes so users can tell via the UI that raise/lower had
    an effect.
    """
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 200}
    )
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    await _call_service(hass, "lighting_lower", "network_room")
    await hass.async_block_till_done()

    select_state = hass.states.get("select.network_room_last_scene")
    assert select_state is not None
    assert select_state.attributes.get("dimmed") is True
    assert select_state.attributes.get("previous_scene") == "evening"


@pytest.mark.integration
async def test_select_entity_dimmed_attribute_clears_on_restore(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """After `lighting_on` restores a dimmed scene, the select's dimmed
    attribute must go back to False."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set(
        "light.network_room_overhead_1", "on", {"brightness": 200}
    )
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    await _call_service(hass, "lighting_lower", "network_room")
    await hass.async_block_till_done()
    assert hass.states.get("select.network_room_last_scene").attributes.get("dimmed") is True

    await _call_service(hass, "lighting_on", "network_room")
    await hass.async_block_till_done()

    select_state = hass.states.get("select.network_room_last_scene")
    assert select_state.attributes.get("dimmed") is False
    assert select_state.attributes.get("previous_scene") is None
