"""Entity ID vs friendly name alignment tests.

Every HA entity the component registers must have an entity_id that
mirrors its friendly name, modulo snake_case conversion. This catches
the class of bugs where the ID and the display name drift apart
(e.g. `switch.network_room_override_ambient` with friendly name
'Network Room Motion Override Ambient').
"""

from __future__ import annotations

import re

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _name_to_slug(name: str) -> str:
    """Snake-case slug matching the HA convention (lowercase, underscores)."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _entity_suffix_matches_name(entity_id: str, area_id: str, name: str) -> bool:
    """The portion of the entity_id after `{domain}.{area_id}_` should
    be the snake-case slug of the portion of the name after the area's
    display name."""
    # Strip the domain and the area_id prefix from the entity_id
    local_id = entity_id.split(".", 1)[1].removeprefix(f"{area_id}_")
    # Strip the area's display name from the friendly name
    # (area name is the title-cased form of area_id)
    name_local = name
    name_slug = _name_to_slug(name_local)
    # name_slug starts with "{area_id_slug}_" — strip it
    area_slug = _name_to_slug("Network Room")
    if name_slug.startswith(area_slug + "_"):
        name_slug = name_slug[len(area_slug) + 1 :]
    return local_id == name_slug


@pytest.mark.integration
async def test_all_switch_entity_ids_match_friendly_names(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    await _setup(hass, network_room_config)
    {
        eid: state
        for eid, state in hass.states.async_all_internal().items()
        if eid.startswith("switch.network_room_")
    } if hasattr(hass.states, "async_all_internal") else None

    # Iterate all switch entities the component registered
    for entity_id in hass.states.async_entity_ids("switch"):
        if not entity_id.startswith("switch.network_room_"):
            continue
        state = hass.states.get(entity_id)
        assert state is not None
        name = state.attributes.get("friendly_name", "")
        assert _entity_suffix_matches_name(entity_id, "network_room", name), (
            f"entity_id {entity_id!r} does not match friendly_name {name!r}"
        )


@pytest.mark.integration
async def test_override_ambient_switch_id_includes_motion(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The 'Motion Override Ambient' switch's entity_id must include 'motion'."""
    await _setup(hass, network_room_config)
    # Should exist as switch.network_room_motion_override_ambient
    assert hass.states.get("switch.network_room_motion_override_ambient") is not None, (
        "expected switch.network_room_motion_override_ambient but only these "
        "area_lighting switches exist: "
        + ", ".join(
            sorted(
                eid
                for eid in hass.states.async_entity_ids("switch")
                if eid.startswith("switch.network_room_")
            )
        )
    )
    state = hass.states.get("switch.network_room_motion_override_ambient")
    assert state.attributes.get("friendly_name") == "Network Room Motion Override Ambient"


@pytest.mark.integration
async def test_diagnostic_snapshot_uses_motion_override_ambient_key(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The diagnostic snapshot dict must use the renamed key. Regression
    test for the diagnostics sensor showing 'override_ambient: False'."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    snap = ctrl.diagnostic_snapshot()
    assert "motion_override_ambient" in snap
    assert "override_ambient" not in snap


@pytest.mark.integration
async def test_state_dict_uses_motion_override_ambient_key(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The persisted state dict must use the renamed key."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    data = ctrl.state_dict()
    assert "motion_override_ambient" in data
    assert "override_ambient" not in data


@pytest.mark.integration
async def test_controller_exposes_motion_override_ambient_property(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """The controller property must be the renamed one."""
    await _setup(hass, network_room_config)
    ctrl = hass.data["area_lighting"]["controllers"]["network_room"]
    # Should have the new property
    assert hasattr(ctrl, "motion_override_ambient")
    # And should NOT have the old one
    assert not hasattr(ctrl, "override_ambient"), (
        "ctrl.override_ambient should be removed — use motion_override_ambient"
    )


# ── Icons — each entity type must have a semantically-meaningful icon ──

# Locks in the icon choices so future refactors don't drift back to
# generic or duplicate icons. Values approved by the user.
EXPECTED_ICONS = {
    # Switches
    "switch.network_room_motion_light_enabled": "mdi:motion-sensor",
    "switch.network_room_ambience_enabled": "mdi:television-ambient-light",
    "switch.network_room_night_mode": "mdi:weather-night",
    "switch.network_room_motion_override_ambient": "mdi:shield-off-outline",
    # Select
    "select.network_room_last_scene": "mdi:palette",
    # Number entities
    "number.network_room_manual_fadeout_seconds": "mdi:remote",
    "number.network_room_motion_fadeout_seconds": "mdi:motion-pause-outline",
    "number.network_room_motion_timeout_minutes": "mdi:timer-sand",
    "number.network_room_motion_night_timeout_minutes": "mdi:timer-sand-empty",
    "number.network_room_occupancy_timeout_minutes": "mdi:account-clock",
    "number.network_room_occupancy_night_timeout_minutes": "mdi:account-clock-outline",
}


@pytest.mark.integration
async def test_entity_icons_match_function(
    hass: HomeAssistant, helper_entities, network_room_config
) -> None:
    """Every helper entity must expose its approved icon."""
    await _setup(hass, network_room_config)
    mismatches: list[str] = []
    for entity_id, expected_icon in EXPECTED_ICONS.items():
        state = hass.states.get(entity_id)
        if state is None:
            mismatches.append(f"{entity_id}: entity not found")
            continue
        actual = state.attributes.get("icon")
        if actual != expected_icon:
            mismatches.append(f"{entity_id}: expected {expected_icon!r}, got {actual!r}")
    assert not mismatches, "Icon mismatches:\n  " + "\n  ".join(mismatches)
