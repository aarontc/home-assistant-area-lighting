"""Cluster (Hue Zone) state changes must NOT trigger manual detection.

Reproduces the production bug seen on upstairs_bathroom: scenes target
the individual member lights, so cluster entities (e.g.
`light.hz_upstairs_bath_vanity_all`) are never present in
`_active_scene_targets`. A state-change event on a cluster outside the
grace window was being interpreted as a divergent manual override,
falsely transitioning the area to `manual` whenever the cluster's
aggregate state updated late (post-fade, late Hue bridge report, etc.).

The fix: clusters are aggregate echoes of their members. Any genuine
user adjustment reaches the member lights, which fire manual detection
themselves — clusters must not be tracked by manual detection.
"""

from __future__ import annotations

import time

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


@pytest.fixture
def cluster_room_config() -> dict:
    """Area with a Hue Zone-style cluster over its two member lights.

    Mirrors the upstairs_bathroom shape: scenes target the individual
    member lights; the cluster has no entry in the scene's `entities`.
    """
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "cluster_room",
                    "name": "Cluster Room",
                    "event_handlers": True,
                    "ambient_lighting_zone": "upstairs",
                    "circadian_switches": [
                        {"name": "Overhead", "max_brightness": 100, "min_brightness": 65},
                    ],
                    "lights": [
                        {
                            "id": "light.cluster_room_overhead_1",
                            "circadian_switch": "Overhead",
                            "circadian_type": "ct",
                            "roles": ["color", "dimming", "night", "white"],
                        },
                        {
                            "id": "light.cluster_room_overhead_2",
                            "circadian_switch": "Overhead",
                            "circadian_type": "ct",
                            "roles": ["color", "dimming", "night", "white"],
                        },
                    ],
                    "light_clusters": [
                        {
                            "id": "light.hz_cluster_room_all",
                            "members": [
                                "light.cluster_room_overhead_1",
                                "light.cluster_room_overhead_2",
                            ],
                        },
                    ],
                    "scenes": [
                        {
                            "id": "daylight",
                            "name": "Daylight",
                            "entities": {
                                "light.cluster_room_overhead_1": {
                                    "state": "on",
                                    "brightness": 200,
                                    "color_temp_kelvin": 5000,
                                },
                                "light.cluster_room_overhead_2": {
                                    "state": "on",
                                    "brightness": 200,
                                    "color_temp_kelvin": 5000,
                                },
                            },
                        },
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "ambient", "name": "Ambient"},
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


@pytest.fixture
async def cluster_helper_entities(hass: HomeAssistant, helper_entities) -> None:
    """Stub the cluster_room lights + its circadian switch."""
    hass.states.async_set(
        "switch.circadian_lighting_cluster_room_overhead_circadian",
        "off",
        {"brightness": 75.0, "colortemp": 3500},
    )
    hass.states.async_set("light.cluster_room_overhead_1", "off", {})
    hass.states.async_set("light.cluster_room_overhead_2", "off", {})
    hass.states.async_set("light.hz_cluster_room_all", "off", {})
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_cluster_state_change_does_not_mark_manual(
    hass: HomeAssistant, cluster_helper_entities, cluster_room_config
) -> None:
    """A state change on a cluster entity after the grace window must
    NOT transition the area to manual, even though clusters are absent
    from `_active_scene_targets`.
    """
    await _setup(hass, cluster_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["cluster_room"]

    # Put the area into a visual scene with targets resolved for the
    # MEMBER lights (not the cluster). This is how scene activation
    # actually runs in production.
    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    ctrl._active_scene_targets = ctrl._resolve_scene_targets("daylight")
    # Expire the grace window so manual detection isn't short-circuited
    # by the post-scene-change bypass.
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0

    # Cluster updates its aggregate state (e.g. late Hue bridge report
    # after the fade completes). The cluster has NO entry in
    # `_active_scene_targets`, so `state_matches_scene_target` returns
    # False — before the fix, this fires manual detection.
    hass.states.async_set(
        "light.hz_cluster_room_all",
        "on",
        {"brightness": 200, "color_temp_kelvin": 5000},
    )
    await hass.async_block_till_done()

    assert not ctrl._state.is_manual, (
        f"cluster aggregate update falsely transitioned area to manual "
        f"(state={ctrl._state.state}, scene={ctrl._state.scene_slug})"
    )


@pytest.mark.integration
async def test_individual_member_change_still_marks_manual(
    hass: HomeAssistant, cluster_helper_entities, cluster_room_config
) -> None:
    """Sanity check: the fix must not break manual detection on member lights.

    A real user adjustment on an individual member light (outside grace,
    diverging from scene target) must still trigger the manual transition.
    """
    await _setup(hass, cluster_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["cluster_room"]

    ctrl._state.transition_to_scene("daylight", ActivationSource.USER)
    ctrl._active_scene_targets = ctrl._resolve_scene_targets("daylight")
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0

    # Member light diverges from its scene target (brightness=200) —
    # the user dragged the slider down to 40.
    hass.states.async_set(
        "light.cluster_room_overhead_1",
        "on",
        {"brightness": 40, "color_temp_kelvin": 5000},
    )
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
