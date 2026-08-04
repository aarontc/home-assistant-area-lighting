"""Pure demand-response shedding policy (HA-free, unit-testable).

While the global demand-response flag is active, each lighting activation
sheds a fraction of the bulbs it would turn ON:

  - n <= 5 on-bulbs  -> shed 50%
  - n >= 6 on-bulbs  -> shed 80%
  - keep = ceil(n * (1 - ratio)); at least one bulb survives when n >= 1.

Bulbs are shed from the TAIL of the on-set (first-declared bulbs survive).
The on-set is ordered by the area's config order; any on-bulb NOT declared in
that list sorts after every declared one, so undeclared bulbs are shed first.

Callers must exclude Hue-Zone cluster entities before calling in, since a
cluster addresses the same physical bulbs as its members and would otherwise
be counted twice.

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


def _dedupe(ids: list[str]) -> list[str]:
    """Order-preserving de-duplication."""
    seen: set[str] = set()
    out: list[str] = []
    for eid in ids:
        if eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out


def _shed_order(ordered_light_ids: list[str], on_ids: list[str]) -> list[str]:
    """The on-set in shed precedence order: declared bulbs in config order,
    then undeclared ones in on-set order.

    Undeclared bulbs sort last, so they are the first shed. A scene's
    `entities:` block may name a light that is not declared under the area's
    `lights:`; such a bulb is still physically turned on, so it must count
    toward n and be sheddable. Exempting it previously let it burn through an
    entire window AND shrank the denominator, weakening the shed applied to
    the declared bulbs.

    Both inputs are de-duplicated: a light declared twice must not be counted
    twice, which could push n past the 5/6 tier boundary and burn a kept slot
    on the duplicate.
    """
    ordered = _dedupe(ordered_light_ids)
    declared = set(ordered)
    on = _dedupe(on_ids)
    on_set = set(on)
    return [eid for eid in ordered if eid in on_set] + [eid for eid in on if eid not in declared]


def demand_response_shed_ids(
    ordered_light_ids: list[str],
    on_ids: list[str],
) -> list[str]:
    """Return the entity_ids to shed: the tail of the ordered on-set.

    ordered_light_ids: the area's individual lights in config order.
    on_ids: the entity_ids the activation would turn ON.
    """
    ordered_on = _shed_order(ordered_light_ids, on_ids)
    return ordered_on[keep_count(len(ordered_on)) :]


def apply_demand_response(
    targets: dict[str, dict],
    ordered_light_ids: list[str],
) -> dict[str, dict]:
    """Return `targets` with the tail of the ON-set forced off.

    Every entity targeted `on` is eligible to shed, whether or not it appears
    in `ordered_light_ids`; undeclared ones are shed first. Shed entries are
    replaced with a fresh {"state": "off"} dict, so the caller's original
    per-light state dicts are never mutated. A shallow copy is returned when
    anything is shed; the original mapping is returned unchanged when nothing
    is shed (callers must not mutate it).
    """
    on_ids = [
        eid
        for eid, state in targets.items()
        if isinstance(state, dict) and state.get("state") == "on"
    ]
    shed = demand_response_shed_ids(ordered_light_ids, on_ids)
    if not shed:
        return targets
    out = dict(targets)
    for eid in shed:
        out[eid] = {"state": "off"}
    return out
