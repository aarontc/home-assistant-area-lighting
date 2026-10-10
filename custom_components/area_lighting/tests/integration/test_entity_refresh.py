"""Entities refresh from a new config and detach from controllers on removal."""

from __future__ import annotations

from dataclasses import replace

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.controller import AreaLightingController
from custom_components.area_lighting.diagnostics import AreaLightingDiagnosticSensor
from custom_components.area_lighting.models import SceneConfig
from custom_components.area_lighting.scene import AreaLightingScene, BehavioralScene
from custom_components.area_lighting.select import AreaLastSceneSelect

AREA = "network_room"


async def _setup(hass: HomeAssistant, cfg: dict) -> AreaLightingController:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    return hass.data["area_lighting"]["controllers"][AREA]


def _recorded(hass: HomeAssistant, key: str = AREA) -> list:
    return hass.data["area_lighting"]["entities"][key]


def _select(hass: HomeAssistant) -> AreaLastSceneSelect:
    return next(e for e in _recorded(hass) if isinstance(e, AreaLastSceneSelect))


def _refresh_area(hass: HomeAssistant, ctrl: AreaLightingController) -> None:
    """Refresh every entity of the area the way a reload would."""
    names = {scene.slug: scene for scene in ctrl.area.scenes}
    for entity in _recorded(hass):
        if isinstance(entity, AreaLightingScene):
            entity.refresh_from_config(ctrl.area, names[entity._scene_cfg.slug])
        elif isinstance(entity, BehavioralScene):
            scene = names.get(entity._slug)
            entity.refresh_from_config(ctrl.area, scene.name if scene else entity._slug.title())
        else:
            entity.refresh_from_config()


@pytest.mark.integration
async def test_select_options_follow_a_new_scene_list(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config)
    scenes = [s for s in ctrl.area.scenes if s.slug != "christmas"]
    scenes.append(SceneConfig(slug="movie", name="Movie", area_id=AREA))
    ctrl.area = replace(ctrl.area, scenes=scenes)

    _select(hass).refresh_from_config()

    options = hass.states.get(f"select.{AREA}_last_scene").attributes["options"]
    assert "movie" in options
    assert "christmas" not in options


@pytest.mark.integration
async def test_area_name_change_updates_friendly_names(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config)
    ctrl.area = replace(ctrl.area, name="Server Room")

    _refresh_area(hass, ctrl)

    for entity_id, name in (
        (f"select.{AREA}_last_scene", "Server Room Last Scene"),
        (f"switch.{AREA}_night_mode", "Server Room Night Mode"),
        (f"number.{AREA}_motion_timeout_minutes", "Server Room Motion Timeout"),
        (f"binary_sensor.{AREA}_occupied", "Server Room Occupied"),
        (f"scene.{AREA}_daylight", "Server Room Daylight"),
        (f"scene.{AREA}_circadian", "Server Room Circadian"),
    ):
        assert hass.states.get(entity_id).attributes["friendly_name"] == name


@pytest.mark.integration
async def test_scene_entity_applies_refreshed_scene_config(
    hass: HomeAssistant, helper_entities, service_calls, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    scene = next(
        e
        for e in _recorded(hass)
        if isinstance(e, AreaLightingScene) and e._scene_cfg.slug == "daylight"
    )
    new_cfg = replace(
        scene._scene_cfg,
        entities={
            "light.network_room_overhead_1": {"state": "on", "brightness": 42},
            "light.network_room_overhead_2": {"state": "off"},
        },
    )
    scene.refresh_from_config(scene._area, new_cfg)
    service_calls.clear()

    await scene.async_activate()
    await hass.async_block_till_done()

    calls = {(c.service, c.data["entity_id"]): c.data for c in service_calls}
    assert calls[("turn_on", "light.network_room_overhead_1")]["brightness"] == 42
    assert ("turn_off", "light.network_room_overhead_2") in calls
    assert hass.states.get(f"scene.{AREA}_daylight").attributes["scene_source"] == "config"


@pytest.mark.integration
async def test_refresh_without_changes_writes_no_state_change(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config)
    changes = []
    hass.bus.async_listen(EVENT_STATE_CHANGED, changes.append)

    _refresh_area(hass, ctrl)
    await hass.async_block_till_done()

    assert [e.data["entity_id"] for e in changes] == []


@pytest.mark.integration
async def test_diagnostics_listens_to_new_controllers_only(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    old = await _setup(hass, network_room_config)
    sensor = next(
        e for e in _recorded(hass, "__global__") if isinstance(e, AreaLightingDiagnosticSensor)
    )
    new = AreaLightingController(hass, old.area, hass.data["area_lighting"]["config"])
    hass.data["area_lighting"]["controllers"] = {AREA: new}

    sensor.sync_controller_listeners()
    sensor.sync_controller_listeners()

    assert sensor._on_controller_change not in old._state_listeners
    assert new._state_listeners.count(sensor._on_controller_change) == 1
    new.shutdown()


@pytest.mark.integration
async def test_registered_entities_are_recorded_by_area(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    record = hass.data["area_lighting"]["entities"]

    assert set(record) == {AREA, "__global__"}
    assert {e.entity_id for e in record["__global__"]} == {
        "switch.area_lighting_motion_lights_enabled",
        "switch.area_lighting_occupancy_timeout_enabled",
        "switch.area_lighting_demand_response_active",
        "sensor.area_lighting_diagnostics",
        "sensor.area_lighting_circadian",
    }
    area_ids = {e.entity_id for e in record[AREA]}
    # 5 switches, 1 select, 6 numbers, 1 binary sensor, 5 visual and 2 behavioral scenes
    assert len(area_ids) == len(record[AREA]) == 20
    assert {f"scene.{AREA}_daylight", f"scene.{AREA}_off", f"select.{AREA}_last_scene"} <= area_ids
    assert all(eid.split(".", 1)[1].startswith(f"{AREA}_") for eid in area_ids)
    assert all(hass.states.get(eid) is not None for eid in area_ids)


@pytest.mark.integration
async def test_removed_entity_no_longer_listens_to_its_controller(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    ctrl = await _setup(hass, network_room_config)
    select = _select(hass)
    await select.async_remove()
    await hass.async_block_till_done()
    changes = []
    hass.bus.async_listen(EVENT_STATE_CHANGED, changes.append)

    ctrl._notify_state_change()
    await hass.async_block_till_done()

    assert select._on_controller_change not in ctrl._state_listeners
    assert [e for e in changes if e.data["entity_id"] == select.entity_id] == []
