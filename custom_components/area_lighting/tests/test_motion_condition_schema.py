"""Unit tests for MOTION_LIGHT_CONDITION_SCHEMA validation."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting.config_schema import MOTION_LIGHT_CONDITION_SCHEMA

# ── Existing single-entity form (regression) ────────────────────────────


def test_single_entity_with_state_passes():
    MOTION_LIGHT_CONDITION_SCHEMA(
        {
            "entity_id": "lock.front_door_deadbolt",
            "state": "unlocked",
        }
    )


def test_single_entity_with_attribute_and_below_passes():
    MOTION_LIGHT_CONDITION_SCHEMA(
        {
            "entity_id": "sun.sun",
            "attribute": "elevation",
            "below": 0,
        }
    )


# ── New entity_ids form ─────────────────────────────────────────────────


def test_entity_ids_with_aggregate_and_below_passes():
    MOTION_LIGHT_CONDITION_SCHEMA(
        {
            "entity_ids": [
                "sensor.back_patio_illuminance",
                "sensor.shed_illuminance",
            ],
            "aggregate": "average",
            "below": 100,
        }
    )


def test_entity_ids_accepts_min_aggregate():
    MOTION_LIGHT_CONDITION_SCHEMA(
        {
            "entity_ids": ["sensor.a", "sensor.b"],
            "aggregate": "min",
            "below": 100,
        }
    )


def test_entity_ids_accepts_max_aggregate():
    MOTION_LIGHT_CONDITION_SCHEMA(
        {
            "entity_ids": ["sensor.a", "sensor.b"],
            "aggregate": "max",
            "below": 100,
        }
    )


# ── Validation errors ───────────────────────────────────────────────────


def test_both_entity_id_and_entity_ids_rejected():
    with pytest.raises(vol.Invalid):
        MOTION_LIGHT_CONDITION_SCHEMA(
            {
                "entity_id": "sensor.a",
                "entity_ids": ["sensor.b"],
                "aggregate": "average",
                "below": 100,
            }
        )


def test_neither_entity_id_nor_entity_ids_rejected():
    with pytest.raises(vol.Invalid):
        MOTION_LIGHT_CONDITION_SCHEMA(
            {
                "below": 100,
            }
        )


def test_entity_ids_without_aggregate_rejected():
    with pytest.raises(vol.Invalid):
        MOTION_LIGHT_CONDITION_SCHEMA(
            {
                "entity_ids": ["sensor.a", "sensor.b"],
                "below": 100,
            }
        )


def test_entity_ids_with_state_rejected():
    with pytest.raises(vol.Invalid):
        MOTION_LIGHT_CONDITION_SCHEMA(
            {
                "entity_ids": ["sensor.a", "sensor.b"],
                "aggregate": "average",
                "state": "unlocked",
            }
        )


def test_invalid_aggregate_value_rejected():
    with pytest.raises(vol.Invalid):
        MOTION_LIGHT_CONDITION_SCHEMA(
            {
                "entity_ids": ["sensor.a", "sensor.b"],
                "aggregate": "sum",
                "below": 100,
            }
        )
