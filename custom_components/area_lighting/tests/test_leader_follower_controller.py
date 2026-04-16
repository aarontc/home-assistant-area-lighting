"""Unit tests for leader/follower controller logic.

These are pure tests that exercise controller methods without a running HA
instance. They construct synthetic AreaConfig objects and either real or
mock AreaLightingController instances. For anything that needs a full
wire-up (service calls, state subscribers), see
tests/integration/test_leader_follower.py instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.area_lighting.area_state import (
    ActivationSource,
    LeaderReason,
)
from custom_components.area_lighting.controller import AreaLightingController
from custom_components.area_lighting.models import AreaConfig, AreaLightingConfig, SceneConfig


def test_activation_source_leader_exists():
    assert ActivationSource.LEADER.value == "leader"


def test_leader_reason_values():
    assert LeaderReason.SCENE_ACTIVATED.value == "scene_activated"
    assert LeaderReason.OFF.value == "off"
    assert LeaderReason.AMBIENT.value == "ambient"
    assert LeaderReason.MANUAL.value == "manual"


def _make_controller(
    area_id: str = "bath",
    scenes: tuple[str, ...] = ("ambient", "circadian", "evening", "off"),
) -> AreaLightingController:
    """Construct a real AreaLightingController with a mock HA instance.

    Good enough for logic that doesn't touch services, timers, or state
    subscribers — which is exactly the leader/follower helper surface.
    """
    area = AreaConfig(
        id=area_id,
        name=area_id.title(),
        scenes=[SceneConfig(slug=s, name=s.title(), area_id=area_id) for s in scenes],
    )
    hass = MagicMock()
    hass.data = {}
    return AreaLightingController(hass, area, AreaLightingConfig(areas=[area]))


def test_current_on_scene_slug_off_returns_none():
    c = _make_controller()
    c._state.transition_to_off(ActivationSource.USER)
    assert c.current_on_scene_slug() is None


def test_current_on_scene_slug_ambient_returns_none():
    c = _make_controller()
    c._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    assert c.current_on_scene_slug() is None


def test_current_on_scene_slug_christmas_returns_none():
    c = _make_controller(scenes=("ambient", "christmas", "evening", "off"))
    c._state.transition_to_scene("christmas", ActivationSource.HOLIDAY)
    assert c.current_on_scene_slug() is None


def test_current_on_scene_slug_manual_returns_none():
    c = _make_controller()
    c._state.transition_to_manual()
    assert c.current_on_scene_slug() is None


def test_current_on_scene_slug_evening_returns_evening():
    c = _make_controller()
    c._state.transition_to_scene("evening", ActivationSource.USER)
    assert c.current_on_scene_slug() == "evening"


def test_current_on_scene_slug_circadian_returns_circadian():
    c = _make_controller()
    c._state.transition_to_circadian(ActivationSource.USER)
    assert c.current_on_scene_slug() == "circadian"


def test_resolve_leader_on_slug_no_leader_returns_none():
    c = _make_controller()
    assert c._resolve_leader_on_slug() is None


def test_resolve_leader_on_slug_leader_off_returns_none():
    follower = _make_controller(area_id="closet")
    leader = _make_controller(area_id="bath")
    follower.leader = leader
    leader._state.transition_to_off(ActivationSource.USER)
    assert follower._resolve_leader_on_slug() is None


def test_resolve_leader_on_slug_leader_manual_returns_none():
    follower = _make_controller(area_id="closet")
    leader = _make_controller(area_id="bath")
    follower.leader = leader
    leader._state.transition_to_manual()
    assert follower._resolve_leader_on_slug() is None


def test_resolve_leader_on_slug_leader_ambient_returns_none():
    follower = _make_controller(area_id="closet")
    leader = _make_controller(area_id="bath")
    follower.leader = leader
    leader._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    assert follower._resolve_leader_on_slug() is None


def test_resolve_leader_on_slug_leader_evening_follower_has_evening():
    follower = _make_controller(
        area_id="closet",
        scenes=("evening", "circadian", "off"),
    )
    leader = _make_controller(area_id="bath", scenes=("evening", "circadian", "off"))
    follower.leader = leader
    leader._state.transition_to_scene("evening", ActivationSource.USER)
    assert follower._resolve_leader_on_slug() == "evening"


def test_resolve_leader_on_slug_follower_missing_slug_returns_none():
    """Follower falls back to its own default (no hint) when it has no christmas scene."""
    follower = _make_controller(
        area_id="closet",
        scenes=("evening", "circadian", "off"),
    )
    leader = _make_controller(
        area_id="bath",
        scenes=("christmas", "evening", "off"),
    )
    follower.leader = leader
    leader._state.transition_to_scene("christmas", ActivationSource.HOLIDAY)
    assert follower._resolve_leader_on_slug() is None


class _RecordingLeader:
    """Stand-in leader with just enough surface for follower tests.

    The follower's `handle_leader_change` doesn't reach into the leader —
    it only needs `self.leader.area.id` for logging. So we build a thin
    recording leader that exposes an `area.id` and nothing else.
    """

    def __init__(self, area_id: str = "bath"):
        self.area = MagicMock()
        self.area.id = area_id


@pytest.fixture
def follower_ctrl(monkeypatch):
    """Real controller with _activate_scene stubbed as an async spy."""
    c = _make_controller(area_id="closet")
    c.leader = _RecordingLeader("bath")

    calls: list = []

    async def fake_activate_scene(scene_slug, source=None, transition=None):
        calls.append(("activate_scene", scene_slug, source))

    monkeypatch.setattr(c, "_activate_scene", fake_activate_scene)
    c._calls = calls
    return c


async def test_handle_leader_scene_activated_follower_on_mirrors(follower_ctrl):
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    await follower_ctrl.handle_leader_change("evening", LeaderReason.SCENE_ACTIVATED)
    assert ("activate_scene", "evening", ActivationSource.LEADER) in follower_ctrl._calls


async def test_handle_leader_scene_activated_follower_off_noop(follower_ctrl):
    follower_ctrl._state.transition_to_off(ActivationSource.USER)
    await follower_ctrl.handle_leader_change("evening", LeaderReason.SCENE_ACTIVATED)
    assert follower_ctrl._calls == []


async def test_handle_leader_scene_activated_follower_ambient_noop(follower_ctrl):
    follower_ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    await follower_ctrl.handle_leader_change("evening", LeaderReason.SCENE_ACTIVATED)
    assert follower_ctrl._calls == []


async def test_handle_leader_scene_activated_follower_manual_noop(follower_ctrl):
    follower_ctrl._state.transition_to_manual()
    await follower_ctrl.handle_leader_change("evening", LeaderReason.SCENE_ACTIVATED)
    assert follower_ctrl._calls == []


async def test_handle_leader_scene_not_on_follower_logs_warning(follower_ctrl, caplog):
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    import logging

    caplog.set_level(logging.WARNING)
    await follower_ctrl.handle_leader_change("christmas", LeaderReason.SCENE_ACTIVATED)
    assert follower_ctrl._calls == []
    assert any("leader bath activated scene christmas" in rec.message for rec in caplog.records)


async def test_handle_leader_off_default_is_noop(follower_ctrl):
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    # follow_leader_deactivation defaults to False
    await follower_ctrl.handle_leader_change(None, LeaderReason.OFF)
    assert follower_ctrl._calls == []


async def test_handle_leader_off_with_follow_deactivation_turns_off(follower_ctrl):
    from custom_components.area_lighting.const import SCENE_OFF_INTERNAL

    follower_ctrl.area.follow_leader_deactivation = True
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    await follower_ctrl.handle_leader_change(None, LeaderReason.OFF)
    assert ("activate_scene", SCENE_OFF_INTERNAL, ActivationSource.LEADER) in follower_ctrl._calls


async def test_handle_leader_ambient_default_is_noop(follower_ctrl):
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    await follower_ctrl.handle_leader_change(None, LeaderReason.AMBIENT)
    assert follower_ctrl._calls == []


async def test_handle_leader_ambient_with_follow_deactivation_activates_ambient(follower_ctrl):
    follower_ctrl.area.follow_leader_deactivation = True
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    await follower_ctrl.handle_leader_change(None, LeaderReason.AMBIENT)
    assert ("activate_scene", "ambient", ActivationSource.LEADER) in follower_ctrl._calls


async def test_handle_leader_manual_always_noop(follower_ctrl):
    follower_ctrl.area.follow_leader_deactivation = True
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    await follower_ctrl.handle_leader_change(None, LeaderReason.MANUAL)
    assert follower_ctrl._calls == []
