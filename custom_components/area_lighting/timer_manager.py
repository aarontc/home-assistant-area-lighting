"""Async timer management for Area Lighting."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class TimerHandle:
    """A cancellable async timer that tracks its absolute deadline.

    The deadline is recorded as a timezone-aware UTC datetime so it can
    be persisted across Home Assistant restarts (D4).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        duration_seconds: float,
        callback: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        self._hass = hass
        self._name = name
        self._cancel: asyncio.TimerHandle | None = None
        self._callback = callback
        self._duration = duration_seconds
        self._deadline_utc: datetime | None = None

    def start(self, duration: float | None = None) -> None:
        """Start (or restart) the timer.

        Args:
            duration: Override the construction-time duration for this start.
        """
        self.cancel()
        effective = duration if duration is not None else self._duration
        _LOGGER.debug("Timer %s: starting (%ss)", self._name, effective)
        self._deadline_utc = dt_util.utcnow() + timedelta(seconds=effective)
        self._cancel = self._hass.loop.call_later(
            effective,
            lambda: self._hass.async_create_task(self._fire()),
        )

    def cancel(self) -> None:
        """Cancel the timer if running."""
        if self._cancel is not None:
            _LOGGER.debug("Timer %s: cancelled", self._name)
            self._cancel.cancel()
            self._cancel = None
        self._deadline_utc = None

    def restore(self, deadline_utc: datetime) -> None:
        """Re-arm the timer to fire at the given absolute UTC deadline.

        If the deadline is in the past, fires the callback immediately
        via async_create_task (D4). The callback goes through the normal
        lighting_off_fade path which honors live ambience/holiday state.

        NOTE: past-due fire happens during HA startup. External entities
        (ambient zone booleans, holiday mode) may not yet be loaded by
        their owning integrations. Accepted tradeoff per D13; revisit if
        observed to cause issues in production.
        """
        self.cancel()
        now = dt_util.utcnow()
        remaining = (deadline_utc - now).total_seconds()
        _LOGGER.debug(
            "Timer %s: restore (deadline=%s, remaining=%ss)",
            self._name,
            deadline_utc.isoformat(),
            remaining,
        )
        if remaining <= 0:
            # Past-due: fire immediately
            self._deadline_utc = None
            self._hass.async_create_task(self._fire())
            return
        self._deadline_utc = deadline_utc
        self._cancel = self._hass.loop.call_later(
            remaining,
            lambda: self._hass.async_create_task(self._fire()),
        )

    @property
    def is_active(self) -> bool:
        return self._cancel is not None

    @property
    def deadline_utc(self) -> datetime | None:
        """The absolute UTC deadline of the currently-running timer, or None."""
        return self._deadline_utc

    async def _fire(self) -> None:
        self._cancel = None
        self._deadline_utc = None
        _LOGGER.debug("Timer %s: fired", self._name)
        try:
            await self._callback()
        except Exception:
            _LOGGER.exception("Timer %s: callback error", self._name)


def parse_duration_to_seconds(duration_str: str) -> float:
    """Parse HH:MM:SS duration string to seconds."""
    parts = duration_str.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return float(parts[0])
