"""Config schema parse tests — new fields land, removed fields rejected."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.mark.integration
async def test_brightness_step_pct_parses(hass: HomeAssistant, helper_entities) -> None:
    """brightness_step_pct is accepted and propagates to AreaConfig."""
    assert await async_setup_component(
        hass,
        "area_lighting",
        {
            "area_lighting": {
                "areas": [
                    {
                        "id": "test_area",
                        "name": "Test Area",
                        "event_handlers": False,
                        "brightness_step_pct": 25,
                        "scenes": [{"id": "circadian", "name": "Circadian"}],
                    }
                ]
            }
        },
    )
    await hass.async_block_till_done()
    config = hass.data["area_lighting"]["config"]
    area = config.area_by_id("test_area")
    assert area.brightness_step_pct == 25


@pytest.mark.integration
async def test_night_fadeout_seconds_parses(hass: HomeAssistant, helper_entities) -> None:
    assert await async_setup_component(
        hass,
        "area_lighting",
        {
            "area_lighting": {
                "areas": [
                    {
                        "id": "test_area",
                        "name": "Test Area",
                        "event_handlers": False,
                        "night_fadeout_seconds": 2.5,
                        "scenes": [{"id": "circadian", "name": "Circadian"}],
                    }
                ]
            }
        },
    )
    await hass.async_block_till_done()
    area = hass.data["area_lighting"]["config"].area_by_id("test_area")
    assert area.night_fadeout_seconds == 2.5


@pytest.mark.integration
async def test_follow_area_id_rejected(hass: HomeAssistant, helper_entities) -> None:
    """follow_area_id is no longer part of the schema."""
    result = await async_setup_component(
        hass,
        "area_lighting",
        {
            "area_lighting": {
                "areas": [
                    {
                        "id": "test_area",
                        "name": "Test Area",
                        "follow_area_id": "other_area",
                        "scenes": [{"id": "circadian", "name": "Circadian"}],
                    }
                ]
            }
        },
    )
    # Setup returns False on schema validation failure
    assert result is False


@pytest.mark.integration
async def test_light_followers_top_level_rejected(hass: HomeAssistant, helper_entities) -> None:
    result = await async_setup_component(
        hass,
        "area_lighting",
        {
            "area_lighting": {
                "areas": [
                    {
                        "id": "test_area",
                        "name": "Test Area",
                        "scenes": [{"id": "circadian", "name": "Circadian"}],
                    }
                ],
                "light_followers": [{"name": "f", "leader": "light.a", "follower": "light.b"}],
            }
        },
    )
    assert result is False


@pytest.mark.integration
async def test_standalone_remotes_top_level_rejected(hass: HomeAssistant, helper_entities) -> None:
    result = await async_setup_component(
        hass,
        "area_lighting",
        {
            "area_lighting": {
                "areas": [
                    {
                        "id": "test_area",
                        "name": "Test Area",
                        "scenes": [{"id": "circadian", "name": "Circadian"}],
                    }
                ],
                "standalone_remotes": [{"id": "abc", "name": "SR"}],
            }
        },
    )
    assert result is False
