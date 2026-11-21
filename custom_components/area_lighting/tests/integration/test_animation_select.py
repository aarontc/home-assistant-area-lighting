"""Global animation choices, persistence, and configuration reloads."""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

import custom_components.area_lighting as area_lighting
from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.const import DOMAIN

SELECT = "select.area_lighting_christmas_animation"


def _area(area_id: str, animations: list[str], name: str = "Christmas") -> dict:
    return {
        "id": area_id,
        "name": area_id.title(),
        "lights": [{"id": f"light.{area_id}", "roles": ["color", "dimming"]}],
        "scenes": [
            {
                "id": "christmas",
                "name": name,
                "animations": animations,
                "entities": {
                    f"light.{area_id}": {
                        "state": "on",
                        "brightness": 80,
                        "hs_color": [30, 100],
                    }
                },
            }
        ],
    }


def _config(*areas: dict) -> dict:
    return {
        DOMAIN: {
            "scene_self_heal": False,
            "areas": list(areas) or [_area("den", ["red", "green"])],
            "animations": {
                "red": {"period": 30, "transition": 3, "palette": [{"hs_color": [0, 100]}]},
                "green": {
                    "period": 60,
                    "transition": 4,
                    "palette": [{"hs_color": [120, 100]}],
                },
                "blue": {"period": 10, "palette": [{"hs_color": [240, 100]}]},
            },
        }
    }


async def _setup(hass: HomeAssistant, config: dict) -> None:
    for area in config[DOMAIN]["areas"]:
        hass.states.async_set(f"light.{area['id']}", "off", {})
    assert await async_setup_component(hass, DOMAIN, copy.deepcopy(config))
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


async def _choose(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call(
        "select", "select_option", {"entity_id": SELECT, "option": option}, blocking=True
    )
    await hass.async_block_till_done()


async def _reload(hass: HomeAssistant, config: dict) -> None:
    with patch(
        "custom_components.area_lighting.async_hass_config_yaml",
        AsyncMock(return_value=copy.deepcopy(config)),
    ):
        await hass.services.async_call(DOMAIN, "reload", {}, blocking=True)
    await hass.async_block_till_done()


def _turn_ons(service_calls) -> dict[str, dict]:
    return {
        call.data["entity_id"]: call.data for call in service_calls if call.service == "turn_on"
    }


@pytest.mark.integration
async def test_animation_select_unions_options_and_uses_first_scene_name(
    hass: HomeAssistant, helper_entities
) -> None:
    await _setup(
        hass,
        _config(
            _area("den", [], "Winter"),
            _area("hall", ["green", "red"]),
            _area("study", ["red", "blue"]),
        ),
    )

    state = hass.states.get(SELECT)
    assert state.state == "green"
    assert state.attributes["options"] == ["off", "green", "red", "blue"]
    assert state.attributes["friendly_name"] == "Winter Animation"


@pytest.mark.integration
async def test_animation_select_applies_off_and_another_animation_to_active_areas(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config(_area("den", ["red", "green"]), _area("hall", ["red"])))
    for ctrl in hass.data[DOMAIN]["controllers"].values():
        await ctrl._activate_scene("christmas", ActivationSource.USER)
    await hass.async_block_till_done()
    service_calls.clear()

    await _choose(hass, "green")

    calls = _turn_ons(service_calls)
    assert calls["light.den"]["hs_color"] == [120, 100]
    assert calls["light.den"]["transition"] == 4
    assert calls["light.hall"]["hs_color"] == [30, 100]
    await _choose(hass, "red")
    service_calls.clear()

    await _choose(hass, "off")

    calls = _turn_ons(service_calls)
    assert calls["light.den"]["hs_color"] == [30, 100]
    assert calls["light.hall"]["hs_color"] == [30, 100]
    assert hass.states.get(SELECT).state == "off"


@pytest.mark.integration
@pytest.mark.parametrize("option", ["off", "green"])
async def test_animation_choice_survives_restart(
    hass: HomeAssistant, helper_entities, option: str
) -> None:
    config = _config()
    await _setup(hass, config)
    await _choose(hass, option)
    await area_lighting.async_teardown(hass)

    assert await area_lighting.async_setup(hass, area_lighting.CONFIG_SCHEMA(copy.deepcopy(config)))
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    assert hass.states.get(SELECT).state == option
    assert hass.data[DOMAIN]["global"].scene_animation("christmas") == option


@pytest.mark.integration
async def test_reload_updates_animation_options_and_falls_back_when_choice_removed(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data[DOMAIN]["controllers"]["den"]
    await ctrl._activate_scene("christmas", ActivationSource.USER)
    await _choose(hass, "green")
    service_calls.clear()

    await _reload(hass, _config(_area("den", ["blue", "red"], "Winter")))

    state = hass.states.get(SELECT)
    assert state.state == "blue"
    assert state.attributes["options"] == ["off", "blue", "red"]
    assert state.attributes["friendly_name"] == "Winter Animation"
    assert _turn_ons(service_calls)["light.den"]["hs_color"] == [240, 100]
    assert hass.data[DOMAIN]["state_storage"].get_global_state()["scene_animations"] == {
        "christmas": "blue"
    }


@pytest.mark.integration
async def test_reload_adds_and_removes_animation_select(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config(_area("den", [])))
    assert hass.states.get(SELECT) is None

    await _reload(hass, _config())
    assert hass.states.get(SELECT).state == "red"
    ctrl = hass.data[DOMAIN]["controllers"]["den"]
    await ctrl._activate_scene("christmas", ActivationSource.USER)
    await hass.async_block_till_done()
    service_calls.clear()

    await _reload(hass, _config(_area("den", [])))

    state = hass.states.get(SELECT)
    assert state is None or state.state == "unavailable"
    assert _turn_ons(service_calls)["light.den"]["hs_color"] == [30, 100]
    assert hass.data[DOMAIN]["global"].scene_animation("christmas") is None
    await _reload(hass, _config())
    assert hass.states.get(SELECT).state == "red"
    entities = hass.data[DOMAIN]["entities"][area_lighting.GLOBAL_ENTITIES]
    assert [entity.entity_id for entity in entities].count(SELECT) == 1


@pytest.mark.integration
async def test_reload_writes_animation_select_once_after_refreshing_options(
    hass: HomeAssistant, helper_entities
) -> None:
    await _setup(hass, _config())
    states = []

    def record_state(event) -> None:
        if event.data["entity_id"] == SELECT:
            states.append(event.data["new_state"])

    unsubscribe = hass.bus.async_listen("state_changed", record_state)
    try:
        await _reload(hass, _config(_area("den", ["blue"])))
        assert [state.state for state in states] == ["blue"]
        assert states[0].attributes["options"] == ["off", "blue"]
        assert states[0].attributes["friendly_name"] == "Christmas Animation"
    finally:
        unsubscribe()


@pytest.mark.integration
async def test_disabled_areas_do_not_supply_animation_select_configuration(
    hass: HomeAssistant, helper_entities
) -> None:
    disabled = _area("disabled", ["blue"], "Disabled")
    disabled["enabled"] = False
    await _setup(hass, _config(disabled, _area("den", ["green", "red"], "Winter")))

    state = hass.states.get(SELECT)
    assert state.state == "green"
    assert state.attributes["options"] == ["off", "green", "red"]
    assert state.attributes["friendly_name"] == "Winter Animation"


@pytest.mark.integration
async def test_disabled_area_alone_does_not_create_animation_select(
    hass: HomeAssistant, helper_entities
) -> None:
    disabled = _area("disabled", ["blue"])
    disabled["enabled"] = False
    await _setup(hass, _config(disabled, _area("den", [])))

    assert hass.states.get(SELECT) is None


@pytest.mark.integration
@pytest.mark.parametrize("option", ["off", "blue"])
async def test_unchanged_effective_animation_does_not_reactivate_area(
    hass: HomeAssistant, helper_entities, service_calls, option: str
) -> None:
    await _setup(hass, _config(_area("den", ["red"]), _area("hall", ["green", "blue"])))
    await _choose(hass, "green")
    ctrl = hass.data[DOMAIN]["controllers"]["den"]
    await ctrl._activate_scene("christmas", ActivationSource.USER)
    await hass.async_block_till_done()
    generation = ctrl._animation_generation
    targets = ctrl._active_scene_targets
    service_calls.clear()

    await _choose(hass, option)

    assert not service_calls
    assert ctrl._animation_generation == generation
    assert ctrl._active_scene_targets is targets
