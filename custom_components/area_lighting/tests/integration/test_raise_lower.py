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


def _stepped_ids(service_calls: list) -> set[str]:
    """Entity IDs that received a relative brightness step."""
    return {
        c.data.get("entity_id")
        for c in _light_turn_on_calls(service_calls)
        if c.data.get("brightness_step_pct", 0) != 0
    }


def _commanded_ids(service_calls: list) -> set[str]:
    """Entity IDs that received any light.turn_on at all."""
    return {c.data.get("entity_id") for c in _light_turn_on_calls(service_calls)}


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
    service_calls.clear()
    await ctrl.lighting_raise()
    assert ctrl._state.scene_slug == "evening"
    assert ctrl._state.dimmed


@pytest.mark.integration
async def test_raise_after_lights_all_off_restores_the_last_scene(
    hass: HomeAssistant,
    helper_entities,
    network_room_config,
    service_calls,
) -> None:
    """The real going-dark path still remembers the scene.

    Switching the last light off drives handle_lights_all_off, and a later
    dim-up must come back to the scene the room was showing rather than the
    area's default on-scene.
    """
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    hass.states.async_set("light.network_room_overhead_1", "on", {"brightness": 180})
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)

    hass.states.async_set("light.network_room_overhead_1", "off", {})
    hass.states.async_set("light.network_room_overhead_2", "off", {})
    await ctrl.handle_lights_all_off()
    assert ctrl._state.is_off

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


# ── Cluster (Hue Zone) stepping ─────────────────────────────────────────
#
# A cluster entity reports `on` when ANY member is on, and Home Assistant
# resolves brightness_step_pct against the cluster's averaged brightness
# before forwarding one absolute brightness to EVERY member. Stepping a
# cluster therefore relights its off members and flattens the members'
# distinct brightnesses, so raise/lower must always address real bulbs.

ZONE = "light.den_zone"
ZONE_MEMBERS = ["light.den_a", "light.den_b"]


@pytest.fixture
def den_config() -> dict:
    """Area with a two-member Hue-Zone cluster listed alongside its members."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "den",
                    "name": "Den",
                    "event_handlers": False,
                    "lights": [{"id": m, "roles": ["dimming"]} for m in ZONE_MEMBERS],
                    "light_clusters": [{"id": ZONE, "members": list(ZONE_MEMBERS)}],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "evening", "name": "Evening"},
                    ],
                }
            ]
        }
    }


@pytest.mark.integration
async def test_raise_does_not_relight_off_members_of_a_partly_lit_cluster(
    hass: HomeAssistant, helper_entities, den_config, service_calls
) -> None:
    """One zone member on, one off → only the lit member is stepped.

    The zone entity must not be commanded: HA would forward an absolute
    brightness to every member and switch the dark one on.
    """
    await _setup(hass, den_config)
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    hass.states.async_set("light.den_a", "on", {"brightness": 120})
    hass.states.async_set("light.den_b", "off", {})
    # A Hue Zone reports `on` while any member is lit.
    hass.states.async_set(ZONE, "on", {"brightness": 120})
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    service_calls.clear()
    await ctrl.lighting_raise()

    assert _stepped_ids(service_calls) == {"light.den_a"}
    assert ZONE not in _commanded_ids(service_calls)
    assert "light.den_b" not in _commanded_ids(service_calls)


@pytest.mark.integration
async def test_raise_steps_fully_lit_cluster_members_individually(
    hass: HomeAssistant, helper_entities, den_config, service_calls
) -> None:
    """All members on → each is stepped directly, never through the zone.

    Going through the zone would step against the members' mean brightness
    and overwrite both with one value, and would also double-step members
    that are commanded individually as well.
    """
    await _setup(hass, den_config)
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    hass.states.async_set("light.den_a", "on", {"brightness": 60})
    hass.states.async_set("light.den_b", "on", {"brightness": 220})
    hass.states.async_set(ZONE, "on", {"brightness": 140})
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    service_calls.clear()
    await ctrl.lighting_raise()

    assert _stepped_ids(service_calls) == {"light.den_a", "light.den_b"}
    assert ZONE not in _commanded_ids(service_calls)


@pytest.mark.integration
async def test_raise_steps_members_of_a_cluster_only_area(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """An area declaring only a cluster still steps the underlying bulbs."""
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "hall",
                    "name": "Hall",
                    "event_handlers": False,
                    "light_clusters": [
                        {"id": "light.hall_zone", "members": ["light.hall_a", "light.hall_b"]}
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "evening", "name": "Evening"},
                    ],
                }
            ]
        }
    }
    await _setup(hass, cfg)
    ctrl = hass.data["area_lighting"]["controllers"]["hall"]
    hass.states.async_set("light.hall_a", "on", {"brightness": 100})
    hass.states.async_set("light.hall_b", "off", {})
    hass.states.async_set("light.hall_zone", "on", {"brightness": 100})
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    service_calls.clear()
    await ctrl.lighting_raise()

    assert _stepped_ids(service_calls) == {"light.hall_a"}
    assert "light.hall_zone" not in _commanded_ids(service_calls)


@pytest.mark.integration
async def test_raise_does_not_step_a_cluster_nested_in_another_cluster(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A zone listed inside another zone's members is still never stepped.

    The outer zone contributes the nested zone's bulbs through the nested
    zone's own declaration, so every command lands on a real bulb.
    """
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "loft",
                    "name": "Loft",
                    "event_handlers": False,
                    "light_clusters": [
                        {
                            "id": "light.loft_all",
                            "members": ["light.loft_a", "light.loft_inner"],
                        },
                        {
                            "id": "light.loft_inner",
                            "members": ["light.loft_b", "light.loft_c"],
                        },
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "evening", "name": "Evening"},
                    ],
                }
            ]
        }
    }
    await _setup(hass, cfg)
    ctrl = hass.data["area_lighting"]["controllers"]["loft"]
    hass.states.async_set("light.loft_a", "on", {"brightness": 100})
    hass.states.async_set("light.loft_b", "on", {"brightness": 100})
    hass.states.async_set("light.loft_c", "off", {})
    hass.states.async_set("light.loft_inner", "on", {"brightness": 100})
    hass.states.async_set("light.loft_all", "on", {"brightness": 100})
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    service_calls.clear()
    await ctrl.lighting_raise()

    assert _stepped_ids(service_calls) == {"light.loft_a", "light.loft_b"}
    assert not {"light.loft_all", "light.loft_inner"} & _commanded_ids(service_calls)


@pytest.mark.integration
async def test_raise_does_not_step_a_members_bearing_entry_under_lights(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A zone misfiled under `lights:` is still a zone, so it is not stepped.

    The schema accepts `members` on either list, so what makes an entry a
    batch target is having members, not which key it was declared under.
    """
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "porch",
                    "name": "Porch",
                    "event_handlers": False,
                    "lights": [
                        {
                            "id": "light.porch_zone",
                            "members": ["light.porch_a", "light.porch_b"],
                        }
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "evening", "name": "Evening"},
                    ],
                }
            ]
        }
    }
    await _setup(hass, cfg)
    ctrl = hass.data["area_lighting"]["controllers"]["porch"]
    hass.states.async_set("light.porch_a", "on", {"brightness": 100})
    hass.states.async_set("light.porch_b", "off", {})
    hass.states.async_set("light.porch_zone", "on", {"brightness": 100})
    ctrl._state.transition_to_scene("evening", ActivationSource.USER)
    service_calls.clear()
    await ctrl.lighting_raise()

    assert _stepped_ids(service_calls) == {"light.porch_a"}
    assert "light.porch_zone" not in _commanded_ids(service_calls)


@pytest.mark.integration
async def test_raise_treats_a_cluster_with_all_members_off_as_dark(
    hass: HomeAssistant, helper_entities, den_config, service_calls
) -> None:
    """A stale `on` zone over dark bulbs is not 'lights are on'.

    Every real bulb is off, so raise takes the dark-area path and brings
    the area to its minimum level rather than stepping a phantom zone.
    """
    await _setup(hass, den_config)
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    hass.states.async_set("light.den_a", "off", {})
    hass.states.async_set("light.den_b", "off", {})
    hass.states.async_set(ZONE, "on", {"brightness": 120})
    service_calls.clear()
    await ctrl.lighting_raise()

    assert _stepped_ids(service_calls) == set()
    assert {"light.den_a", "light.den_b"} <= _ids_set_to_min(service_calls)
    assert ctrl._state.dimmed
