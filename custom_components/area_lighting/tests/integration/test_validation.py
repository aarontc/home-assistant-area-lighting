"""External entity validator tests (D10)."""

from __future__ import annotations

import logging

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


async def _setup_minimal(hass: HomeAssistant, cfg: dict) -> bool:
    return await async_setup_component(hass, "area_lighting", cfg)


@pytest.mark.integration
async def test_validator_all_entities_present_no_error(
    hass: HomeAssistant, helper_entities, network_room_config, caplog
) -> None:
    caplog.set_level(logging.ERROR)
    assert await _setup_minimal(hass, network_room_config)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()
    assert not any(
        "required external entities are missing" in rec.message
        for rec in caplog.records
    )


@pytest.mark.integration
async def test_validator_missing_holiday_mode_logs_error(
    hass: HomeAssistant, network_room_config, caplog
) -> None:
    # Set up helper_entities WITHOUT input_select.holiday_mode
    from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
    from homeassistant.components.input_select import DOMAIN as INPUT_SELECT_DOMAIN

    assert await async_setup_component(
        hass,
        INPUT_SELECT_DOMAIN,
        {
            "input_select": {
                "ambient_scene": {
                    "name": "Ambient Scene",
                    "options": ["ambient", "holiday"],
                    "initial": "ambient",
                }
                # intentionally no holiday_mode
            }
        },
    )
    assert await async_setup_component(
        hass,
        INPUT_BOOLEAN_DOMAIN,
        {
            "input_boolean": {
                "lighting_circadian_daylight_lights_enabled": {"initial": True},
                "motion_light_enabled": {"initial": True},
                "lighting_upstairs_ambient": {"initial": False},
            }
        },
    )
    hass.states.async_set("sensor.circadian_values", "0", {"colortemp": 3500})
    hass.states.async_set(
        "switch.circadian_lighting_network_room_overhead_circadian",
        "off",
        {"brightness": 75.0, "colortemp": 3500},
    )
    await hass.async_block_till_done()

    caplog.set_level(logging.ERROR)
    assert await _setup_minimal(hass, network_room_config)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    # Integration still loads
    assert "network_room" in hass.data["area_lighting"]["controllers"]
    # And the error was logged
    error_messages = [
        rec.message for rec in caplog.records if rec.levelno >= logging.ERROR
    ]
    assert any("holiday_mode" in m for m in error_messages)
    assert any("required external entities are missing" in m for m in error_messages)


@pytest.mark.integration
async def test_validator_multiple_missing_single_error_line(
    hass: HomeAssistant, network_room_config, caplog
) -> None:
    """Multiple missing entities → single log error listing all of them."""
    hass.states.async_set(
        "switch.circadian_lighting_network_room_overhead_circadian",
        "off",
        {"brightness": 75.0, "colortemp": 3500},
    )
    await hass.async_block_till_done()
    caplog.set_level(logging.ERROR)
    assert await _setup_minimal(hass, network_room_config)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    validator_errors = [
        rec.message
        for rec in caplog.records
        if rec.levelno >= logging.ERROR
        and "required external entities are missing" in rec.message
    ]
    assert len(validator_errors) == 1
    msg = validator_errors[0]
    assert "holiday_mode" in msg
    assert "ambient_scene" in msg
    assert "lighting_circadian_daylight_lights_enabled" in msg
    assert "motion_light_enabled" in msg
    assert "sensor.circadian_values" in msg


# ── Repairs issue + YAML bootstrap block (bug 2) ────────────────────────


@pytest.mark.integration
async def test_validator_creates_repairs_issue_when_entities_missing(
    hass: HomeAssistant, network_room_config
) -> None:
    """A missing-entity state must raise an HA repairs issue (not just a log)."""
    from homeassistant.helpers import issue_registry as ir

    # Only set up the circadian switch, nothing else
    hass.states.async_set(
        "switch.circadian_lighting_network_room_overhead_circadian",
        "off",
        {"brightness": 75.0, "colortemp": 3500},
    )
    await hass.async_block_till_done()
    assert await _setup_minimal(hass, network_room_config)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    issue = registry.async_get_issue("area_lighting", "missing_external_entities")
    assert issue is not None, "expected a 'missing_external_entities' issue"
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.is_persistent is False


@pytest.mark.integration
async def test_validator_issue_includes_yaml_bootstrap(
    hass: HomeAssistant, network_room_config
) -> None:
    """The issue's translation placeholders must include a YAML bootstrap
    block the user can copy into configuration.yaml to create the missing
    input_select / input_boolean / sensor entities.
    """
    from homeassistant.helpers import issue_registry as ir

    # No helpers at all — every required entity is missing.
    hass.states.async_set(
        "switch.circadian_lighting_network_room_overhead_circadian",
        "off",
        {"brightness": 75.0, "colortemp": 3500},
    )
    await hass.async_block_till_done()
    assert await _setup_minimal(hass, network_room_config)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    issue = registry.async_get_issue("area_lighting", "missing_external_entities")
    assert issue is not None
    placeholders = issue.translation_placeholders or {}
    bootstrap = placeholders.get("bootstrap_yaml", "")
    # The YAML must be copy-pasteable for every input_* helper we read
    assert "input_select:" in bootstrap
    assert "holiday_mode:" in bootstrap
    assert "options:" in bootstrap
    assert "christmas" in bootstrap
    assert "ambient_scene:" in bootstrap
    assert "input_boolean:" in bootstrap
    assert "lighting_circadian_daylight_lights_enabled:" in bootstrap
    assert "motion_light_enabled:" in bootstrap
    assert "lighting_upstairs_ambient:" in bootstrap  # the ambient zone from network_room
    # And a list of the missing entities, so the user knows what's wrong
    missing_list = placeholders.get("missing_list", "")
    assert "input_select.holiday_mode" in missing_list
    assert "sensor.circadian_values" in missing_list


@pytest.mark.integration
async def test_validator_issue_cleared_when_entities_present(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """When all required entities exist, the repairs issue should not exist
    (or should be cleared if a previous run created one)."""
    from homeassistant.helpers import issue_registry as ir

    assert await _setup_minimal(hass, network_room_config)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    issue = registry.async_get_issue("area_lighting", "missing_external_entities")
    assert issue is None


# ── Circadian_lighting bootstrap YAML + switch-light mapping verification ──


@pytest.mark.integration
async def test_bootstrap_yaml_includes_circadian_lighting_top_level(
    hass: HomeAssistant, network_room_config
) -> None:
    """The bootstrap YAML must include the `circadian_lighting:` top-level
    block (configures the HACS integration itself — interval, min/max
    colortemp) when sensor.circadian_values is missing."""
    from homeassistant.helpers import issue_registry as ir

    # Nothing set up — everything is missing
    await hass.async_block_till_done()
    assert await _setup_minimal(hass, network_room_config)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    issue = registry.async_get_issue("area_lighting", "missing_external_entities")
    assert issue is not None
    bootstrap = (issue.translation_placeholders or {}).get("bootstrap_yaml", "")
    # The top-level circadian_lighting: block must be present
    assert "circadian_lighting:" in bootstrap
    assert "min_colortemp:" in bootstrap
    assert "max_colortemp:" in bootstrap
    assert "interval:" in bootstrap


@pytest.mark.integration
async def test_bootstrap_yaml_includes_circadian_switches_block(
    hass: HomeAssistant, network_room_config
) -> None:
    """The bootstrap YAML must include a `switch:` block listing each
    circadian_lighting switch needed by the configured areas, with the
    lights grouped by `circadian_type` ('lights_ct', 'lights_brightness',
    or 'lights_rgb')."""
    from homeassistant.helpers import issue_registry as ir

    await hass.async_block_till_done()
    assert await _setup_minimal(hass, network_room_config)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    issue = registry.async_get_issue("area_lighting", "missing_external_entities")
    assert issue is not None
    bootstrap = (issue.translation_placeholders or {}).get("bootstrap_yaml", "")
    # The switch: top-level block must appear
    assert "switch:" in bootstrap
    # For the network_room area, the switch should be named "Network Room Overhead Circadian"
    assert "Network Room Overhead Circadian" in bootstrap
    assert "platform: circadian_lighting" in bootstrap
    assert "lights_ct:" in bootstrap
    assert "light.network_room_overhead_1" in bootstrap
    assert "light.network_room_overhead_2" in bootstrap
    # Per-switch brightness ranges from the config
    assert "max_brightness: 100" in bootstrap
    assert "min_brightness: 65" in bootstrap


@pytest.mark.integration
async def test_validator_flags_light_referenced_by_circadian_switch_but_missing(
    hass: HomeAssistant, helper_entities, caplog
) -> None:
    """If a light assigned to a circadian switch doesn't exist as an
    HA entity, the validator must flag it as missing."""
    import logging

    cfg = {
        "area_lighting": {
            "areas": [
                {
                    "id": "test_area",
                    "name": "Test Area",
                    "event_handlers": True,
                    "circadian_switches": [
                        {"name": "Main", "max_brightness": 100, "min_brightness": 50}
                    ],
                    "lights": [
                        {
                            "id": "light.nonexistent_light",
                            "circadian_switch": "Main",
                            "circadian_type": "ct",
                            "roles": ["color"],
                        }
                    ],
                    "scenes": [{"id": "circadian", "name": "Circadian"}],
                }
            ]
        }
    }
    # The circadian switch entity itself is "present" via async_set, but
    # the light it's supposed to control doesn't exist.
    hass.states.async_set(
        "switch.circadian_lighting_test_area_main_circadian",
        "off",
        {"brightness": 75.0, "colortemp": 3500},
    )
    await hass.async_block_till_done()

    caplog.set_level(logging.ERROR)
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()

    # An error must be logged about the missing light
    error_messages = [
        rec.message for rec in caplog.records if rec.levelno >= logging.ERROR
    ]
    assert any("light.nonexistent_light" in m for m in error_messages)
