"""The built-in circadian clock, its sensor and the global `circadian:` block."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
import voluptuous as vol
from astral import Observer
from astral import sun as astral_sun
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.area_lighting import CONFIG_SCHEMA, async_teardown, circadian
from custom_components.area_lighting.circadian_clock import CircadianClock, CircadianValues
from custom_components.area_lighting.config_schema import parse_config
from custom_components.area_lighting.const import DOMAIN, SIGNAL_CIRCADIAN_TICK
from custom_components.area_lighting.models import CircadianConfig

SENSOR = "sensor.area_lighting_circadian"


async def _setup(hass: HomeAssistant, cfg: dict) -> CircadianClock:
    assert await async_setup_component(hass, DOMAIN, cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    return hass.data[DOMAIN]["circadian_clock"]


def _record_ticks(hass: HomeAssistant) -> list[CircadianValues]:
    received: list[CircadianValues] = []

    @callback
    def _record(values: CircadianValues) -> None:
        received.append(values)

    async_dispatcher_connect(hass, SIGNAL_CIRCADIAN_TICK, _record)
    return received


@pytest.mark.integration
async def test_clock_sends_signal_every_interval(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    network_room_config[DOMAIN]["circadian"] = {"interval": 60}
    clock = await _setup(hass, network_room_config)
    received = _record_ticks(hass)
    start = dt_util.utcnow()

    async_fire_time_changed(hass, start + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert len(received) == 1

    async_fire_time_changed(hass, start + timedelta(seconds=121))
    await hass.async_block_till_done()
    assert len(received) == 2
    assert received[-1] == clock.last


@pytest.mark.integration
async def test_sensor_reports_percent_and_colortemp(
    hass: HomeAssistant, helper_entities, network_room_config, pin_circadian
) -> None:
    await pin_circadian(42.126, 4321.6, tick=False)
    await _setup(hass, network_room_config)

    state = hass.states.get(SENSOR)
    assert state is not None
    assert float(state.state) == 42.13
    assert state.attributes["colortemp"] == 4322
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["friendly_name"] == "Area Lighting Circadian"
    assert state.attributes["icon"] == "mdi:theme-light-dark"


@pytest.mark.integration
async def test_sensor_exists_before_home_assistant_starts(
    hass: HomeAssistant, helper_entities, network_room_config, pin_circadian
) -> None:
    """Automations attach their triggers when Home Assistant starts, and a
    numeric_state trigger on an entity that does not exist yet fails to set
    up. The sensor therefore has to exist once setup returns."""
    await pin_circadian(42.0, 4321.0, tick=False)
    assert await async_setup_component(hass, DOMAIN, network_room_config)
    await hass.async_block_till_done()

    state = hass.states.get(SENSOR)
    assert state is not None
    assert state.attributes["colortemp"] == 4321


@pytest.mark.integration
async def test_numeric_state_automation_on_the_sensor_sets_up_at_startup(
    hass: HomeAssistant,
    helper_entities,
    network_room_config,
    pin_circadian,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """While Home Assistant starts, automations attach their triggers before
    EVENT_HOMEASSISTANT_STARTED fires. A numeric_state trigger on the sensor
    must find it then, without the "unknown entity" warning, and fire when the
    color temperature crosses."""
    hass.set_state(CoreState.starting)
    await pin_circadian(-5.0, 3000.0, tick=False)
    assert await async_setup_component(hass, DOMAIN, network_room_config)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "trigger": {
                    "platform": "numeric_state",
                    "entity_id": SENSOR,
                    "value_template": "{{ state.attributes.colortemp }}",
                    "above": 4000,
                },
                "action": {"event": "circadian_daylight"},
            }
        },
    )
    await hass.async_block_till_done()
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    fired: list = []
    hass.bus.async_listen("circadian_daylight", fired.append)
    await pin_circadian(5.0, 4500.0)

    assert len(fired) == 1
    assert "Error initializing" not in caplog.text


@pytest.mark.integration
async def test_sensor_updates_on_tick(
    hass: HomeAssistant, helper_entities, network_room_config, pin_circadian
) -> None:
    await _setup(hass, network_room_config)

    await pin_circadian(10.0, 2800.0)
    state = hass.states.get(SENSOR)
    assert float(state.state) == 10.0
    assert state.attributes["colortemp"] == 2800

    await pin_circadian(-50.0, 2500.0)
    state = hass.states.get(SENSOR)
    assert float(state.state) == -50.0
    assert state.attributes["colortemp"] == 2500


@pytest.mark.integration
def test_circadian_block_defaults_match_circadian_lighting() -> None:
    validated = CONFIG_SCHEMA({DOMAIN: {"areas": []}})[DOMAIN]

    assert parse_config(validated).circadian == CircadianConfig(
        min_colortemp=2500, max_colortemp=5500, interval=300, transition=60
    )
    assert parse_config({"areas": []}).circadian == CircadianConfig()


@pytest.mark.integration
def test_circadian_block_rejects_min_colortemp_above_max() -> None:
    with pytest.raises(vol.Invalid, match="min_colortemp"):
        CONFIG_SCHEMA(
            {DOMAIN: {"areas": [], "circadian": {"min_colortemp": 6000, "max_colortemp": 3000}}}
        )


@pytest.mark.integration
async def test_clock_events_match_astral_for_hass_config_location(hass: HomeAssistant) -> None:
    hass.config.elevation = 150
    clock = CircadianClock(hass, CircadianConfig())
    observer = Observer(hass.config.latitude, hass.config.longitude, hass.config.elevation)
    today = datetime(2026, 6, 21).date()

    for hour in range(0, 24, 3):
        now = dt_util.as_utc(
            datetime(2026, 6, 21, hour, 7, tzinfo=dt_util.get_time_zone(hass.config.time_zone))
        )
        expected = [
            (name, getattr(astral_sun, name)(observer, today + timedelta(days=offset)).timestamp())
            for offset in (-1, 0, 1)
            for name in ("sunrise", "sunset", "noon", "midnight")
        ]
        window = circadian.relevant_events(expected, now.timestamp())

        events = clock.events(now)
        assert events == window
        assert set(events) == {"sunrise", "sunset", "noon", "midnight"}
        assert sum(ts <= now.timestamp() for ts in events.values()) == 2
        pct = circadian.percent(window, now.timestamp())
        assert clock.values(now) == CircadianValues(pct, circadian.kelvin(pct, 2500, 5500))


@pytest.mark.integration
async def test_clock_keeps_last_values_when_sun_events_are_missing(
    hass: HomeAssistant, helper_entities, network_room_config, caplog
) -> None:
    clock = await _setup(hass, network_room_config)
    await clock.async_tick()
    last = clock.last
    assert last is not None
    received = _record_ticks(hass)

    with (
        caplog.at_level(logging.WARNING),
        patch(
            "custom_components.area_lighting.circadian_clock.get_astral_event_date",
            return_value=None,
        ),
    ):
        await clock.async_tick()
        await clock.async_tick()
        await hass.async_block_till_done()

    assert clock.last is last
    assert received == []
    warnings = [
        r
        for r in caplog.records
        if r.name == "custom_components.area_lighting.circadian_clock"
        and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1


@pytest.mark.integration
async def test_teardown_stops_the_clock_interval(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    network_room_config[DOMAIN]["circadian"] = {"interval": 60}
    clock = await _setup(hass, network_room_config)
    received = _record_ticks(hass)

    await async_teardown(hass)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()

    assert received == []
    clock.async_stop()


@pytest.mark.integration
async def test_pin_circadian_sets_values_and_ticks(
    hass: HomeAssistant, helper_entities, network_room_config, pin_circadian
) -> None:
    clock = await _setup(hass, network_room_config)
    received = _record_ticks(hass)

    await pin_circadian(25.0, 3250.0)

    pinned = CircadianValues(25.0, 3250.0)
    assert received == [pinned]
    assert clock.last == pinned
    assert clock.values() == pinned

    await pin_circadian(-10.0, 2500.0, tick=False)
    assert received == [pinned]
    assert clock.values() == CircadianValues(-10.0, 2500.0)
