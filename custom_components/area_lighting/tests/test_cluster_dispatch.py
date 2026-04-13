"""Pure-unit tests for cluster_dispatch.select_dispatch_commands."""

from __future__ import annotations

from custom_components.area_lighting.cluster_dispatch import (
    select_dispatch_commands,
)


# ── Baseline: no clusters → one command per light ───────────────────────


def test_no_clusters_emits_one_command_per_light():
    entities = {
        "light.a": {"state": "on", "brightness": 128},
        "light.b": {"state": "on", "brightness": 128},
    }
    commands = select_dispatch_commands(entities, clusters=[])
    ids = [c[0] for c in commands]
    assert sorted(ids) == ["light.a", "light.b"]
    assert len(commands) == 2


def test_empty_entities_emits_no_commands():
    assert select_dispatch_commands({}, clusters=[]) == []


# ── Cluster exactly covers cohort → one command ────────────────────────


def test_cluster_exactly_covers_cohort():
    entities = {
        "light.a": {"state": "on", "brightness": 220, "color_temp_kelvin": 5000},
        "light.b": {"state": "on", "brightness": 220, "color_temp_kelvin": 5000},
        "light.c": {"state": "on", "brightness": 220, "color_temp_kelvin": 5000},
        "light.d": {"state": "on", "brightness": 220, "color_temp_kelvin": 5000},
    }
    clusters = [("light.all", ["light.a", "light.b", "light.c", "light.d"])]
    commands = select_dispatch_commands(entities, clusters)
    assert len(commands) == 1
    assert commands[0][0] == "light.all"
    assert commands[0][1] == {
        "state": "on", "brightness": 220, "color_temp_kelvin": 5000,
    }


# ── Two cohorts with one cluster each (classic vanity-by-role case) ─────


def test_two_cohorts_use_two_clusters():
    entities = {
        # Color lights ON with red
        "light.left_color": {"state": "on", "brightness": 64, "hs_color": [0, 100]},
        "light.right_color": {"state": "on", "brightness": 64, "hs_color": [0, 100]},
        # White lights OFF
        "light.left_white": {"state": "off"},
        "light.right_white": {"state": "off"},
    }
    clusters = [
        ("light.all", ["light.left_color", "light.right_color",
                       "light.left_white", "light.right_white"]),
        ("light.color", ["light.left_color", "light.right_color"]),
        ("light.white", ["light.left_white", "light.right_white"]),
    ]
    commands = select_dispatch_commands(entities, clusters)
    # Must be exactly 2 commands (one per cluster). NOT 4 individual
    # commands, and NOT the "all" cluster (states differ).
    assert len(commands) == 2
    targets = {c[0] for c in commands}
    assert targets == {"light.color", "light.white"}


# ── Cluster is too big to cover cohort — must NOT be used ──────────────


def test_cluster_with_outside_members_is_not_used():
    """Cluster 'all' has members outside the cohort — using it would
    incorrectly affect the outside members."""
    entities = {
        "light.a": {"state": "on", "brightness": 100},
        "light.b": {"state": "on", "brightness": 100},
        "light.c": {"state": "off"},
    }
    clusters = [
        ("light.all", ["light.a", "light.b", "light.c"]),
    ]
    commands = select_dispatch_commands(entities, clusters)
    # The 'all' cluster can't be used — its members span two cohorts.
    # Result: 3 individual commands (2 for the on cohort, 1 for off).
    assert len(commands) == 3
    ids = sorted(c[0] for c in commands)
    assert ids == ["light.a", "light.b", "light.c"]


# ── Cluster partially covers cohort — cluster + individual ─────────────


def test_cluster_partial_cover_plus_individuals():
    """Cohort has 3 members; a 2-member cluster covers 2, 1 left over."""
    entities = {
        "light.a": {"state": "on", "brightness": 128},
        "light.b": {"state": "on", "brightness": 128},
        "light.c": {"state": "on", "brightness": 128},
    }
    clusters = [
        ("light.pair", ["light.a", "light.b"]),
    ]
    commands = select_dispatch_commands(entities, clusters)
    assert len(commands) == 2  # one cluster + one individual
    ids = sorted(c[0] for c in commands)
    assert ids == ["light.c", "light.pair"]


# ── Largest cluster is preferred when multiple fit ─────────────────────


def test_largest_cluster_preferred():
    """Both 'all 4' and 'color pair' fit the cohort. Use 'all 4'."""
    entities = {
        "light.a": {"state": "on", "brightness": 200},
        "light.b": {"state": "on", "brightness": 200},
        "light.c": {"state": "on", "brightness": 200},
        "light.d": {"state": "on", "brightness": 200},
    }
    clusters = [
        ("light.pair", ["light.a", "light.b"]),
        ("light.all", ["light.a", "light.b", "light.c", "light.d"]),
    ]
    commands = select_dispatch_commands(entities, clusters)
    assert len(commands) == 1
    assert commands[0][0] == "light.all"


# ── Cluster ordering: algorithm consumes the biggest first ─────────────


def test_large_cluster_then_smaller_cluster_combined():
    """Cohort has 5 lights. A 4-member cluster and a 2-member cluster
    are both present; algorithm picks the 4-member first, then has
    1 leftover that the 2-member can't fit."""
    entities = {
        f"light.{x}": {"state": "on", "brightness": 50}
        for x in ("a", "b", "c", "d", "e")
    }
    clusters = [
        ("light.pair", ["light.a", "light.b"]),
        ("light.quad", ["light.a", "light.b", "light.c", "light.d"]),
    ]
    commands = select_dispatch_commands(entities, clusters)
    # Largest first: quad consumes a/b/c/d. Remaining: {e}. pair can't
    # fit (b is already taken). e goes individual. → 2 commands.
    ids = sorted(c[0] for c in commands)
    assert len(commands) == 2
    assert ids == ["light.e", "light.quad"]


# ── Off state routes through cluster correctly ─────────────────────────


def test_off_state_uses_cluster():
    entities = {
        "light.a": {"state": "off"},
        "light.b": {"state": "off"},
    }
    clusters = [("light.pair", ["light.a", "light.b"])]
    commands = select_dispatch_commands(entities, clusters)
    assert len(commands) == 1
    assert commands[0] == ("light.pair", {"state": "off"})


# ── List attributes (hs_color) hash correctly ──────────────────────────


def test_list_attributes_partition_correctly():
    """Two lights with the same hs_color should cohort together even
    though the hs_color value is a list."""
    entities = {
        "light.a": {"state": "on", "brightness": 100, "hs_color": [0, 100]},
        "light.b": {"state": "on", "brightness": 100, "hs_color": [0, 100]},
        "light.c": {"state": "on", "brightness": 100, "hs_color": [120, 100]},
    }
    clusters = [
        ("light.ab", ["light.a", "light.b"]),
    ]
    commands = select_dispatch_commands(entities, clusters)
    # a+b cohort → light.ab; c is its own cohort → individual
    assert len(commands) == 2
    # Verify the hs_color list survived the round trip
    ab_cmd = next(c for c in commands if c[0] == "light.ab")
    assert ab_cmd[1]["hs_color"] == [0, 100]
    c_cmd = next(c for c in commands if c[0] == "light.c")
    assert c_cmd[1]["hs_color"] == [120, 100]


# ── Upstairs bathroom real-world case ──────────────────────────────────

# Shared cluster spec matching area_lighting.yaml
_UB_CLUSTERS = [
    ("light.hz_upstairs_bath_vanity_all", [
        "light.upstairs_bathroom_vanity_left",
        "light.upstairs_bathroom_vanity_left_w",
        "light.upstairs_bathroom_vanity_right",
        "light.upstairs_bathroom_vanity_right_w",
    ]),
    ("light.hz_upstairs_bath_vanity_color", [
        "light.upstairs_bathroom_vanity_left",
        "light.upstairs_bathroom_vanity_right",
    ]),
    ("light.hz_upstairs_bath_vanity_white", [
        "light.upstairs_bathroom_vanity_left_w",
        "light.upstairs_bathroom_vanity_right_w",
    ]),
]


def test_upstairs_bathroom_daylight_all_four_same_state():
    """Daylight scene: 4 vanity lights all at brightness 220 /
    color_temp_kelvin 5000. With the 'all' cluster, should be 1 command."""
    entities = {
        "light.upstairs_bathroom_vanity_left": {
            "state": "on", "brightness": 220, "color_temp_kelvin": 5000,
        },
        "light.upstairs_bathroom_vanity_left_w": {
            "state": "on", "brightness": 220, "color_temp_kelvin": 5000,
        },
        "light.upstairs_bathroom_vanity_right": {
            "state": "on", "brightness": 220, "color_temp_kelvin": 5000,
        },
        "light.upstairs_bathroom_vanity_right_w": {
            "state": "on", "brightness": 220, "color_temp_kelvin": 5000,
        },
    }
    commands = select_dispatch_commands(entities, _UB_CLUSTERS)
    assert len(commands) == 1
    assert commands[0][0] == "light.hz_upstairs_bath_vanity_all"


def test_upstairs_bathroom_night_color_on_white_off():
    """Night scene: color lights on red, white lights off.
    → color cluster turn_on + white cluster turn_off = 2 commands total."""
    entities = {
        "light.upstairs_bathroom_vanity_left": {
            "state": "on", "brightness": 64, "hs_color": [0, 100],
        },
        "light.upstairs_bathroom_vanity_left_w": {"state": "off"},
        "light.upstairs_bathroom_vanity_right": {
            "state": "on", "brightness": 64, "hs_color": [0, 100],
        },
        "light.upstairs_bathroom_vanity_right_w": {"state": "off"},
    }
    clusters = _UB_CLUSTERS
    commands = select_dispatch_commands(entities, clusters)
    assert len(commands) == 2
    targets = {c[0] for c in commands}
    assert targets == {
        "light.hz_upstairs_bath_vanity_color",
        "light.hz_upstairs_bath_vanity_white",
    }
    # Verify the command types
    on_cmd = next(c for c in commands if c[0] == "light.hz_upstairs_bath_vanity_color")
    assert on_cmd[1]["state"] == "on"
    assert on_cmd[1]["brightness"] == 64
    off_cmd = next(c for c in commands if c[0] == "light.hz_upstairs_bath_vanity_white")
    assert off_cmd[1]["state"] == "off"


# ── Upstairs bathroom: explicit per-scene command count proofs ─────────


def test_upstairs_bathroom_daylight_1_turn_on():
    """Daylight: all 4 lights → same on-state → 1 turn_on to the 'all' cluster."""
    entities = {
        "light.upstairs_bathroom_vanity_left":   {"state": "on", "brightness": 220, "color_temp_kelvin": 5000},
        "light.upstairs_bathroom_vanity_left_w":  {"state": "on", "brightness": 220, "color_temp_kelvin": 5000},
        "light.upstairs_bathroom_vanity_right":   {"state": "on", "brightness": 220, "color_temp_kelvin": 5000},
        "light.upstairs_bathroom_vanity_right_w": {"state": "on", "brightness": 220, "color_temp_kelvin": 5000},
    }
    commands = select_dispatch_commands(entities, _UB_CLUSTERS)
    assert len(commands) == 1
    assert commands[0][0] == "light.hz_upstairs_bath_vanity_all"
    assert commands[0][1]["state"] == "on"
    assert commands[0][1]["brightness"] == 220
    assert commands[0][1]["color_temp_kelvin"] == 5000


def test_upstairs_bathroom_off_1_turn_off():
    """Off: all 4 lights → same off-state → 1 turn_off to the 'all' cluster."""
    entities = {
        "light.upstairs_bathroom_vanity_left":   {"state": "off"},
        "light.upstairs_bathroom_vanity_left_w":  {"state": "off"},
        "light.upstairs_bathroom_vanity_right":   {"state": "off"},
        "light.upstairs_bathroom_vanity_right_w": {"state": "off"},
    }
    commands = select_dispatch_commands(entities, _UB_CLUSTERS)
    assert len(commands) == 1
    assert commands[0][0] == "light.hz_upstairs_bath_vanity_all"
    assert commands[0][1] == {"state": "off"}


def test_upstairs_bathroom_night_2_commands():
    """Night: 2 color on (red) + 2 white off → color cluster turn_on +
    white cluster turn_off = exactly 2 commands."""
    entities = {
        "light.upstairs_bathroom_vanity_left":   {"state": "on", "brightness": 64, "hs_color": [0, 100]},
        "light.upstairs_bathroom_vanity_left_w":  {"state": "off"},
        "light.upstairs_bathroom_vanity_right":   {"state": "on", "brightness": 64, "hs_color": [0, 100]},
        "light.upstairs_bathroom_vanity_right_w": {"state": "off"},
    }
    commands = select_dispatch_commands(entities, _UB_CLUSTERS)
    assert len(commands) == 2

    on_cmd = next(c for c in commands if c[1].get("state") == "on")
    off_cmd = next(c for c in commands if c[1].get("state") == "off")

    assert on_cmd[0] == "light.hz_upstairs_bath_vanity_color"
    assert on_cmd[1] == {"state": "on", "brightness": 64, "hs_color": [0, 100]}

    assert off_cmd[0] == "light.hz_upstairs_bath_vanity_white"
    assert off_cmd[1] == {"state": "off"}
