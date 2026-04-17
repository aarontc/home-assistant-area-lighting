"""Tests for alert pattern config parsing."""

from __future__ import annotations

import pytest

from custom_components.area_lighting.config_schema import parse_config
from custom_components.area_lighting.models import AlertPattern, AlertStep


@pytest.mark.unit
def test_parse_valid_alert_pattern() -> None:
    """A valid alert_patterns block parses into AlertPattern objects."""
    raw = {
        "areas": [],
        "alert_patterns": {
            "blue_alert": {
                "steps": [
                    {
                        "target": "color",
                        "state": "on",
                        "brightness": 255,
                        "rgb_color": [0, 0, 255],
                    },
                    {"target": "white", "state": "off"},
                ],
                "delay": 3.0,
                "restore": True,
            },
            "three_flashes": {
                "steps": [
                    {"target": "all", "state": "on", "brightness": 255, "delay": 1.0},
                    {"target": "all", "state": "off", "delay": 1.0},
                ],
                "repeat": 3,
                "start_inverted": True,
                "restore": True,
            },
        },
    }
    config = parse_config(raw)
    assert len(config.alert_patterns) == 2
    blue = config.alert_patterns["blue_alert"]
    assert isinstance(blue, AlertPattern)
    assert len(blue.steps) == 2
    assert blue.steps[0].target == "color"
    assert blue.steps[0].brightness == 255
    assert blue.steps[0].rgb_color == (0, 0, 255)
    assert blue.delay == 3.0
    assert blue.restore is True
    assert blue.repeat == 1  # default

    flashes = config.alert_patterns["three_flashes"]
    assert flashes.repeat == 3
    assert flashes.start_inverted is True


@pytest.mark.unit
def test_parse_defaults_applied() -> None:
    """Missing optional fields get correct defaults."""
    raw = {
        "areas": [],
        "alert_patterns": {
            "simple": {
                "steps": [{"target": "all", "state": "on"}],
            },
        },
    }
    config = parse_config(raw)
    p = config.alert_patterns["simple"]
    assert p.repeat == 1
    assert p.delay == 0.0
    assert p.start_inverted is False
    assert p.restore is True
    assert p.steps[0].delay == 0.0
    assert p.steps[0].brightness is None


@pytest.mark.unit
def test_parse_no_alert_patterns() -> None:
    """Config without alert_patterns parses with an empty dict."""
    raw = {"areas": []}
    config = parse_config(raw)
    assert config.alert_patterns == {}
