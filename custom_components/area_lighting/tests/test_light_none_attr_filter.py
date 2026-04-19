"""Regression: light.turn_on calls must not forward None-valued attributes.

Hue's 2025 deprecation warns on `effect=None`; more generally, passing
None for any light service attribute is never meaningful and can surface
as warnings or errors from downstream integrations.

Covers the two turn_on assembly sites in the integration:

- `AreaLightingController._apply_light_state` in `controller.py`
- `Scene._apply_stored` in `scene.py`

Both loops copy a fixed set of attributes from stored state_data into
the service-call kwargs; both must skip keys whose value is None.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.area_lighting.controller import AreaLightingController
from custom_components.area_lighting.models import AreaConfig, AreaLightingConfig, SceneConfig


def _make_controller() -> AreaLightingController:
    area = AreaConfig(
        id="study",
        name="Study",
        scenes=[SceneConfig(slug="circadian", name="Circadian", area_id="study")],
    )
    hass = MagicMock()
    hass.data = {}
    return AreaLightingController(hass, area, AreaLightingConfig(areas=[area]))


@pytest.mark.asyncio
async def test_apply_light_state_skips_none_attributes():
    """State data with `effect: None` must not pass effect to turn_on."""
    ctrl = _make_controller()
    ctrl._call_service = AsyncMock()

    state_data = {
        "state": "on",
        "brightness": 180,
        "effect": None,
        "rgb_color": None,
        "color_temp_kelvin": 3000,
    }
    await ctrl._apply_light_state("light.study_main", state_data)

    ctrl._call_service.assert_awaited_once()
    args, kwargs = ctrl._call_service.call_args
    assert args == ("light.turn_on",)
    assert kwargs["entity_id"] == "light.study_main"
    assert kwargs["brightness"] == 180
    assert kwargs["color_temp_kelvin"] == 3000
    assert "effect" not in kwargs
    assert "rgb_color" not in kwargs


@pytest.mark.asyncio
async def test_apply_light_state_forwards_real_effect_value():
    """A non-None `effect` must still be forwarded unchanged."""
    ctrl = _make_controller()
    ctrl._call_service = AsyncMock()

    state_data = {"state": "on", "brightness": 100, "effect": "colorloop"}
    await ctrl._apply_light_state("light.study_main", state_data)

    _, kwargs = ctrl._call_service.call_args
    assert kwargs["effect"] == "colorloop"
    assert kwargs["brightness"] == 100


@pytest.mark.asyncio
async def test_apply_light_state_off_state_ignores_attrs():
    """state=off must call turn_off and never forward any attrs."""
    ctrl = _make_controller()
    ctrl._call_service = AsyncMock()

    state_data = {"state": "off", "brightness": 50, "effect": None}
    await ctrl._apply_light_state("light.study_main", state_data)

    args, kwargs = ctrl._call_service.call_args
    assert args == ("light.turn_off",)
    assert "brightness" not in kwargs
    assert "effect" not in kwargs
