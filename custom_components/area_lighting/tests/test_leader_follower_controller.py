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


SPLIT_SCENES = ("ambient", "christmas", "christmas_ambient", "evening", "off")


@pytest.mark.parametrize(
    ("scenes", "slug", "expected"),
    [
        (("ambient", "christmas", "evening", "off"), "ambient", True),
        (("ambient", "christmas", "evening", "off"), "christmas", True),
        (("ambient", "christmas", "evening", "off"), "evening", False),
        (SPLIT_SCENES, "ambient", True),
        (SPLIT_SCENES, "christmas_ambient", True),
        (SPLIT_SCENES, "christmas", False),
    ],
)
def test_is_ambient_like_reads_the_area_scenes(scenes, slug, expected):
    c = _make_controller(scenes=scenes)
    c._state.transition_to_scene(slug, ActivationSource.USER)
    assert c.is_ambient_like is expected


def test_current_on_scene_slug_split_christmas_returns_christmas():
    c = _make_controller(scenes=SPLIT_SCENES)
    c._state.transition_to_scene("christmas", ActivationSource.MOTION)
    assert c.current_on_scene_slug() == "christmas"


def test_current_on_scene_slug_christmas_ambient_returns_none():
    c = _make_controller(scenes=SPLIT_SCENES)
    c._state.transition_to_scene("christmas_ambient", ActivationSource.AMBIENCE)
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


def _follower(monkeypatch, scenes=("ambient", "circadian", "evening", "off")):
    """Real controller with _activate_scene stubbed as an async spy."""
    c = _make_controller(area_id="closet", scenes=scenes)
    c.leader = _RecordingLeader("bath")

    calls: list = []

    async def fake_activate_scene(scene_slug, source=None, transition=None):
        calls.append(("activate_scene", scene_slug, source))

    monkeypatch.setattr(c, "_activate_scene", fake_activate_scene)
    c._calls = calls
    return c


@pytest.fixture
def follower_ctrl(monkeypatch):
    return _follower(monkeypatch)


async def test_handle_leader_scene_activated_follower_on_mirrors(follower_ctrl):
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    await follower_ctrl.handle_leader_change(
        follower_ctrl.leader, "evening", LeaderReason.SCENE_ACTIVATED
    )
    assert ("activate_scene", "evening", ActivationSource.LEADER) in follower_ctrl._calls


async def test_handle_leader_scene_activated_follower_off_noop(follower_ctrl):
    follower_ctrl._state.transition_to_off(ActivationSource.USER)
    await follower_ctrl.handle_leader_change(
        follower_ctrl.leader, "evening", LeaderReason.SCENE_ACTIVATED
    )
    assert follower_ctrl._calls == []


async def test_handle_leader_scene_activated_follower_ambient_noop(follower_ctrl):
    follower_ctrl._state.transition_to_scene("ambient", ActivationSource.AMBIENCE)
    await follower_ctrl.handle_leader_change(
        follower_ctrl.leader, "evening", LeaderReason.SCENE_ACTIVATED
    )
    assert follower_ctrl._calls == []


async def test_handle_leader_scene_activated_follower_manual_noop(follower_ctrl):
    follower_ctrl._state.transition_to_manual()
    await follower_ctrl.handle_leader_change(
        follower_ctrl.leader, "evening", LeaderReason.SCENE_ACTIVATED
    )
    assert follower_ctrl._calls == []


async def test_handle_leader_scene_not_on_follower_logs_warning(follower_ctrl, caplog):
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    import logging

    caplog.set_level(logging.WARNING)
    await follower_ctrl.handle_leader_change(
        follower_ctrl.leader, "movie", LeaderReason.SCENE_ACTIVATED
    )
    assert follower_ctrl._calls == []
    assert any("leader bath activated scene movie" in rec.message for rec in caplog.records)


async def test_handle_leader_holiday_not_on_follower_logs_debug(follower_ctrl, caplog):
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    import logging

    caplog.set_level(logging.DEBUG)
    await follower_ctrl.handle_leader_change(
        follower_ctrl.leader, "christmas", LeaderReason.SCENE_ACTIVATED
    )
    assert follower_ctrl._calls == []
    [record] = [r for r in caplog.records if "activated scene christmas" in r.message]
    assert record.levelno == logging.DEBUG


async def test_handle_leader_scene_activated_follower_in_split_christmas_mirrors(monkeypatch):
    follower = _follower(monkeypatch, SPLIT_SCENES)
    follower._state.transition_to_scene("christmas", ActivationSource.MOTION)
    await follower.handle_leader_change(follower.leader, "evening", LeaderReason.SCENE_ACTIVATED)
    assert follower._calls == [("activate_scene", "evening", ActivationSource.LEADER)]


async def test_handle_leader_scene_activated_follower_in_christmas_ambient_noop(monkeypatch):
    follower = _follower(monkeypatch, SPLIT_SCENES)
    follower._state.transition_to_scene("christmas_ambient", ActivationSource.AMBIENCE)
    await follower.handle_leader_change(follower.leader, "evening", LeaderReason.SCENE_ACTIVATED)
    assert follower._calls == []


async def test_handle_leader_off_default_is_noop(follower_ctrl):
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    # follow_leader_deactivation defaults to False
    await follower_ctrl.handle_leader_change(follower_ctrl.leader, None, LeaderReason.OFF)
    assert follower_ctrl._calls == []


async def test_handle_leader_off_with_follow_deactivation_turns_off(follower_ctrl):
    from custom_components.area_lighting.const import SCENE_OFF_INTERNAL

    follower_ctrl.area.follow_leader_deactivation = True
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    await follower_ctrl.handle_leader_change(follower_ctrl.leader, None, LeaderReason.OFF)
    assert ("activate_scene", SCENE_OFF_INTERNAL, ActivationSource.LEADER) in follower_ctrl._calls


async def test_handle_leader_ambient_default_is_noop(follower_ctrl):
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    await follower_ctrl.handle_leader_change(follower_ctrl.leader, None, LeaderReason.AMBIENT)
    assert follower_ctrl._calls == []


async def test_handle_leader_ambient_with_follow_deactivation_activates_ambient(follower_ctrl):
    follower_ctrl.area.follow_leader_deactivation = True
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    await follower_ctrl.handle_leader_change(follower_ctrl.leader, None, LeaderReason.AMBIENT)
    assert ("activate_scene", "ambient", ActivationSource.LEADER) in follower_ctrl._calls


async def test_handle_leader_manual_always_noop(follower_ctrl):
    follower_ctrl.area.follow_leader_deactivation = True
    follower_ctrl._state.transition_to_scene("circadian", ActivationSource.USER)
    await follower_ctrl.handle_leader_change(follower_ctrl.leader, None, LeaderReason.MANUAL)
    assert follower_ctrl._calls == []
