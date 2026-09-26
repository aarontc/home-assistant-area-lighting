"""Pure-unit tests for building light.turn_on arguments from scene targets."""

from __future__ import annotations

from custom_components.area_lighting.light_targets import light_turn_on_data


def test_keeps_only_the_color_key_named_by_color_mode():
    target = {
        "state": "on",
        "brightness": 120,
        "color_mode": "hs",
        "hs_color": [30.0, 80.0],
        "rgb_color": [255, 150, 51],
        "xy_color": [0.55, 0.4],
    }
    assert light_turn_on_data(target) == {"brightness": 120, "hs_color": [30.0, 80.0]}


def test_mired_color_temp_becomes_kelvin():
    assert light_turn_on_data({"state": "on", "color_temp": 370}) == {"color_temp_kelvin": 2702}


def test_mired_color_temp_from_yaml_string():
    assert light_turn_on_data({"color_temp": "370"}) == {"color_temp_kelvin": 2702}


def test_existing_kelvin_wins_over_mired():
    target = {"color_temp": 370, "color_temp_kelvin": 2700}
    assert light_turn_on_data(target) == {"color_temp_kelvin": 2700}


def test_white_mode_sends_white_at_the_saved_brightness():
    target = {"state": "on", "color_mode": "white", "brightness": 150}
    assert light_turn_on_data(target) == {"brightness": 150, "white": 150}


def test_missing_native_color_falls_back_to_the_first_reported():
    """An RGBW snapshot from before rgbw_color was captured."""
    target = {"color_mode": "rgbw", "rgb_color": [255, 0, 0], "hs_color": [0.0, 100.0]}
    assert light_turn_on_data(target) == {"hs_color": [0.0, 100.0]}


def test_without_color_mode_a_single_config_color_passes_through():
    target = {"state": "on", "rgbw_color": [255, 0, 0, 40], "effect": "colorloop"}
    assert light_turn_on_data(target) == {"rgbw_color": [255, 0, 0, 40], "effect": "colorloop"}


def test_without_color_mode_the_first_color_in_ha_order_wins():
    target = {"xy_color": [0.3, 0.3], "color_temp_kelvin": 3000}
    assert light_turn_on_data(target) == {"color_temp_kelvin": 3000}


def test_colorless_modes_send_no_color():
    target = {"color_mode": "brightness", "brightness": 90, "hs_color": [1.0, 2.0], "effect": None}
    assert light_turn_on_data(target) == {"brightness": 90}
