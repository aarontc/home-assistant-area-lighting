"""Pure-function cluster selection for scene dispatch.

Given a scene's target light states and a list of available clusters,
partition the commands so that when every member of a cluster shares
an identical target state, a single command to the cluster replaces
N per-light commands.

Example: four bathroom vanity lights all going to the same daylight
state, and an "all vanity" Hue Zone containing those four → one
`light.turn_on` to the zone instead of four.

This module is HA-free (no imports from homeassistant.*) so it can
be unit-tested against a wide variety of input shapes quickly.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _hashable(value: Any) -> Any:
    """Recursively convert a value so it can be used as a dict key."""
    if isinstance(value, list):
        return tuple(_hashable(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _hashable(v)) for k, v in value.items()))
    return value


def _state_key(state: dict[str, Any]) -> tuple:
    """Build a hashable cohort key from a light state dict."""
    return tuple(sorted((k, _hashable(v)) for k, v in state.items()))


def select_dispatch_commands(
    entities: dict[str, dict[str, Any]],
    clusters: list[tuple[str, list[str]]],
) -> list[tuple[str, dict[str, Any]]]:
    """Pick the minimal command set to apply a scene.

    Args:
        entities: Map of {entity_id -> state_dict} for lights the scene
            wants to set. Each state_dict has at minimum a "state" key
            ('on' or 'off') plus optional attributes (brightness,
            color_temp_kelvin, hs_color, ...).
        clusters: List of (cluster_entity_id, [member_entity_ids])
            tuples. Clusters whose members set is not a subset of the
            entities keyset are still considered — only members that
            appear in the scene's cohort are relevant.

    Returns:
        An ordered list of (entity_id, state_dict) commands. Cluster
        commands come before per-light commands where possible, but
        order is otherwise unspecified.

    Algorithm:
        1. Partition the entities into cohorts by target state
           (entities with byte-identical state form one cohort).
        2. For each cohort, greedily select the largest cluster whose
           members are all in the cohort. Consume those members; repeat
           until no cluster fits. Any leftover members emit individual
           commands.

    Greedy is near-optimal for the typical pattern (a few clusters
    that form clean partitions, e.g. "all", "color", "white"). Exact
    minimum-cover is NP-hard but isn't needed here.
    """
    if not entities:
        return []

    # Pre-normalize cluster members to sets for fast subset checks.
    # Skip clusters with no members (they're individual lights, not clusters).
    normalized_clusters: list[tuple[str, set[str]]] = [
        (cid, set(members)) for cid, members in clusters if members
    ]

    # Group entities by their target state.
    cohorts: dict[tuple, set[str]] = defaultdict(set)
    for entity_id, state in entities.items():
        cohorts[_state_key(state)].add(entity_id)

    commands: list[tuple[str, dict[str, Any]]] = []

    for state_key, cohort_members in cohorts.items():
        remaining = set(cohort_members)
        # Rehydrate the state dict from the key
        state_dict: dict[str, Any] = {}
        for k, v in state_key:
            if isinstance(v, tuple):
                # Assume it came from a list
                state_dict[k] = list(v)
            else:
                state_dict[k] = v

        # Sort candidate clusters by descending member count so we
        # consume the largest batch available first.
        candidates = sorted(normalized_clusters, key=lambda c: -len(c[1]))

        for cluster_id, cluster_members in candidates:
            if cluster_members <= remaining:
                # Entire cluster can be batched under this cohort's state.
                commands.append((cluster_id, state_dict))
                remaining -= cluster_members

        # Whatever's left gets individual commands. Sort for deterministic
        # ordering so tests are reproducible.
        commands.extend((entity_id, state_dict) for entity_id in sorted(remaining))

    return commands
