"""snapshot_scene must refuse to run while demand response is active.

The service captures raw `hass.states` for every light in the area. During a
demand-response window the shed bulbs are physically OFF, so a snapshot taken
then bakes the shed into the stored scene PERMANENTLY: the scene is still wrong
after the window ends, and there is no record of what the bulbs should have
been, so it cannot be reconstructed.

Excluding the shed bulbs instead would silently store a partial scene, which is
just as wrong and harder to notice. Refusing is the only safe option, and the
recovery is trivial: run it again once the window closes.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.global_state import GlobalToggles

LIGHTS = [f"light.snap_{suffix}" for suffix in "abcdef"]


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _config() -> dict:
    entities = {eid: {"state": "on", "brightness": 200} for eid in LIGHTS}
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "snap_room",
                    "name": "Snap Room",
                    "event_handlers": True,
                    "lights": [{"id": eid, "roles": ["dimming"]} for eid in LIGHTS],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "bright", "name": "Bright", "entities": entities},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant) -> None:
    for eid in LIGHTS:
        hass.states.async_set(eid, "on", {"brightness": 200})
    assert await async_setup_component(hass, "area_lighting", _config())
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


async def _snapshot(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "area_lighting",
        "snapshot_scene",
        {"area_id": "snap_room", "scene": "bright"},
        blocking=True,
    )
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_snapshot_scene_refused_during_demand_response(
    hass: HomeAssistant, helper_entities
) -> None:
    await _setup(hass)
    storage = hass.data["area_lighting"]["scene_storage"]
    _toggles(hass)._demand_response_active = True

    # Half the bulbs are physically off, as they would be mid-shed.
    for eid in LIGHTS[2:]:
        hass.states.async_set(eid, "off", {})

    await _snapshot(hass)

    assert storage.get_scene_data("snap_room", "bright") is None


@pytest.mark.integration
async def test_snapshot_scene_works_when_demand_response_inactive(
    hass: HomeAssistant, helper_entities
) -> None:
    """The guard must not break the normal path."""
    await _setup(hass)
    storage = hass.data["area_lighting"]["scene_storage"]

    await _snapshot(hass)

    stored = storage.get_scene_data("snap_room", "bright")
    assert stored is not None
    assert set(stored) == set(LIGHTS)
