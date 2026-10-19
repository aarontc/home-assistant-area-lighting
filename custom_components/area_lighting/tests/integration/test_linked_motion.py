"""Tests for linked_motion cross-area coordination."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


def _two_area_config() -> dict:
    """Config with stairs (has linked_motion) and theater (target)."""
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "theater",
                    "name": "Theater",
                    "event_handlers": True,
                    "lights": [
                        {"id": "light.theater_overhead", "roles": ["dimming"]},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "daylight", "name": "Daylight"},
                        {"id": "evening", "name": "Evening"},
                        {"id": "movie", "name": "Movie"},
                        {"id": "stairs", "name": "Stairs"},
                    ],
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.theater_motion",
                    ],
                    "motion_light_timer_durations": {"off": "00:08:00"},
                },
                {
                    "id": "stairs",
                    "name": "Stairs",
                    "event_handlers": True,
                    "lights": [
                        {"id": "light.stairs_upper", "roles": ["dimming"]},
                    ],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "movie", "name": "Movie"},
                    ],
                    "motion_light_motion_sensor_ids": [
                        "binary_sensor.stairs_motion",
                    ],
                    "motion_light_timer_durations": {"off": "00:02:00"},
                    "linked_motion": [
                        {
                            "remote_area": "theater",
                            "default": {
                                "local_scene": "circadian",
                                "remote_scene": None,
                            },
                            "when_remote_scene": {
                                "off": {
                                    "local_scene": "circadian",
                                    "remote_scene": "stairs",
                                },
                                "movie": {
                                    "local_scene": "movie",
                                    "remote_scene": None,
                                },
                            },
                        }
                    ],
                },
            ]
        }
    }


@pytest.mark.integration
async def test_linked_basic_activation_theater_off(hass: HomeAssistant, helper_entities) -> None:
    """Stairs motion + theater off -> stairs=circadian, theater=stairs."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    assert stairs_ctrl._state.scene_slug == "circadian" or stairs_ctrl._state.is_circadian
    assert theater_ctrl._state.scene_slug == "stairs"
    assert theater_ctrl._state.source == ActivationSource.LINKED


@pytest.mark.integration
async def test_linked_movie_mode(hass: HomeAssistant, helper_entities) -> None:
    """Stairs motion + theater in movie -> stairs=movie, theater untouched."""
    hass.states.async_set("light.theater_overhead", "on")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]
    theater_ctrl._state.transition_to_scene("movie", ActivationSource.USER)

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    assert stairs_ctrl._state.scene_slug == "movie"
    assert theater_ctrl._state.scene_slug == "movie"
    assert theater_ctrl._state.source == ActivationSource.USER


@pytest.mark.integration
async def test_linked_default_no_match(hass: HomeAssistant, helper_entities) -> None:
    """Stairs motion + theater in daylight -> stairs=circadian, theater untouched."""
    hass.states.async_set("light.theater_overhead", "on")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]
    theater_ctrl._state.transition_to_scene("daylight", ActivationSource.USER)

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    assert stairs_ctrl._state.scene_slug == "circadian" or stairs_ctrl._state.is_circadian
    assert theater_ctrl._state.scene_slug == "daylight"
    assert theater_ctrl._state.source == ActivationSource.USER


@pytest.mark.integration
async def test_linked_cleanup_normal(hass: HomeAssistant, helper_entities) -> None:
    """Stairs timer expires + theater still in 'stairs' -> theater goes off."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()
    assert theater_ctrl._state.scene_slug == "stairs"

    await stairs_ctrl.handle_motion_off()
    await hass.async_block_till_done()

    # Fire timer callback directly
    await stairs_ctrl._on_motion_timer()
    await hass.async_block_till_done()

    assert theater_ctrl._state.is_off


@pytest.mark.integration
async def test_linked_cleanup_manual_override(hass: HomeAssistant, helper_entities) -> None:
    """Theater changed manually during stairs activity -> cleanup skips theater."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()
    assert theater_ctrl._state.scene_slug == "stairs"

    # User manually changes theater to evening
    theater_ctrl._state.transition_to_scene("evening", ActivationSource.USER)

    await stairs_ctrl._on_motion_timer()
    await hass.async_block_till_done()

    # Theater should stay in evening — NOT forced to off
    assert theater_ctrl._state.scene_slug == "evening"


@pytest.mark.integration
async def test_linked_cleanup_null_remote_scene(hass: HomeAssistant, helper_entities) -> None:
    """When remote_scene was null (movie case), no cleanup needed."""
    hass.states.async_set("light.theater_overhead", "on")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]
    theater_ctrl._state.transition_to_scene("movie", ActivationSource.USER)

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    await stairs_ctrl._on_motion_timer()
    await hass.async_block_till_done()

    assert theater_ctrl._state.scene_slug == "movie"


@pytest.mark.integration
async def test_linked_retrigger_no_duplicate(hass: HomeAssistant, helper_entities) -> None:
    """Re-triggering stairs motion doesn't re-activate theater if it was changed."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()
    assert theater_ctrl._state.scene_slug == "stairs"

    # User changes theater while stairs is active
    theater_ctrl._state.transition_to_scene("evening", ActivationSource.USER)

    # Stairs re-triggers — theater is in "evening", not in "off" or "movie",
    # so default mapping applies (remote_scene=null), theater untouched
    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    assert theater_ctrl._state.scene_slug == "evening"


@pytest.mark.integration
async def test_linked_invalid_remote_area(hass: HomeAssistant, helper_entities) -> None:
    """linked_motion references non-existent area -> local area still works."""
    cfg = _two_area_config()
    cfg["area_lighting"]["areas"][1]["linked_motion"][0]["remote_area"] = "nonexistent"
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, cfg)

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    # Stairs still activates (falls back to default local_scene since resolve
    # can't find the remote controller, but doesn't crash)
    assert stairs_ctrl._state.scene_slug == "circadian" or stairs_ctrl._state.is_circadian


@pytest.mark.integration
async def test_linked_retrigger_then_cleanup_turns_theater_off(
    hass: HomeAssistant, helper_entities
) -> None:
    """Re-trigger while theater is still in the linked scene -> cleanup still
    turns theater off.

    Regression: _activate_linked_areas() must not discard the pending-cleanup
    tracking when a later motion event resolves to no remote activation. The
    second stairs motion-on happens while the theater is still in "stairs"
    (the linked scene), which resolves to the default mapping (remote_scene
    null). If that wipes _linked_activated_scenes, the subsequent timer expiry
    no-ops and the theater is stranded on.
    """
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    # First stairs motion: theater is off -> theater activated to "stairs".
    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()
    assert theater_ctrl._state.scene_slug == "stairs"

    # Stairs motion re-fires while theater is STILL in "stairs" (no manual
    # override). Resolution yields no remote activation; the tracking that
    # cleanup relies on must survive.
    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()
    assert theater_ctrl._state.scene_slug == "stairs"

    # Motion stops and the stairs timer expires -> theater must turn off.
    await stairs_ctrl.handle_motion_off()
    await hass.async_block_till_done()
    await stairs_ctrl._on_motion_timer()
    await hass.async_block_till_done()

    assert theater_ctrl._state.is_off


@pytest.mark.integration
async def test_linked_skips_remote_activated_during_local_apply(
    hass: HomeAssistant, helper_entities
) -> None:
    """A remote press that lands while stairs applies its own scene wins.

    Regression: handle_motion_on resolved the theater as "off", awaited the
    stairs lights, then applied the linked "stairs" scene on top of the
    circadian scene a Pico press had started in the meantime. The stairs
    motion timer then turned the theater off under the user.
    """
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    original_activate = stairs_ctrl._activate_scene

    async def activate_with_remote_press(*args, **kwargs):
        await theater_ctrl._activate_scene("circadian", ActivationSource.USER)
        await original_activate(*args, **kwargs)

    stairs_ctrl._activate_scene = activate_with_remote_press

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    assert theater_ctrl._state.is_circadian
    assert theater_ctrl._state.source == ActivationSource.USER

    await stairs_ctrl.handle_motion_off()
    await stairs_ctrl._on_motion_timer()
    await hass.async_block_till_done()

    assert theater_ctrl._state.is_circadian


@pytest.mark.integration
async def test_linked_skips_remote_with_activation_in_flight(
    hass: HomeAssistant, helper_entities
) -> None:
    """A remote scene still applying when stairs motion fires is left alone.

    A visual scene commits its state only after its light commands finish,
    so the theater still reads "off" while one is in flight.
    """
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    release = asyncio.Event()
    original_apply = theater_ctrl._apply_scene_data

    async def slow_apply(scene_slug, *args, **kwargs):
        if scene_slug == "daylight":
            await release.wait()
        await original_apply(scene_slug, *args, **kwargs)

    theater_ctrl._apply_scene_data = slow_apply
    press = hass.async_create_task(theater_ctrl._activate_scene("daylight", ActivationSource.USER))
    await asyncio.sleep(0)
    assert theater_ctrl.current_scene == "off"

    await stairs_ctrl.handle_motion_on()
    release.set()
    await press
    await hass.async_block_till_done()

    assert theater_ctrl._state.scene_slug == "daylight"
    assert theater_ctrl._state.source == ActivationSource.USER
    assert not stairs_ctrl._linked_activated_scenes


@pytest.mark.integration
async def test_linked_skips_remote_whose_activation_ends_off_during_local_apply(
    hass: HomeAssistant, helper_entities
) -> None:
    """An off already in flight at resolution is not followed by a linked scene.

    The off claims its sequence before resolution and leaves the theater
    reading "off", so only the in-flight count at resolution catches it.
    """
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    release = asyncio.Event()
    original_turn_off = theater_ctrl._turn_off_all_lights

    async def slow_turn_off(*args, **kwargs):
        await release.wait()
        await original_turn_off(*args, **kwargs)

    theater_ctrl._turn_off_all_lights = slow_turn_off
    force_off = hass.async_create_task(
        theater_ctrl._activate_scene("off_internal", ActivationSource.USER)
    )
    await asyncio.sleep(0)

    original_activate = stairs_ctrl._activate_scene

    async def activate_then_finish_off(*args, **kwargs):
        await original_activate(*args, **kwargs)
        release.set()
        await force_off

    stairs_ctrl._activate_scene = activate_then_finish_off

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    assert theater_ctrl._state.is_off
    assert not stairs_ctrl._linked_activated_scenes


@pytest.mark.integration
async def test_linked_cleanup_leaves_same_scene_chosen_by_user(
    hass: HomeAssistant, helper_entities
) -> None:
    """Cleanup does not turn off a linked scene someone has since chosen."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()
    assert theater_ctrl._state.source == ActivationSource.LINKED

    await theater_ctrl._activate_scene("stairs", ActivationSource.USER)

    await stairs_ctrl.handle_motion_off()
    await stairs_ctrl._on_motion_timer()
    await hass.async_block_till_done()

    assert theater_ctrl._state.scene_slug == "stairs"
    assert theater_ctrl._state.source == ActivationSource.USER


@pytest.mark.integration
async def test_linked_cleanup_keeps_relink_made_during_cleanup(
    hass: HomeAssistant, helper_entities
) -> None:
    """A linked activation recorded while cleanup awaits survives it.

    Cleanup used to iterate the live dict across its awaits and then clear
    it, so a new entry either raised RuntimeError or was silently dropped.
    A re-link of the same remote area must not be mistaken for the entry
    cleanup is processing either.
    """
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()
    stairs_ctrl._linked_activated_scenes["kitchen"] = ("stairs", 100)

    original_activate = theater_ctrl._activate_scene

    async def activate_with_relink(scene_slug, *args, **kwargs):
        stairs_ctrl._linked_activated_scenes["theater"] = ("stairs", 101)
        stairs_ctrl._linked_activated_scenes["kitchen"] = ("stairs", 102)
        await original_activate(scene_slug, *args, **kwargs)

    theater_ctrl._activate_scene = activate_with_relink

    await stairs_ctrl._cleanup_linked_areas()
    await hass.async_block_till_done()

    assert theater_ctrl._state.is_off
    assert stairs_ctrl._linked_activated_scenes == {
        "theater": ("stairs", 101),
        "kitchen": ("stairs", 102),
    }


@pytest.mark.integration
async def test_linked_cleanup_keeps_unreached_entries_when_it_raises(
    hass: HomeAssistant, helper_entities
) -> None:
    """An entry cleanup did not reach before raising stays for the next run."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()
    stairs_ctrl._linked_activated_scenes["kitchen"] = ("stairs", 100)

    async def failing_activate(*args, **kwargs):
        raise RuntimeError("light service failed")

    theater_ctrl._activate_scene = failing_activate

    with pytest.raises(RuntimeError):
        await stairs_ctrl._cleanup_linked_areas()

    assert stairs_ctrl._linked_activated_scenes == {"kitchen": ("stairs", 100)}


@pytest.mark.integration
async def test_linked_skips_commit_when_remote_pressed_during_linked_apply(
    hass: HomeAssistant, helper_entities
) -> None:
    """A press during the remote's own linked apply wins and is not cleaned up."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    original_apply = theater_ctrl._apply_scene_data

    async def apply_with_press(scene_slug, *args, **kwargs):
        if scene_slug == "stairs":
            await theater_ctrl._activate_scene("circadian", ActivationSource.USER)
        await original_apply(scene_slug, *args, **kwargs)

    theater_ctrl._apply_scene_data = apply_with_press

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    assert theater_ctrl._state.is_circadian
    assert theater_ctrl._state.source == ActivationSource.USER
    assert not stairs_ctrl._linked_activated_scenes


@pytest.mark.integration
async def test_linked_cleanup_skips_remote_with_activation_in_flight(
    hass: HomeAssistant, helper_entities
) -> None:
    """Cleanup leaves a remote area alone while a user scene is applying."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    stairs_ctrl = hass.data["area_lighting"]["controllers"]["stairs"]
    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    await stairs_ctrl.handle_motion_on()
    await hass.async_block_till_done()

    release = asyncio.Event()
    original_apply = theater_ctrl._apply_scene_data

    async def slow_apply(scene_slug, *args, **kwargs):
        if scene_slug == "daylight":
            await release.wait()
        await original_apply(scene_slug, *args, **kwargs)

    theater_ctrl._apply_scene_data = slow_apply
    press = hass.async_create_task(theater_ctrl._activate_scene("daylight", ActivationSource.USER))
    await asyncio.sleep(0)
    assert theater_ctrl._state.source == ActivationSource.LINKED

    await stairs_ctrl._cleanup_linked_areas()
    assert not theater_ctrl._state.is_off
    assert "theater" in stairs_ctrl._linked_activated_scenes
    release.set()
    await press
    await hass.async_block_till_done()

    assert theater_ctrl._state.scene_slug == "daylight"
    assert theater_ctrl._state.source == ActivationSource.USER

    # The kept entry no longer matches, so the next cleanup drops it.
    await stairs_ctrl._cleanup_linked_areas()
    assert theater_ctrl._state.scene_slug == "daylight"
    assert not stairs_ctrl._linked_activated_scenes


@pytest.mark.integration
async def test_external_scene_activation_takes_an_activation_claim(
    hass: HomeAssistant, helper_entities
) -> None:
    """scene.turn_on tracking and lighting_circadian supersede yielding activations."""
    hass.states.async_set("light.theater_overhead", "off")
    hass.states.async_set("light.stairs_upper", "off")
    hass.states.async_set("binary_sensor.theater_motion", "off")
    hass.states.async_set("binary_sensor.stairs_motion", "off")
    await _setup(hass, _two_area_config())

    theater_ctrl = hass.data["area_lighting"]["controllers"]["theater"]

    before = theater_ctrl._activation_seq
    await theater_ctrl.handle_scene_activated("movie")
    assert theater_ctrl._activation_seq == before + 1

    await theater_ctrl.lighting_circadian()
    assert theater_ctrl._activation_seq == before + 2
