"""Configuration and model helpers for `<holiday>_ambient` scenes."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting import CONFIG_SCHEMA, scene_machine
from custom_components.area_lighting.config_schema import AREA_SCHEMA, parse_config
from custom_components.area_lighting.const import (
    HOLIDAY_AMBIENT_SCENES,
    HOLIDAY_SCENES,
    holiday_ambient_scene,
)
from custom_components.area_lighting.models import AreaConfig
from custom_components.area_lighting.scene import _scene_icon


def _area(*slugs: str, **extra) -> dict:
    return {
        "id": "kitchen",
        "name": "Kitchen",
        "scenes": [{"id": slug, "name": slug.title()} for slug in slugs],
        **extra,
    }


def _parse(area: dict) -> AreaConfig:
    return parse_config(CONFIG_SCHEMA({"area_lighting": {"areas": [area]}})["area_lighting"]).areas[
        0
    ]


def _remote(buttons: dict) -> list[dict]:
    return [{"id": "abc123", "name": "Entry Remote", "buttons": buttons}]


def test_constants():
    assert {"christmas_ambient", "halloween_ambient"} == HOLIDAY_AMBIENT_SCENES
    assert holiday_ambient_scene("christmas") == "christmas_ambient"


def test_scene_machine_uses_the_shared_holiday_constant():
    assert scene_machine.HOLIDAY_SCENES is HOLIDAY_SCENES


@pytest.mark.parametrize("slug", ["christmas_ambient", "halloween_ambient"])
def test_holiday_ambient_scene_id_accepted(slug):
    holiday = slug.removesuffix("_ambient")
    area = _parse(_area(holiday, slug))
    assert slug in area.last_scene_options


@pytest.mark.parametrize("slug", ["christmas_ambient", "halloween_ambient"])
def test_holiday_ambient_without_its_holiday_is_rejected(slug):
    holiday = slug.removesuffix("_ambient")
    with pytest.raises(
        vol.Invalid, match=rf"area 'kitchen': scene '{slug}' requires a '{holiday}' scene"
    ):
        AREA_SCHEMA(_area("ambient", slug))


def test_on_sequence_rejects_holiday_ambient():
    area = _area("christmas", "christmas_ambient", "study")
    area["lutron_remotes"] = _remote({"on": ["study", "christmas_ambient"]})
    with pytest.raises(vol.Invalid, match=r"buttons.on cannot list 'christmas_ambient'"):
        _parse(area)


def test_favorite_and_linked_motion_may_name_holiday_ambient():
    area = _area("christmas", "christmas_ambient")
    area["lutron_remotes"] = _remote({"favorite": ["christmas", "christmas_ambient"]})
    area["linked_motion"] = [
        {"remote_area": "hall", "default": {"local_scene": "christmas_ambient"}}
    ]
    parsed = _parse(area)
    assert parsed.lutron_remotes[0].favorite_cycle == ["christmas", "christmas_ambient"]
    assert parsed.linked_motion[0].default.local_scene == "christmas_ambient"


def test_ambient_like_slugs_in_an_unsplit_area():
    area = _parse(_area("ambient", "christmas", "halloween", "evening"))
    assert area.ambient_like_slugs == {"ambient", "christmas", "halloween"}


def test_ambient_like_slugs_in_a_split_area():
    area = _parse(_area("ambient", "christmas", "christmas_ambient", "halloween"))
    assert area.ambient_like_slugs == {"ambient", "christmas_ambient", "halloween"}


def test_ambient_like_slugs_without_any_scenes():
    assert AreaConfig(id="den", name="Den").ambient_like_slugs == {
        "ambient",
        "christmas",
        "halloween",
    }


def test_holiday_ambient_slug():
    split = _parse(_area("christmas", "christmas_ambient", "halloween"))
    assert split.holiday_ambient_slug("christmas") == "christmas_ambient"
    assert split.holiday_ambient_slug("halloween") == "halloween"


@pytest.mark.parametrize("holiday", sorted(HOLIDAY_SCENES))
def test_holiday_ambient_scene_shares_the_holiday_icon(holiday):
    assert _scene_icon(holiday_ambient_scene(holiday)) == _scene_icon(holiday)
