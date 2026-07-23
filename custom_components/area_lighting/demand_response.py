"""Pure demand-response shedding policy (HA-free, unit-testable).

While the global demand-response flag is active, each lighting activation
sheds a fraction of the bulbs it would turn ON:

  - n <= 5 on-bulbs  -> shed 50%
  - n >= 6 on-bulbs  -> shed 80%
  - keep = ceil(n * (1 - ratio)); at least one bulb survives when n >= 1.

Bulbs are shed from the config-order TAIL of the on-set (first-declared
bulbs survive). The shed universe is an area's individual lights, in config
order; Hue-Zone clusters are excluded (they address the same physical bulbs).

Imports nothing from homeassistant.* so it can be unit-tested against many
input shapes quickly (mirrors cluster_dispatch.py).
"""

from __future__ import annotations

from math import ceil


def keep_count(n: int) -> int:
    """Number of on-bulbs to keep for an on-set of size n."""
    if n <= 0:
        return 0
    ratio = 0.50 if n <= 5 else 0.80
    return ceil(n * (1 - ratio))


def demand_response_shed_ids(
    ordered_light_ids: list[str],
    on_ids: list[str],
) -> list[str]:
    """Return the entity_ids to shed: the config-order tail of the on-set.

    ordered_light_ids: the area's individual lights in config order.
    on_ids: the entity_ids the activation would turn ON.
    """
    on_set = set(on_ids)
    ordered_on = [eid for eid in ordered_light_ids if eid in on_set]
    return ordered_on[keep_count(len(ordered_on)) :]


def apply_demand_response(
    targets: dict[str, dict],
    ordered_light_ids: list[str],
) -> dict[str, dict]:
    """Return a copy of `targets` with the shed tail forced to off.

    Only entities present in `ordered_light_ids` are eligible to shed. Shed
    entries are replaced with a fresh {"state": "off"} dict, so the caller's
    original per-light state dicts are never mutated.
    """
    ordered_on = [eid for eid in ordered_light_ids if targets.get(eid, {}).get("state") == "on"]
    shed = ordered_on[keep_count(len(ordered_on)) :]
    if not shed:
        return targets
    out = dict(targets)
    for eid in shed:
        out[eid] = {"state": "off"}
    return out
