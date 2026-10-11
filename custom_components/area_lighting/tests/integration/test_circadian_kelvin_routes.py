"""Integration tests for circadian_kelvin_routes (router + controller wiring).

The kitchen fixture has one fluorescent (banded 4500-5500K) and three
lightstrips (fallback). Tests assert which entities `light.turn_on` and
`light.turn_off` get called for, as the clock's kelvin changes.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.circadian_clock import CircadianClock

_TUBE = "light.kitchen_fluorescent"
_STRIPS = {"light.kitchen_strip_1", "light.kitchen_strip_2", "light.kitchen_strip_3"}


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.fixture
def kitchen_with_routes_config() -> dict:
    """Kitchen with one fluorescent banded [4500, 5500] and 3 strips as fallback."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "event_handlers": True,
                    "circadian_groups": [
                        {"name": "Kitchen", "max_brightness": 100, "min_brightness": 20},
                    ],
                    "lights": [
                        {
                            "id": "light.kitchen_fluorescent",
                            "circadian_group": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_1",
                            "circadian_group": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_2",
                            "circadian_group": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_3",
                            "circadian_group": "Kitchen",
                            "circadian_type": "ct",
                        },
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                    "circadian_kelvin_routes": {
                        "crossfade_seconds": 1.0,
                        "routes": [
                            {
                                "kelvin_range": [4500, 5500],
                                "lights": ["light.kitchen_fluorescent"],
                            },
                            {
                                "lights": [
                                    "light.kitchen_strip_1",
                                    "light.kitchen_strip_2",
                                    "light.kitchen_strip_3",
                                ]
                            },
                        ],
                    },
                }
            ]
        }
    }


@pytest.fixture
def _stub_kitchen_entities(hass: HomeAssistant):
    """Pre-populate the light states the validator expects."""
    for entity_id in (_TUBE, *_STRIPS):
        hass.states.async_set(entity_id, "off", {})


def _targets(service_calls, service: str) -> set[str]:
    return {
        call.data.get("entity_id")
        for call in service_calls
        if call.domain == "light" and call.service == service
    }


async def _enter_circadian(hass: HomeAssistant, pin_circadian, cfg: dict, kelvin: float):
    """Set up with the clock pinned at `kelvin` and enter circadian."""
    await pin_circadian(50, kelvin, tick=False)
    await _setup(hass, cfg)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    return ctrl


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_entering_circadian_activates_route_for_current_colortemp(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
    pin_circadian,
) -> None:
    """5000 K: fluorescent on, strips (already off) not commanded."""
    await _enter_circadian(hass, pin_circadian, kitchen_with_routes_config, 5000)

    assert _TUBE in _targets(service_calls, "turn_on")
    assert _targets(service_calls, "turn_off").isdisjoint(_STRIPS)


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_colortemp_change_swaps_active_route(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
    pin_circadian,
) -> None:
    """A tick at 3000 K after 5000 K swaps the fluorescent for the strips."""
    await _enter_circadian(hass, pin_circadian, kitchen_with_routes_config, 5000)
    # Mocked light services do not update states: simulate the turn_on.
    hass.states.async_set(_TUBE, "on", {})

    service_calls.clear()
    await pin_circadian(50, 3000)

    assert _TUBE in _targets(service_calls, "turn_off")
    assert _targets(service_calls, "turn_on") >= _STRIPS


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_hysteresis_suppresses_flap_at_boundary(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
    pin_circadian,
) -> None:
    """Once banded, small overshoots within hysteresis must not swap."""
    await _enter_circadian(hass, pin_circadian, kitchen_with_routes_config, 5000)

    service_calls.clear()
    # 5510 K is within the +25 K hysteresis: still banded.
    await pin_circadian(50, 5510)

    assert _TUBE not in _targets(service_calls, "turn_off")
    assert _targets(service_calls, "turn_on").isdisjoint(_STRIPS)


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_missing_clock_values_select_fallback(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no clock values the fallback route (strips) is selected."""
    monkeypatch.setattr(CircadianClock, "values", lambda _self, _now=None: None)
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]

    service_calls.clear()
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()

    assert _targets(service_calls, "turn_on") >= _STRIPS
    # The fluorescent was already off, so it is not commanded.
    assert _TUBE not in _targets(service_calls, "turn_off")


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_tick_outside_circadian_does_not_route(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
    pin_circadian,
) -> None:
    """A kelvin change while the area is off sends no route command."""
    await pin_circadian(50, 5000, tick=False)
    await _setup(hass, kitchen_with_routes_config)
    ctrl = hass.data["area_lighting"]["controllers"]["kitchen"]
    await ctrl.lighting_off()
    await hass.async_block_till_done()

    service_calls.clear()
    await pin_circadian(50, 3000)

    routed = {_TUBE, *_STRIPS}
    assert [c for c in service_calls if c.data.get("entity_id") in routed] == []


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_select_scene_change_deactivates_router(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
    pin_circadian,
) -> None:
    """Leaving circadian through the current_scene property setter (the
    select entity's state-only path, which bypasses _activate_scene) must
    still deactivate the kelvin router, so a later tick cannot dispatch the
    stale route."""
    kitchen_with_routes_config["area_lighting"]["areas"][0]["scenes"].append(
        {
            "id": "bright",
            "name": "Bright",
            "entities": {_TUBE: {"state": "on", "brightness": 200}},
        }
    )
    ctrl = await _enter_circadian(hass, pin_circadian, kitchen_with_routes_config, 5000)
    assert ctrl._kelvin_router._active

    ctrl.current_scene = "bright"
    assert not ctrl._kelvin_router._active

    # A kelvin that would swap routes (banded -> fallback) issues no route
    # commands: the router is inactive.
    service_calls.clear()
    await pin_circadian(50, 3000)
    assert [c for c in service_calls if c.domain == "light"] == []


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_crossfade_passed_as_transition(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
    pin_circadian,
) -> None:
    """The configured crossfade_seconds is passed as `transition`."""
    await _enter_circadian(hass, pin_circadian, kitchen_with_routes_config, 5000)

    # The strips are already off, so only the fluorescent's turn_on fires.
    routed_calls = [c for c in service_calls if c.data.get("entity_id") == _TUBE]
    assert routed_calls
    assert all(call.data.get("transition") == 1.0 for call in routed_calls)


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_route_turn_on_carries_group_payload(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
    pin_circadian,
) -> None:
    """A grouped route light turns on with its group's brightness and kelvin."""
    await _enter_circadian(hass, pin_circadian, kitchen_with_routes_config, 5000)

    assert [dict(c.data) for c in service_calls if c.data.get("entity_id") == _TUBE] == [
        {"entity_id": _TUBE, "brightness": 255, "color_temp_kelvin": 5000, "transition": 1}
    ]


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_route_light_without_group_is_switched_only(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
    pin_circadian,
) -> None:
    """A route light with no group turns on with only a transition."""
    tube = kitchen_with_routes_config["area_lighting"]["areas"][0]["lights"][0]
    del tube["circadian_group"], tube["circadian_type"]
    await _enter_circadian(hass, pin_circadian, kitchen_with_routes_config, 5000)

    assert [dict(c.data) for c in service_calls if c.data.get("entity_id") == _TUBE] == [
        {"entity_id": _TUBE, "transition": 1}
    ]


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_tick_after_route_change_leaves_outgoing_lights_fading(
    hass: HomeAssistant,
    helper_entities,
    service_calls,
    kitchen_with_routes_config,
    pin_circadian,
) -> None:
    """The tick after a route change must not relight the outgoing grouped
    light that still reports on while its crossfade runs."""
    await _enter_circadian(hass, pin_circadian, kitchen_with_routes_config, 5000)
    hass.states.async_set(_TUBE, "on", {})
    await pin_circadian(50, 3000)
    assert _TUBE in _targets(service_calls, "turn_off")

    # The fluorescent's off state has not been reported yet, and its echo
    # window from the bring-up has passed.
    assert hass.states.get(_TUBE).state == "on"
    hass.data["area_lighting"]["controllers"]["kitchen"]._circadian_commanded_at.clear()
    service_calls.clear()
    await pin_circadian(50, 3000)

    assert _TUBE not in _targets(service_calls, "turn_on")


_MEDIA_ZONE = "light.media_zone"
_MEDIA_MEMBERS = ["light.media_1", "light.media_2", "light.media_3"]


@pytest.mark.integration
async def test_tick_after_cluster_route_change_leaves_outgoing_members_fading(
    hass: HomeAssistant, helper_entities, service_calls, pin_circadian
) -> None:
    """Under demand response a cluster route is driven as its members: the
    tick after a route change must not relight outgoing members that still
    report on, nor the cluster itself."""
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "media",
                    "name": "Media",
                    "event_handlers": True,
                    "circadian_groups": [{"name": "Media"}],
                    "lights": [
                        {"id": eid, "circadian_group": "Media", "circadian_type": "ct"}
                        for eid in [*_MEDIA_MEMBERS, "light.media_lamp"]
                    ],
                    "light_clusters": [{"id": _MEDIA_ZONE, "members": list(_MEDIA_MEMBERS)}],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "off", "name": "Off"},
                    ],
                    "circadian_kelvin_routes": {
                        "crossfade_seconds": 1.0,
                        "routes": [
                            {"kelvin_range": [4500, 5500], "lights": [_MEDIA_ZONE]},
                            {"lights": ["light.media_lamp"]},
                        ],
                    },
                }
            ]
        }
    }
    for eid in [*_MEDIA_MEMBERS, _MEDIA_ZONE, "light.media_lamp"]:
        hass.states.async_set(eid, "off", {})
    await pin_circadian(50, 5000, tick=False)
    await _setup(hass, cfg)
    hass.data["area_lighting"]["global"]._demand_response_active = True
    ctrl = hass.data["area_lighting"]["controllers"]["media"]
    await ctrl.lighting_circadian()
    await hass.async_block_till_done()
    assert ctrl.dr_shed_ids == frozenset({"light.media_3"})
    for eid in ("light.media_1", "light.media_2", _MEDIA_ZONE):
        hass.states.async_set(eid, "on", {})
    await pin_circadian(50, 3000)
    assert {"light.media_1", "light.media_2"} <= _targets(service_calls, "turn_off")

    # The members' off states have not been reported yet, and their echo
    # window from the bring-up has passed.
    assert all(hass.states.get(eid).state == "on" for eid in ("light.media_1", "light.media_2"))
    ctrl._circadian_commanded_at.clear()
    service_calls.clear()
    await pin_circadian(50, 3000)

    assert _targets(service_calls, "turn_on").isdisjoint({*_MEDIA_MEMBERS, _MEDIA_ZONE})


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_kitchen_entities")
async def test_routes_without_circadian_scene_logs_warning(
    hass: HomeAssistant,
    helper_entities,
    caplog,
) -> None:
    """An area with routes but no `circadian` scene logs a warning at startup."""
    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "event_handlers": True,
                    "circadian_groups": [{"name": "Kitchen"}],
                    "lights": [
                        {
                            "id": "light.kitchen_fluorescent",
                            "circadian_group": "Kitchen",
                            "circadian_type": "ct",
                        },
                        {
                            "id": "light.kitchen_strip_1",
                            "circadian_group": "Kitchen",
                            "circadian_type": "ct",
                        },
                    ],
                    "scenes": [
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "off", "name": "Off"},
                    ],
                    "circadian_kelvin_routes": {
                        "routes": [
                            {
                                "kelvin_range": [4500, 5500],
                                "lights": ["light.kitchen_fluorescent"],
                            },
                            {"lights": ["light.kitchen_strip_1"]},
                        ]
                    },
                }
            ]
        }
    }
    import logging

    caplog.set_level(logging.WARNING, logger="custom_components.area_lighting")
    await _setup(hass, cfg)
    assert any(
        "circadian_kelvin_routes" in record.message
        and "no `circadian` scene" in record.message
        and "kitchen" in record.message
        for record in caplog.records
    )
