"""Pure-unit tests for GlobalToggles (no Home Assistant dependency)."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.area_lighting.const import DOMAIN
from custom_components.area_lighting.global_state import GlobalToggles


class _FakeStorage:
    def __init__(self, initial: dict | None = None) -> None:
        self.saved: list[dict] = []
        self._initial = initial or {}

    def get_global_state(self) -> dict:
        return dict(self._initial)

    async def async_save_global_state(self, state: dict) -> None:
        self.saved.append(dict(state))


class _FakeController:
    def __init__(self) -> None:
        self.enforced = 0
        self.cancelled = 0
        self.reconciled = 0

    def enforce_occupancy_timer(self) -> None:
        self.enforced += 1

    def cancel_occupancy_timer(self) -> None:
        self.cancelled += 1

    async def async_reconcile_demand_response(self) -> None:
        self.reconciled += 1


class _FakeHass:
    def __init__(self) -> None:
        self.data: dict = {}
        self._tasks: list = []

    def async_create_task(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.append(task)
        return task

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()


def test_defaults_enabled():
    t = GlobalToggles(_FakeHass(), _FakeStorage())
    assert t.motion_lights_enabled is True
    assert t.occupancy_timeout_enabled is True


def test_load_persisted_state_applies_and_ignores_empty():
    t = GlobalToggles(_FakeHass(), _FakeStorage())
    t.load_persisted_state({})
    assert t.motion_lights_enabled is True
    t.load_persisted_state({"motion_lights_enabled": False, "occupancy_timeout_enabled": False})
    assert t.motion_lights_enabled is False
    assert t.occupancy_timeout_enabled is False


def test_state_dict_shape():
    t = GlobalToggles(_FakeHass(), _FakeStorage())
    t.load_persisted_state({"motion_lights_enabled": False})
    assert t.state_dict() == {
        "motion_lights_enabled": False,
        "occupancy_timeout_enabled": True,
        "demand_response_active": False,
    }


@pytest.mark.asyncio
async def test_set_motion_notifies_and_saves():
    hass, storage = _FakeHass(), _FakeStorage()
    t = GlobalToggles(hass, storage)
    calls: list[int] = []
    t.add_state_listener(lambda: calls.append(1))

    await t.async_set_motion_lights_enabled(False)
    await hass.drain()

    assert t.motion_lights_enabled is False
    assert calls == [1]
    assert storage.saved == [
        {
            "motion_lights_enabled": False,
            "occupancy_timeout_enabled": True,
            "demand_response_active": False,
        }
    ]


@pytest.mark.asyncio
async def test_set_motion_idempotent():
    hass, storage = _FakeHass(), _FakeStorage()
    t = GlobalToggles(hass, storage)
    calls: list[int] = []
    t.add_state_listener(lambda: calls.append(1))

    await t.async_set_motion_lights_enabled(True)  # already True
    await hass.drain()

    assert calls == []
    assert storage.saved == []


@pytest.mark.asyncio
async def test_set_occupancy_fans_out_cancel_then_enforce():
    hass, storage = _FakeHass(), _FakeStorage()
    c1, c2 = _FakeController(), _FakeController()
    hass.data[DOMAIN] = {"controllers": {"a": c1, "b": c2}}
    t = GlobalToggles(hass, storage)

    await t.async_set_occupancy_timeout_enabled(False)
    await hass.drain()
    assert (c1.cancelled, c2.cancelled) == (1, 1)
    assert (c1.enforced, c2.enforced) == (0, 0)

    await t.async_set_occupancy_timeout_enabled(True)
    await hass.drain()
    assert (c1.enforced, c2.enforced) == (1, 1)


@pytest.mark.asyncio
async def test_remove_state_listener_stops_notifications():
    hass, storage = _FakeHass(), _FakeStorage()
    t = GlobalToggles(hass, storage)
    calls: list[int] = []

    def cb() -> None:
        calls.append(1)

    t.add_state_listener(cb)
    t.remove_state_listener(cb)
    await t.async_set_motion_lights_enabled(False)
    await hass.drain()
    assert calls == []


def test_demand_response_defaults_off():
    t = GlobalToggles(_FakeHass(), _FakeStorage())
    assert t.demand_response_active is False


def test_demand_response_load_persisted():
    t = GlobalToggles(_FakeHass(), _FakeStorage())
    t.load_persisted_state({"demand_response_active": True})
    assert t.demand_response_active is True


@pytest.mark.asyncio
async def test_set_demand_response_fans_out_and_persists():
    hass, storage = _FakeHass(), _FakeStorage()
    c1, c2 = _FakeController(), _FakeController()
    hass.data[DOMAIN] = {"controllers": {"a": c1, "b": c2}}
    t = GlobalToggles(hass, storage)

    await t.async_set_demand_response_active(True)
    await hass.drain()

    assert t.demand_response_active is True
    assert (c1.reconciled, c2.reconciled) == (1, 1)
    assert storage.saved[-1]["demand_response_active"] is True


@pytest.mark.asyncio
async def test_set_demand_response_idempotent():
    hass, storage = _FakeHass(), _FakeStorage()
    c1 = _FakeController()
    hass.data[DOMAIN] = {"controllers": {"a": c1}}
    t = GlobalToggles(hass, storage)

    await t.async_set_demand_response_active(False)  # already False
    await hass.drain()

    assert c1.reconciled == 0
    assert storage.saved == []
