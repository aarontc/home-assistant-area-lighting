"""Tests for shared animation steps and scene target overlays."""

from collections import Counter
from copy import deepcopy

import pytest
from homeassistant.util import color as color_util

from custom_components.area_lighting import animation
from custom_components.area_lighting.animation import (
    animated_light_ids,
    build_step_targets,
    expand_slots,
    step_number,
)
from custom_components.area_lighting.models import AnimationConfig

RED = {"hs_color": [0, 100]}
GREEN = {"hs_color": [128, 100]}
WHITE = {"color_temp_kelvin": 5000}


@pytest.mark.parametrize(
    ("palette", "expected"),
    [
        ([RED], [RED]),
        ([RED, GREEN, WHITE], [RED, GREEN, WHITE]),
        ([dict(RED, weight=2), dict(GREEN, weight=2), WHITE], [RED, GREEN, RED, GREEN, WHITE]),
        ([RED, GREEN, dict(WHITE, weight=2)], [RED, GREEN, WHITE, WHITE]),
        ([dict(RED, weight=3), GREEN], [RED, RED, RED, GREEN]),
        ([dict(RED, weight=3), GREEN, WHITE], [RED, RED, GREEN, RED, WHITE]),
        ([dict(RED, weight=4), dict(GREEN, weight=2)], [RED, RED, GREEN, RED, RED, GREEN]),
    ],
)
def test_expand_weighted_slots(palette, expected):
    original = deepcopy(palette)
    assert expand_slots(palette) == expected
    assert expand_slots(palette) == expected
    assert palette == original


def test_majority_weight_has_only_the_unavoidable_cyclic_adjacency():
    slots = expand_slots([dict(RED, weight=3), GREEN, WHITE])
    neighbors = zip(slots, slots[1:] + slots[:1], strict=True)
    assert sum(left == right for left, right in neighbors) == 1


@pytest.mark.parametrize(
    ("timestamp", "period", "expected"),
    [(0, 30, 0), (29.999, 30, 0), (30, 30, 1), (90.5, 30, 3), (3.75, 1.5, 2), (-0.1, 30, -1)],
)
def test_step_number_uses_epoch_boundaries(timestamp, period, expected):
    assert step_number(timestamp, period) == expected


def test_light_selection_preserves_scene_order_and_excludes_static_targets():
    entities = {
        "light.third": {"state": "on", "rgb_color": [255, 0, 0]},
        "light.white": {"state": "on", "color_temp_kelvin": 2700},
        "light.mired": {"state": "on", "color_temp": 370},
        "light.dim": {"state": "on", "brightness": 100},
        "switch.color": {"state": "on", **RED},
        "light.fixed": {"state": "on", **RED, "animate": False},
        "light.off": {"state": "off", **RED},
        "light.unspecified": {**RED},
        "light.cluster": {"state": "on", **RED},
        "light.first": {"state": "on", "rgbw_color": [1, 2, 3, 4]},
        "light.second": {"state": "on", "rgbww_color": [1, 2, 3, 4, 5]},
        "light.xy": {"state": "on", "xy_color": [0.2, 0.3]},
    }
    assert animated_light_ids(entities, {"light.cluster"}) == [
        "light.third",
        "light.first",
        "light.second",
        "light.xy",
    ]


def test_light_selection_skips_a_resolved_circadian_target():
    """An rgb light's circadian payload carries rgb_color, but the curve drives it."""
    entities = {
        "light.colour": {"state": "on", **RED},
        "light.circadian": {
            "state": "on",
            "circadian": True,
            "brightness": 255,
            "rgb_color": (255, 206, 166),
        },
    }
    assert animated_light_ids(entities) == ["light.colour"]


def test_rotation_preserves_weight_counts_and_advances_every_light():
    animation = AnimationConfig(30, 20, [dict(RED, weight=2), dict(GREEN, weight=2), WHITE])
    entities = {f"light.bulb_{i}": {"state": "on", **RED} for i in range(10)}
    slots = expand_slots(animation.palette)
    for step in range(6):
        targets = build_step_targets(entities, animation, step)
        colors = [
            tuple(
                (key, tuple(value) if isinstance(value, list) else value)
                for key, value in target.items()
                if key != "state"
            )
            for target in targets.values()
        ]
        assert sorted(Counter(colors).values()) == [2, 4, 4]
        for index, target in enumerate(targets.values()):
            assert target == {"state": "on", **slots[(index + step) % len(slots)]}


def test_target_overlay_replaces_all_colors_and_preserves_other_attributes():
    entities = {
        "light.a": {
            "state": "on",
            "brightness": 200,
            "hs_color": [20, 50],
            "rgb_color": [255, 0, 0],
            "rgbw_color": [1, 2, 3, 4],
            "rgbww_color": [1, 2, 3, 4, 5],
            "xy_color": [0.2, 0.3],
            "color_temp_kelvin": 2700,
            "color_temp": 370,
            "color_mode": "rgb",
            "effect": "sparkle",
            "animate": True,
        },
        "light.b": {"state": "on", "brightness": 123, **RED},
        "light.fixed": {"state": "on", **GREEN, "animate": False},
        "light.cluster": {"state": "on", **RED},
    }
    original = deepcopy(entities)
    targets = build_step_targets(
        entities, AnimationConfig(30, 20, [dict(WHITE, brightness=90), GREEN]), 0, {"light.cluster"}
    )
    assert targets == {
        "light.a": {"state": "on", "brightness": 90, **WHITE, "effect": "sparkle"},
        "light.b": {"state": "on", "brightness": 123, **GREEN},
        "light.fixed": {"state": "on", **GREEN},
        "light.cluster": {"state": "on", **RED},
    }
    assert entities == original


def test_animation_can_overlay_stored_snapshot_entities():
    snapshot = {"light.saved": {"state": "on", "rgb_color": [40, 50, 60], "brightness": 80}}
    targets = build_step_targets(snapshot, AnimationConfig(10, 10, [RED, GREEN]), 1)
    assert targets == {"light.saved": {"state": "on", "brightness": 80, **GREEN}}


@pytest.mark.parametrize("mode", ["color_temp", "brightness", "onoff", "unknown", None])
def test_snapshot_color_mode_keeps_white_lights_static(mode):
    snapshot = {
        "light.white": {
            "state": "on",
            "color_mode": mode,
            "hs_color": [30, 10],
            "color_temp_kelvin": 2700,
        },
        "light.color": {"state": "on", "color_mode": "xy", "hs_color": [120, 100]},
    }
    targets = build_step_targets(snapshot, AnimationConfig(10, 10, [RED]), 0)
    assert animated_light_ids(snapshot) == ["light.color"]
    assert targets["light.white"] == snapshot["light.white"]
    assert targets["light.color"] == {"state": "on", **RED}


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ({"xy_color": [0.3, 0.4]}, (0.3, 0.4)),
        ({"hs_color": [120, 100]}, color_util.color_hs_to_xy(120, 100)),
        ({"rgb_color": [20, 120, 200]}, color_util.color_RGB_to_xy(20, 120, 200)),
        (
            {"rgbw_color": [20, 120, 200, 40]},
            color_util.color_RGB_to_xy(*color_util.color_rgbw_to_rgb(20, 120, 200, 40)),
        ),
        (
            {"rgbww_color": [20, 120, 200, 40, 60]},
            color_util.color_RGB_to_xy(
                *color_util.color_rgbww_to_rgb(20, 120, 200, 40, 60, 2000, 6535)
            ),
        ),
        (
            {"color_temp_kelvin": 2700},
            color_util.color_RGB_to_xy(
                *(round(channel) for channel in color_util.color_temperature_to_rgb(2700))
            ),
        ),
        (
            {"color_temp": 370},
            color_util.color_RGB_to_xy(
                *(
                    round(channel)
                    for channel in color_util.color_temperature_to_rgb(
                        color_util.color_temperature_mired_to_kelvin(370)
                    )
                )
            ),
        ),
        ({"state": "on"}, None),
    ],
)
def test_target_color_conversion(target, expected):
    assert animation.target_color_xy(target) == expected


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ({"color_temp_kelvin": 2700}, True),
        ({"color_temp": 370}, True),
        ({"color_temp_kelvin": 2700, "hs_color": [0, 100]}, True),
        ({"hs_color": [120, 89.9]}, True),
        ({"hs_color": [120, 90]}, False),
        ({"hs_color": [120, 100]}, False),
        ({"xy_color": [0.3127, 0.3290]}, True),
        ({"xy_color": color_util.color_hs_to_xy(0, 100)}, False),
        ({"rgb_color": [255, 255, 255]}, True),
        ({"rgb_color": [255, 0, 0]}, False),
        ({"rgbw_color": [0, 0, 0, 255]}, True),
        ({"rgbw_color": [255, 0, 0, 0]}, False),
        ({"rgbww_color": [0, 0, 0, 255, 255]}, True),
        ({"rgbww_color": [255, 0, 0, 0, 0]}, False),
        ({"state": "on", "brightness": 100}, False),
        ({"color_mode": "hs"}, False),
    ],
)
def test_target_desaturation(target, expected):
    assert animation.target_is_desaturated(target) is expected


@pytest.mark.parametrize(
    ("point", "start", "end", "expected"),
    [
        ((0.3, 0.3), (0.1, 0.1), (0.5, 0.5), 0),
        ((0.3, 0.4), (0.1, 0.1), (0.5, 0.5), 0.1 / 2**0.5),
        ((0.0, 0.1), (0.1, 0.1), (0.5, 0.1), 0.1),
        ((0.6, 0.1), (0.1, 0.1), (0.5, 0.1), 0.1),
        ((0.4, 0.5), (0.1, 0.1), (0.1, 0.1), 0.5),
    ],
)
def test_point_to_segment_distance(point, start, end, expected):
    assert animation.point_segment_distance(point, start, end) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("point", "start", "end", "expected"),
    [
        ((0.3, 0.3), (0.1, 0.1), (0.5, 0.5), True),
        ((0.3, 0.32), (0.1, 0.1), (0.5, 0.5), True),
        ((0.17, 0.7), (0.701, 0.299), (0.172, 0.747), True),
        ((0.409, 0.518), (0.371, 0.35), (0.173, 0.746), True),
        ((0.14, 0.05), (0.701, 0.299), (0.172, 0.747), False),
        ((0.7, 0.3), (0.172, 0.747), (0.136, 0.04), False),
    ],
)
def test_point_on_raw_or_hue_animation_segment(point, start, end, expected):
    assert animation.point_on_animation_segment(point, start, end) is expected


@pytest.mark.parametrize(
    ("gamut", "start_hue", "end_hue"),
    [("HUE_GAMUT_A", 120, 150), ("HUE_GAMUT_B", 100, 160), ("HUE_GAMUT_C", 90, 110)],
)
def test_animation_accepts_fade_between_clamped_endpoints(gamut, start_hue, end_hue):
    triangle = getattr(animation, gamut)
    start = color_util.color_hs_to_xy(start_hue, 100)
    end = color_util.color_hs_to_xy(end_hue, 100)
    clamped_start = color_util.color_hs_to_xy(start_hue, 100, triangle)
    clamped_end = color_util.color_hs_to_xy(end_hue, 100, triangle)
    midpoint = tuple(
        (left + right) / 2 for left, right in zip(clamped_start, clamped_end, strict=True)
    )

    assert animation.point_segment_distance(midpoint, start, end) > 0.03
    assert animation.point_on_animation_segment(midpoint, start, end)


@pytest.mark.parametrize(
    ("start_target", "end_target", "point"),
    [
        ({"hs_color": [0, 100]}, {"hs_color": [120, 100]}, (0.17, 0.7)),
        ({"color_temp_kelvin": 5000}, {"hs_color": [128, 100]}, (0.409, 0.518)),
    ],
)
def test_hue_green_reports_match_converted_palette_segments(start_target, end_target, point):
    start = animation.target_color_xy(start_target)
    end = animation.target_color_xy(end_target)
    assert animation.point_segment_distance(point, start, end) > 0.03
    assert animation.point_on_animation_segment(point, start, end)


def test_gamut_clamping_keeps_an_endpoint_inside_the_triangle():
    start = (0.35, 0.3)
    end = color_util.color_hs_to_xy(120, 100)
    clamped_end = color_util.get_closest_point_to_point(end, animation.HUE_GAMUT_B)
    midpoint = tuple((left + right) / 2 for left, right in zip(start, clamped_end, strict=True))

    assert animation.point_segment_distance(midpoint, start, end) > 0.03
    assert animation.point_on_animation_segment(midpoint, start, end)


@pytest.mark.parametrize(
    ("brightness", "start", "end", "expected"),
    [
        (90, 100, 200, True),
        (210, 200, 100, True),
        (89, 100, 200, False),
        (211, 200, 100, False),
        (150, 100, 200, True),
        (None, 100, 200, True),
        (80, None, None, True),
        (89, None, 100, False),
    ],
)
def test_brightness_range(brightness, start, end, expected):
    assert animation.brightness_in_range(brightness, start, end) is expected


@pytest.mark.parametrize(
    ("brightness", "endpoints", "expected"),
    [
        (90, (200, 180, 100), True),
        (210, (100, 120, 200), True),
        (89, (200, 180, 100), False),
        (211, (100, 120, 200), False),
        (150, (100, None, 200), True),
        (None, (100, 120, 200), True),
        (80, (None, None, None), True),
    ],
)
def test_brightness_range_includes_previous_step(brightness, endpoints, expected):
    assert animation.brightness_in_range(brightness, *endpoints) is expected
