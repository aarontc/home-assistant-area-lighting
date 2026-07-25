"""Demand-response shedding: circadian activation and dark bring-up."""

from __future__ import annotations

import time

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.const import BRIGHTNESS_STEP_DEFAULT
from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _config() -> dict:
    # 6 lights, all bound to one circadian switch -> circadian on-set = 6.
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "study",
                    "name": "Study",
                    "event_handlers": True,
                    "circadian_switches": [
                        {"name": "Main", "max_brightness": 100, "min_brightness": 40},
                    ],
                    "lights": [
                        {
                            "id": f"light.study_{i}",
                            "circadian_switch": "Main",
                            "circadian_type": "ct",
                            "roles": ["dimming"],
                        }
                        for i in range(1, 7)
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    for i in range(1, 7):
        hass.states.async_set(f"light.study_{i}", "off", {})
    hass.states.async_set(
        "switch.circadian_lighting_study_main_circadian",
        "on",
        {"brightness": 80.0, "colortemp": 3500},
    )
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_circadian_sheds_to_keep_two(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    off = {
        c.data["entity_id"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    assert on == {"light.study_1", "light.study_2"}
    assert {f"light.study_{i}" for i in (3, 4, 5, 6)} <= off
    assert ctrl.dr_shed_ids == frozenset({f"light.study_{i}" for i in (3, 4, 5, 6)})


@pytest.mark.integration
async def test_external_circadian_sets_and_off_clears_shed_set(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """Externally activated circadian under DR records the shed set; off clears it."""
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True

    await ctrl.handle_scene_activated("circadian")
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset({f"light.study_{i}" for i in (3, 4, 5, 6)})

    await ctrl.handle_scene_activated("off")
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset()


@pytest.mark.integration
async def test_manual_relight_of_shed_bulb_during_circadian_latches_manual(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A DR-shed bulb is not driven by the circadian switches: the user
    relighting it is a genuine override, so the area must latch manual,
    and a later flag flip must not re-fire circadian over that state."""
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True

    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert "light.study_3" in ctrl.dr_shed_ids

    # Physical result of the shed activation: kept on, shed off.
    for i in (1, 2):
        hass.states.async_set(f"light.study_{i}", "on", {"brightness": 204})
    for i in (3, 4, 5, 6):
        hass.states.async_set(f"light.study_{i}", "off", {})
    await hass.async_block_till_done()

    # Expire the post-activation grace window so the event is judged on
    # its own merits.
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0

    service_calls.clear()
    hass.states.async_set("light.study_3", "on", {"brightness": 150})
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    off = {
        c.data["entity_id"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    assert "light.study_3" not in off
    assert hass.states.get("light.study_3").state == "on"

    # Flip the flag off then on via the real setter: the manual area is
    # skipped by reactivate_for_demand_response, so no light command may
    # land and the user's relight survives.
    service_calls.clear()
    await _toggles(hass).async_set_demand_response_active(False)
    await hass.async_block_till_done()
    await _toggles(hass).async_set_demand_response_active(True)
    await hass.async_block_till_done()

    assert ctrl._state.is_manual
    assert [c for c in service_calls if c.domain == "light"] == []
    assert hass.states.get("light.study_3").state == "on"


@pytest.mark.integration
async def test_circadian_tick_on_kept_light_stays_circadian_under_dr(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A circadian-switch-driven update to a KEPT light is still skipped by
    manual detection while shedding is active: no false manual latch."""
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True

    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()
    assert "light.study_1" not in ctrl.dr_shed_ids

    hass.states.async_set("light.study_1", "on", {"brightness": 204, "color_temp_kelvin": 3500})
    await hass.async_block_till_done()
    ctrl._state.last_scene_change_monotonic = time.monotonic() - 30.0

    # Circadian tick: brightness and color temperature drift on a kept bulb.
    hass.states.async_set("light.study_1", "on", {"brightness": 90, "color_temp_kelvin": 2700})
    await hass.async_block_till_done()

    assert ctrl._state.is_circadian
    assert not ctrl._state.is_manual


@pytest.mark.integration
async def test_dark_bring_up_sheds(hass: HomeAssistant, helper_entities, service_calls) -> None:
    # raise from a fully-dark area brings all lights to min; under DR only the
    # kept lights come up.
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._set_all_lights_to_pct(12)
    await hass.async_block_till_done()

    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    assert on == {"light.study_1", "light.study_2"}


def _cluster_switch_config() -> dict:
    """The base 6-light config plus a Hue-Zone cluster over all of them that
    itself carries the circadian switch (a cluster turn_on would relight
    shed members through the zone)."""
    cfg = _config()
    cfg["area_lighting"]["areas"][0]["light_clusters"] = [
        {
            "id": "light.study_all",
            "members": [f"light.study_{i}" for i in range(1, 7)],
            "circadian_switch": "Main",
            "circadian_type": "ct",
        }
    ]
    return cfg


@pytest.mark.integration
async def test_circadian_never_drives_cluster_entity_under_dr(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A cluster with a circadian switch must be skipped under DR: its
    members are driven individually (kept on, shed off) and the cluster is
    neither turned on (would relight shed members) nor turned off (clusters
    are not in the shed universe)."""
    hass.states.async_set("light.study_all", "off", {})
    await _setup(hass, _cluster_switch_config())
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl._activate_scene("circadian", ActivationSource.USER)
    await hass.async_block_till_done()

    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    off = {
        c.data["entity_id"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    assert on == {"light.study_1", "light.study_2"}
    assert {f"light.study_{i}" for i in (3, 4, 5, 6)} <= off
    assert "light.study_all" not in on
    assert "light.study_all" not in off


def _solo_scene_config() -> dict:
    """The base 6-light config plus a 'solo' scene lighting only light.study_6.

    study_6 sits in the config-order shed tail of the all-lights bring-up,
    so restoring the remembered scene lights a bulb the bring-up must drop.
    """
    cfg = _config()
    cfg["area_lighting"]["areas"][0]["scenes"].insert(
        1,
        {
            "id": "solo",
            "name": "Solo",
            "entities": {"light.study_6": {"state": "on", "brightness": 200}},
        },
    )
    return cfg


@pytest.mark.integration
async def test_dark_bring_up_converges_after_scene_restore(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """Raise from dark restores the remembered scene, then converges to the
    all-lights shed: a bulb the scene lit but the bring-up sheds ends OFF,
    and tracking describes the bring-up (shed tail off-targets)."""
    await _setup(hass, _solo_scene_config())
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True
    ctrl._state.transition_to_scene("solo", ActivationSource.USER)

    # Chronological recorder: the grouped service_calls fixture cannot answer
    # which command landed last on study_6 (scene turn_on vs bring-up turn_off).
    chrono: list[tuple[str, str, int | None]] = []

    async def _record(call) -> None:
        chrono.append((call.service, call.data["entity_id"], call.data.get("brightness")))

    hass.services.async_register("light", "turn_on", _record)
    hass.services.async_register("light", "turn_off", _record)

    await ctrl._adjust_brightness(+1)
    await hass.async_block_till_done()

    step_brightness = round(255 * BRIGHTNESS_STEP_DEFAULT / 100)
    final = {eid: (svc, brightness) for svc, eid, brightness in chrono}
    assert final["light.study_1"] == ("turn_on", step_brightness)
    assert final["light.study_2"] == ("turn_on", step_brightness)
    for i in (3, 4, 5, 6):
        assert final[f"light.study_{i}"][0] == "turn_off"
    assert ctrl.dr_shed_ids == frozenset({f"light.study_{i}" for i in (3, 4, 5, 6)})
    # Every shed id must carry an off-target so a partial tracking update
    # cannot pass.
    for shed_id in ctrl.dr_shed_ids:
        assert ctrl._active_scene_targets[shed_id]["state"] == "off"


@pytest.mark.integration
async def test_startup_restore_populates_circadian_shed_diagnostics(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    """A controller restored into circadian with DR active exposes the shed
    set in diagnostics immediately, without driving any lights."""
    from custom_components.area_lighting.controller import AreaLightingController

    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["study"]
    _toggles(hass)._demand_response_active = True
    ctrl._state.transition_to_circadian(ActivationSource.USER)
    saved = ctrl.state_dict()

    fresh = AreaLightingController(hass, ctrl.area, ctrl._global_config)
    fresh.load_persisted_state(saved)
    service_calls.clear()
    fresh.reconcile_startup_state()
    await hass.async_block_till_done()

    snap = fresh.diagnostic_snapshot()
    assert set(snap["demand_response_shed"]) == {f"light.study_{i}" for i in (3, 4, 5, 6)}
    assert len(service_calls) == 0
