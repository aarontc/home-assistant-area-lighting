"""Pure unit tests for leader/follower config schema and parsing."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.area_lighting.config_schema import (
    AREA_SCHEMA,
    parse_config,
    validate_leader_follower_graph,
)
from custom_components.area_lighting.models import AreaConfig


def _minimal_area(area_id: str, **extra) -> dict:
    """Return the minimal raw area dict that passes AREA_SCHEMA."""
    return {"id": area_id, "name": area_id.title(), **extra}


def test_area_config_defaults_have_no_leader():
    cfg = AreaConfig(id="x", name="X")
    assert cfg.leader_area_id is None
    assert cfg.follow_leader_deactivation is False


def test_area_schema_accepts_leader_area_id():
    AREA_SCHEMA(_minimal_area("closet", leader_area_id="bath"))


def test_area_schema_accepts_follow_leader_deactivation():
    AREA_SCHEMA(
        _minimal_area(
            "closet",
            leader_area_id="bath",
            follow_leader_deactivation=True,
        )
    )


def test_parse_config_carries_leader_fields():
    raw = {
        "areas": [
            _minimal_area("bath"),
            _minimal_area(
                "closet",
                leader_area_id="bath",
                follow_leader_deactivation=True,
            ),
        ]
    }
    cfg = parse_config(raw)
    closet = cfg.area_by_id("closet")
    assert closet.leader_area_id == "bath"
    assert closet.follow_leader_deactivation is True
    bath = cfg.area_by_id("bath")
    assert bath.leader_area_id is None
    assert bath.follow_leader_deactivation is False


def test_validate_graph_accepts_valid():
    cfg = parse_config(
        {
            "areas": [
                _minimal_area("bath"),
                _minimal_area("closet", leader_area_id="bath"),
            ],
        }
    )
    validate_leader_follower_graph(cfg)  # should not raise


def test_validate_graph_rejects_self_leader():
    cfg = parse_config(
        {
            "areas": [
                _minimal_area("closet", leader_area_id="closet"),
            ],
        }
    )
    with pytest.raises(vol.Invalid, match="cannot be its own leader"):
        validate_leader_follower_graph(cfg)


def test_validate_graph_rejects_dangling_leader():
    cfg = parse_config(
        {
            "areas": [
                _minimal_area("closet", leader_area_id="nowhere"),
            ],
        }
    )
    with pytest.raises(vol.Invalid, match="nonexistent area 'nowhere'"):
        validate_leader_follower_graph(cfg)


def test_validate_graph_rejects_chain():
    cfg = parse_config(
        {
            "areas": [
                _minimal_area("bath", leader_area_id="kitchen"),
                _minimal_area("kitchen"),
                _minimal_area("closet", leader_area_id="bath"),
            ],
        }
    )
    with pytest.raises(vol.Invalid, match="chained"):
        validate_leader_follower_graph(cfg)


def test_validate_graph_allows_multiple_followers_per_leader():
    cfg = parse_config(
        {
            "areas": [
                _minimal_area("bath"),
                _minimal_area("closet", leader_area_id="bath"),
                _minimal_area("vanity", leader_area_id="bath"),
            ],
        }
    )
    validate_leader_follower_graph(cfg)  # should not raise
