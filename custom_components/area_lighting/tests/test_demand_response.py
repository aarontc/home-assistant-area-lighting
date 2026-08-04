"""Pure-unit tests for the demand-response shedding policy."""

from __future__ import annotations

from custom_components.area_lighting.demand_response import (
    apply_demand_response,
    demand_response_shed_ids,
    keep_count,
)


def test_keep_count_boundaries():
    assert keep_count(0) == 0
    assert keep_count(1) == 1  # ceil(1 * 0.5)
    assert keep_count(2) == 1  # ceil(2 * 0.5)
    assert keep_count(5) == 3  # ceil(5 * 0.5)
    assert keep_count(6) == 2  # ceil(6 * 0.2)
    assert keep_count(10) == 2  # ceil(10 * 0.2)
    assert keep_count(25) == 5  # ceil(25 * 0.2)


def test_shed_ids_tail_config_order():
    ordered = ["l1", "l2", "l3", "l4", "l5", "l6"]
    assert demand_response_shed_ids(ordered, ordered) == ["l3", "l4", "l5", "l6"]


def test_shed_ids_uses_only_on_subset():
    ordered = ["l1", "l2", "l3", "l4", "l5"]
    on = ["l2", "l4"]  # n=2 -> 50% -> keep 1 -> shed tail of the on-set
    assert demand_response_shed_ids(ordered, on) == ["l4"]


def test_shed_ids_empty_on_set():
    assert demand_response_shed_ids(["l1", "l2"], []) == []


def test_shed_ids_sheds_on_ids_missing_from_order_first():
    """An on-bulb absent from the config list still counts toward n and is
    shed before any declared bulb.

    Scene `entities:` blocks can name lights that are not declared under an
    area's `lights:`. Exempting them let them burn through a whole window and
    also shrank the denominator, weakening the shed on the declared bulbs.
    Undeclared bulbs sort last, so they are the first to go.
    """
    assert demand_response_shed_ids(["l1", "l2"], ["l1", "l2", "lX"]) == ["lX"]


def test_shed_ids_strays_keep_on_set_order():
    ordered = ["l1", "l2"]
    on = ["l1", "l2", "lX", "lY"]  # n=4 -> keep ceil(4*0.5)=2 -> shed both strays
    assert demand_response_shed_ids(ordered, on) == ["lX", "lY"]


def test_shed_ids_deduplicates_ordered_list():
    """A light declared twice must not be counted twice.

    Double-counting inflated n past the 5/6 tier boundary and burned a kept
    slot on the duplicate.
    """
    ordered = ["l1", "l1", "l2", "l3", "l4", "l5"]
    on = ["l1", "l2", "l3", "l4", "l5"]
    # 5 distinct on-bulbs -> 50% tier -> keep 3
    assert demand_response_shed_ids(ordered, on) == ["l4", "l5"]


def test_shed_ids_deduplicates_on_set():
    assert demand_response_shed_ids(["l1", "l2"], ["l1", "l1", "l2"]) == ["l2"]


def test_apply_forces_tail_off():
    ordered = ["l1", "l2", "l3", "l4", "l5", "l6"]
    targets = {eid: {"state": "on", "brightness": 200} for eid in ordered}
    out = apply_demand_response(targets, ordered)
    assert out["l1"] == {"state": "on", "brightness": 200}
    assert out["l2"] == {"state": "on", "brightness": 200}
    for eid in ["l3", "l4", "l5", "l6"]:
        assert out[eid] == {"state": "off"}


def test_apply_does_not_mutate_input():
    ordered = ["l1", "l2"]
    targets = {"l1": {"state": "on"}, "l2": {"state": "on"}}
    original_l2 = targets["l2"]
    out = apply_demand_response(targets, ordered)
    assert targets["l2"] is original_l2
    assert targets["l2"] == {"state": "on"}
    assert out["l2"] == {"state": "off"}


def test_apply_counts_only_on_targets():
    ordered = ["l1", "l2", "l3"]
    targets = {"l1": {"state": "on"}, "l2": {"state": "off"}, "l3": {"state": "on"}}
    out = apply_demand_response(targets, ordered)  # on-set [l1, l3] -> shed [l3]
    assert out["l1"] == {"state": "on"}
    assert out["l3"] == {"state": "off"}
    assert out["l2"] == {"state": "off"}


def test_apply_no_shed_when_single_light():
    out = apply_demand_response({"l1": {"state": "on"}}, ["l1"])
    assert out["l1"] == {"state": "on"}  # keep_count(1) == 1


def test_apply_sheds_targets_missing_from_order():
    """An `on` target not declared under `lights:` is shed, not passed through.

    Previously it was returned untouched and the dispatcher issued
    light.turn_on for it, so it stayed lit for the entire window.
    """
    targets = {"l1": {"state": "on"}, "lX": {"state": "on"}}
    out = apply_demand_response(targets, ["l1"])  # n=2 -> keep 1 -> shed the stray
    assert out["l1"] == {"state": "on"}
    assert out["lX"] == {"state": "off"}


def test_apply_stray_counts_toward_tier():
    """The stray must inflate n, not be invisible to it."""
    ordered = ["l1", "l2", "l3", "l4", "l5"]
    targets = {eid: {"state": "on"} for eid in [*ordered, "lX"]}
    # 6 on-bulbs -> 80% tier -> keep 2; without counting the stray it would
    # have been the 5-bulb tier and kept 3.
    out = apply_demand_response(targets, ordered)
    kept = [eid for eid, st in out.items() if st.get("state") == "on"]
    assert kept == ["l1", "l2"]


def test_apply_deduplicates_ordered_list():
    ordered = ["l1", "l1", "l2", "l3", "l4", "l5"]
    targets = {eid: {"state": "on"} for eid in ["l1", "l2", "l3", "l4", "l5"]}
    out = apply_demand_response(targets, ordered)
    kept = [eid for eid, st in out.items() if st.get("state") == "on"]
    assert kept == ["l1", "l2", "l3"]  # 5 distinct -> 50% tier
