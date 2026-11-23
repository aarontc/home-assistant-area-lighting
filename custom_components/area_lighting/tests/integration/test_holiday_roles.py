"""Holiday roles in an area that defines christmas_ambient.

christmas_ambient plays the ambient role and christmas the task role.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.event_handlers import motion_may_light

MOTION_SENSOR = "binary_sensor.network_room_motion_sensor_motion"
OCCUPANCY_SENSOR = "binary_sensor.network_room_presence"


def _split(cfg: dict, *, occupancy: bool = False) -> dict:
    area = cfg["area_lighting"]["areas"][0]
    area["scenes"].append({"id": "christmas_ambient", "name": "Christmas Ambient"})
    if occupancy:
        area["occupancy_light_sensor_ids"] = [OCCUPANCY_SENSOR]
    return cfg


async def _setup(hass: HomeAssistant, cfg: dict):
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    return hass.data["area_lighting"]["controllers"]["network_room"]


async def _set_holiday(hass: HomeAssistant, mode: str) -> None:
    hass.states.async_set("input_select.holiday_mode", mode)
    await hass.async_block_till_done()


def _holiday_ambience(hass: HomeAssistant, ctrl) -> None:
    hass.states.async_set("input_select.ambient_scene", "holiday")
    hass.states.async_set("input_boolean.lighting_upstairs_ambient", "on")
    ctrl.ambience_enabled = True


@pytest.mark.integration
async def test_holiday_change_lights_christmas_ambient_and_none_turns_it_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, _split(network_room_config))
    await _set_holiday(hass, "christmas")
    assert ctrl._state.scene_slug == "christmas_ambient"
    assert ctrl._state.source == ActivationSource.HOLIDAY

    await _set_holiday(hass, "none")
    assert ctrl._state.is_off


@pytest.mark.integration
async def test_holiday_change_moves_a_holiday_lit_christmas_to_christmas_ambient(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """A split area restored in christmas lit by the holiday handler."""
    ctrl = await _setup(hass, _split(network_room_config))
    ctrl._state.transition_to_scene("christmas", ActivationSource.HOLIDAY)
    await ctrl.handle_holiday_changed("christmas")
    assert ctrl._state.scene_slug == "christmas_ambient"
    assert ctrl._state.source == ActivationSource.HOLIDAY


@pytest.mark.integration
async def test_holiday_change_leaves_a_persons_christmas_alone(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, _split(network_room_config))
    ctrl._state.transition_to_scene("christmas", ActivationSource.REMOTE)
    await ctrl.handle_holiday_changed("christmas")
    assert ctrl._state.scene_slug == "christmas"


@pytest.mark.integration
async def test_ambience_on_with_holiday_ambient_scene_lights_christmas_ambient(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, _split(network_room_config))
    hass.states.async_set("input_select.ambient_scene", "holiday")
    await _set_holiday(hass, "christmas")
    await ctrl._activate_scene("off_internal", ActivationSource.USER)
    await ctrl.handle_ambient_enabled()
    assert ctrl._state.scene_slug == "christmas_ambient"
    assert ctrl._state.source == ActivationSource.AMBIENCE


@pytest.mark.integration
async def test_off_from_christmas_lands_in_christmas_ambient_owned_by_ambience(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, _split(network_room_config))
    await _set_holiday(hass, "christmas")
    _holiday_ambience(hass, ctrl)
    await ctrl._activate_scene("christmas", ActivationSource.REMOTE)

    await ctrl.lighting_off()
    assert ctrl._state.scene_slug == "christmas_ambient"
    assert ctrl._state.source == ActivationSource.AMBIENCE
    assert not ctrl.is_occupied

    await ctrl.handle_ambient_disabled()
    assert ctrl._state.is_off


@pytest.mark.integration
async def test_fade_from_christmas_lands_in_christmas_ambient_owned_by_ambience(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, _split(network_room_config))
    await _set_holiday(hass, "christmas")
    _holiday_ambience(hass, ctrl)
    await ctrl._activate_scene("christmas", ActivationSource.MOTION)

    await ctrl.lighting_off_fade(source=ActivationSource.MOTION)
    assert ctrl._state.scene_slug == "christmas_ambient"
    assert ctrl._state.source == ActivationSource.AMBIENCE


@pytest.mark.integration
async def test_restored_christmas_owned_by_ambience_turns_off_at_next_off(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """State saved before the area split, when christmas played the ambient role."""
    ctrl = await _setup(hass, _split(network_room_config))
    await _set_holiday(hass, "christmas")
    _holiday_ambience(hass, ctrl)
    ctrl._state.transition_to_scene("christmas", ActivationSource.AMBIENCE)
    saved = ctrl.state_dict()
    ctrl.load_persisted_state(saved)
    ctrl.reconcile_startup_state()
    assert ctrl._state.scene_slug == "christmas"

    await ctrl.lighting_off()
    assert ctrl._state.is_off


@pytest.mark.integration
async def test_occupancy_timer_arms_in_christmas_and_cancels_in_christmas_ambient(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    hass.states.async_set(OCCUPANCY_SENSOR, "off")
    ctrl = await _setup(hass, _split(network_room_config, occupancy=True))
    await _set_holiday(hass, "christmas")
    await ctrl._activate_scene("christmas", ActivationSource.MOTION)
    assert ctrl._occupancy_timer.is_active

    await ctrl._activate_scene("christmas_ambient", ActivationSource.AMBIENCE)
    assert not ctrl._occupancy_timer.is_active

    await ctrl.handle_occupancy_off()
    assert not ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_unsplit_christmas_does_not_arm_the_occupancy_timer(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    hass.states.async_set(OCCUPANCY_SENSOR, "off")
    cfg = network_room_config
    cfg["area_lighting"]["areas"][0]["occupancy_light_sensor_ids"] = [OCCUPANCY_SENSOR]
    ctrl = await _setup(hass, cfg)
    await _set_holiday(hass, "christmas")
    await ctrl._activate_scene("christmas", ActivationSource.MOTION)
    assert not ctrl._occupancy_timer.is_active


@pytest.mark.integration
async def test_motion_steps_up_from_christmas_ambient_to_christmas(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    hass.states.async_set(MOTION_SENSOR, "off")
    ctrl = await _setup(hass, _split(network_room_config))
    ctrl.motion_light_enabled = True
    await _set_holiday(hass, "christmas")
    assert ctrl._state.scene_slug == "christmas_ambient"

    hass.states.async_set(MOTION_SENSOR, "on")
    await hass.async_block_till_done()
    assert ctrl._state.scene_slug == "christmas"
    assert ctrl._state.source == ActivationSource.MOTION


@pytest.mark.integration
async def test_motion_may_not_take_a_persons_split_christmas(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, _split(network_room_config))
    ctrl.motion_light_enabled = True
    await _set_holiday(hass, "christmas")
    assert motion_may_light(hass, ctrl)

    await ctrl._activate_scene("christmas", ActivationSource.REMOTE)
    assert not motion_may_light(hass, ctrl)


def _leader_follower_config() -> dict:
    def area(area_id: str, scenes: list[str], **extra) -> dict:
        return {
            "id": area_id,
            "name": area_id.title(),
            "event_handlers": True,
            "lights": [{"id": f"light.{area_id}_overhead", "roles": ["dimming"]}],
            "scenes": [{"id": s, "name": s.title()} for s in scenes],
            **extra,
        }

    return {
        "area_lighting": {
            "areas": [
                area("bath", ["ambient", "circadian", "christmas", "christmas_ambient", "off"]),
                area(
                    "closet",
                    ["ambient", "circadian", "christmas", "off"],
                    leader_area_id="bath",
                    follow_leader_deactivation=True,
                ),
            ]
        }
    }


@pytest.mark.integration
async def test_leader_christmas_ambient_sends_follower_to_ambient(
    hass: HomeAssistant, helper_entities
) -> None:
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "on")
    assert await async_setup_component(hass, "area_lighting", _leader_follower_config())
    await hass.async_block_till_done()
    controllers = hass.data["area_lighting"]["controllers"]
    bath, closet = controllers["bath"], controllers["closet"]
    await closet._activate_scene("circadian", ActivationSource.USER)

    await bath._activate_scene("christmas_ambient", ActivationSource.AMBIENCE)
    await hass.async_block_till_done()
    assert closet._state.scene_slug == "ambient"
    assert closet._state.source == ActivationSource.LEADER


@pytest.mark.integration
async def test_leader_split_christmas_propagates_as_a_scene(
    hass: HomeAssistant, helper_entities
) -> None:
    hass.states.async_set("light.bath_overhead", "on")
    hass.states.async_set("light.closet_overhead", "on")
    assert await async_setup_component(hass, "area_lighting", _leader_follower_config())
    await hass.async_block_till_done()
    controllers = hass.data["area_lighting"]["controllers"]
    bath, closet = controllers["bath"], controllers["closet"]
    await closet._activate_scene("circadian", ActivationSource.USER)

    await bath._activate_scene("christmas", ActivationSource.USER)
    await hass.async_block_till_done()
    assert closet._state.scene_slug == "christmas"
    assert closet._state.source == ActivationSource.LEADER
