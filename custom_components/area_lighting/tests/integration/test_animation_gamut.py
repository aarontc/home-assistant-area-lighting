"""Hue gamut reports retain ownership of animated colours."""

from unittest.mock import patch

import pytest

from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.controller import AreaLightingController
from custom_components.area_lighting.tests.integration.test_animation import (
    START,
    _activate,
    _advance,
    _area,
    _config,
    _setup,
)


@pytest.mark.integration
@pytest.mark.parametrize("self_heal", [True, False])
@pytest.mark.parametrize(
    ("palette", "reported"),
    [
        ([{"hs_color": [0, 100]}, {"hs_color": [128, 100]}], (0.17, 0.7)),
        ([{"hs_color": [0, 100]}, {"hs_color": [120, 100]}], (0.17, 0.7)),
        ([{"color_temp_kelvin": 2700}, {"hs_color": [120, 100]}], (0.17, 0.7)),
        ([{"color_temp_kelvin": 5000}, {"hs_color": [128, 100]}], (0.409, 0.518)),
    ],
    ids=["C-red-green128", "C-red-green120", "C-warm-green120", "B-white-green128"],
)
async def test_hue_green_echo_keeps_animation(
    hass, helper_entities, service_calls, freezer, palette, reported, self_heal
):
    freezer.move_to(START)
    cfg = _config(_area(count=1), scene_self_heal=self_heal)
    cfg[DOMAIN]["animations"]["festive"].update(transition=20, palette=palette)
    with patch.object(AreaLightingController, "_schedule_post_settle_selfcheck"):
        ctrl = (await _setup(hass, cfg))["den"]
        await _activate(hass, ctrl)
        await _advance(hass, freezer, 20)
        assert "animation_from" in ctrl._active_scene_targets["light.den_0"]
        await _advance(hass, freezer, 25)
        hass.states.async_set(
            "light.den_0", "on", {"brightness": 200, "xy_color": reported, "color_mode": "xy"}
        )
        await hass.async_block_till_done()

    assert ctrl.current_scene == "christmas"
    assert ctrl.diagnostic_snapshot()["animation"] is not None
