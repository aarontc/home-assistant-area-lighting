"""First-class state machine for an area's lighting.

Replaces the scattered boolean flags (`_current_scene`, `_dimmed`,
`_circadian_active`, `_ambient_activated`, `_triggered_by_motion`) with
a single explicit state object.

The state machine has three components:

1. **State**: the high-level mode the area is in (OFF, SCENE, CIRCADIAN, MANUAL)
2. **Source**: how the current state was entered (USER, MOTION, AMBIENCE, ...)
3. **Modifiers**: dimmed flag, current/previous scene slug

All transitions go through `transition_to()` which logs and emits a single
state-change event. This module is pure (no HA dependencies) so the state
logic is unit-testable in isolation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

_LOGGER = logging.getLogger(__name__)


class LightingState(Enum):
    OFF = "off"
    SCENE = "scene"
    CIRCADIAN = "circadian"
    MANUAL = "manual"


class ActivationSource(Enum):
    USER = "user"  # Explicit: service call, scene.turn_on, behavioral scene
    REMOTE = "remote"  # Lutron remote button
    MOTION = "motion"  # Motion sensor
    OCCUPANCY = "occupancy"  # Occupancy sensor
    AMBIENCE = "ambience"  # Ambience mode toggle
    HOLIDAY = "holiday"  # Holiday mode change
    RESTORED = "restored"  # Loaded from persistence
    MANUAL = "manual"  # Detected manual light adjustment
    LINKED = "linked"  # Cross-area linked motion activation
    LEADER = "leader"  # Follower scene mirrored from its leader area


class LeaderReason(Enum):
    """Why a leader is notifying its followers of a state change."""

    SCENE_ACTIVATED = (
        "scene_activated"  # Leader entered a concrete on-scene (not ambient, not manual)
    )
    OFF = "off"  # Leader turned off
    AMBIENT = "ambient"  # Leader entered ambient/holiday scene
    MANUAL = "manual"  # Leader entered manual mode


# Scene slugs that count as "ambient-style" (will be cleaned up when ambience disabled)
AMBIENT_LIKE_SCENES = frozenset({"ambient", "christmas", "halloween"})


@dataclass
class AreaState:
    """The current lighting state of an area."""

    state: LightingState = LightingState.OFF
    scene_slug: str = "off"
    source: ActivationSource = ActivationSource.USER
    dimmed: bool = False
    # Scene to restore to when dimmed=True is cleared via lighting_on
    previous_scene: str | None = None
    # Monotonic timestamp of the most recent scene transition. Used by
    # manual-detection grace period (D5/D15). Intentionally excluded from
    # persistence: monotonic times don't survive process restart.
    last_scene_change_monotonic: float | None = None

    # ── Queries ────────────────────────────────────────────────────────

    @property
    def is_off(self) -> bool:
        return self.state == LightingState.OFF

    @property
    def is_on(self) -> bool:
        return self.state != LightingState.OFF

    @property
    def is_circadian(self) -> bool:
        return self.state == LightingState.CIRCADIAN

    @property
    def is_manual(self) -> bool:
        return self.state == LightingState.MANUAL

    @property
    def is_scene(self) -> bool:
        return self.state == LightingState.SCENE

    @property
    def is_ambient_like(self) -> bool:
        """Whether the current scene is ambient/holiday."""
        return self.scene_slug in AMBIENT_LIKE_SCENES

    @property
    def was_ambient_activated(self) -> bool:
        """True if currently in an ambient-like scene because of ambience mode."""
        return self.is_ambient_like and self.source == ActivationSource.AMBIENCE

    @property
    def was_motion_triggered(self) -> bool:
        return self.source == ActivationSource.MOTION

    # ── Transitions ────────────────────────────────────────────────────

    def transition_to_off(self, source: ActivationSource = ActivationSource.USER) -> None:
        self.state = LightingState.OFF
        self.scene_slug = "off"
        self.source = source
        self.dimmed = False
        self.previous_scene = None
        self.last_scene_change_monotonic = time.monotonic()

    def transition_to_scene(
        self,
        scene_slug: str,
        source: ActivationSource = ActivationSource.USER,
    ) -> None:
        self.state = LightingState.SCENE
        self.scene_slug = scene_slug
        self.source = source
        # New scene activation always clears dimmed and forgets the restore target
        self.dimmed = False
        self.previous_scene = None
        self.last_scene_change_monotonic = time.monotonic()

    def transition_to_circadian(
        self,
        source: ActivationSource = ActivationSource.USER,
    ) -> None:
        self.state = LightingState.CIRCADIAN
        self.scene_slug = "circadian"
        self.source = source
        self.dimmed = False
        self.previous_scene = None
        self.last_scene_change_monotonic = time.monotonic()

    def transition_to_manual(self) -> None:
        self.state = LightingState.MANUAL
        self.scene_slug = "manual"
        self.source = ActivationSource.MANUAL
        self.dimmed = False
        self.previous_scene = None
        self.last_scene_change_monotonic = time.monotonic()

    def mark_dimmed(self) -> None:
        """Apply the dimmed modifier, remembering the current scene."""
        if not self.dimmed:
            self.previous_scene = self.scene_slug
            self.dimmed = True

    def clear_dimmed(self) -> str | None:
        """Clear the dimmed flag and return the previous scene to restore."""
        if not self.dimmed:
            return None
        prev = self.previous_scene
        self.dimmed = False
        self.previous_scene = None
        return prev

    # ── Persistence ────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "scene_slug": self.scene_slug,
            "source": self.source.value,
            "dimmed": self.dimmed,
            "previous_scene": self.previous_scene,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AreaState:
        if not data:
            return cls()
        try:
            return cls(
                state=LightingState(data.get("state", "off")),
                scene_slug=data.get("scene_slug", "off"),
                source=ActivationSource(data.get("source", "restored")),
                dimmed=bool(data.get("dimmed", False)),
                previous_scene=data.get("previous_scene"),
            )
        except (ValueError, KeyError) as e:
            _LOGGER.warning("Failed to restore AreaState (%s); using defaults", e)
            return cls()
