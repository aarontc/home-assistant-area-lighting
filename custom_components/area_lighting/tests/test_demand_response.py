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


def test_shed_ids_ignores_ids_not_in_order():
    assert demand_response_shed_ids(["l1", "l2"], ["l1", "l2", "lX"]) == ["l2"]


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
