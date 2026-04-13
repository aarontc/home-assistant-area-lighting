"""Pure-unit tests for TimerHandle deadline + restore behavior (D4)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.area_lighting.timer_manager import (
    TimerHandle,
    parse_duration_to_seconds,
)


class _FakeHass:
    """Minimal hass stub exposing loop + async_create_task."""

    def __init__(self) -> None:
        self.loop = asyncio.get_event_loop()
        self._tasks: list = []

    def async_create_task(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.append(task)
        return task

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()


async def _noop() -> None:
    pass


@pytest.mark.asyncio
async def test_deadline_utc_none_when_inactive():
    hass = _FakeHass()
    handle = TimerHandle(hass, "t", 60.0, _noop)
    assert handle.deadline_utc is None


@pytest.mark.asyncio
async def test_deadline_utc_set_on_start():
    hass = _FakeHass()
    handle = TimerHandle(hass, "t", 60.0, _noop)
    before = datetime.now(timezone.utc)
    handle.start()
    after = datetime.now(timezone.utc)
    assert handle.deadline_utc is not None
    expected_min = before + timedelta(seconds=60)
    expected_max = after + timedelta(seconds=60)
    assert expected_min <= handle.deadline_utc <= expected_max
    handle.cancel()


@pytest.mark.asyncio
async def test_start_with_duration_override():
    hass = _FakeHass()
    handle = TimerHandle(hass, "t", 60.0, _noop)
    before = datetime.now(timezone.utc)
    handle.start(duration=30.0)
    assert handle.deadline_utc is not None
    expected = before + timedelta(seconds=30)
    assert abs((handle.deadline_utc - expected).total_seconds()) < 1.0
    handle.cancel()


@pytest.mark.asyncio
async def test_cancel_clears_deadline():
    hass = _FakeHass()
    handle = TimerHandle(hass, "t", 60.0, _noop)
    handle.start()
    handle.cancel()
    assert handle.deadline_utc is None


@pytest.mark.asyncio
async def test_restore_future_deadline_arms_timer():
    hass = _FakeHass()
    handle = TimerHandle(hass, "t", 60.0, _noop)
    future = datetime.now(timezone.utc) + timedelta(seconds=45)
    handle.restore(future)
    assert handle.is_active
    assert handle.deadline_utc is not None
    handle.cancel()


@pytest.mark.asyncio
async def test_restore_past_deadline_fires_immediately():
    fired = asyncio.Event()

    async def cb() -> None:
        fired.set()

    hass = _FakeHass()
    handle = TimerHandle(hass, "t", 60.0, cb)
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    handle.restore(past)
    await hass.drain()
    assert fired.is_set()


def test_parse_duration_hhmmss():
    assert parse_duration_to_seconds("00:08:00") == 480
    assert parse_duration_to_seconds("00:05:00") == 300
    assert parse_duration_to_seconds("01:00:00") == 3600
