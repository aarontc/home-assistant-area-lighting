"""Demand-response edge reconcile: already-lit areas on flag flip."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.area_lighting.area_state import ActivationSource
from custom_components.area_lighting.global_state import GlobalToggles


def _toggles(hass: HomeAssistant) -> GlobalToggles:
    return hass.data["area_lighting"]["global"]


def _flip_flag(hass: HomeAssistant, value: bool) -> None:
    """Mutate the DR flag the way the setter would, WITHOUT its reconcile
    fan-out: flag plus generation bump (the setter bumps the generation on
    every actual change). Lets tests exercise the end-of-activation check
    in isolation."""
    toggles = _toggles(hass)
    toggles._demand_response_active = value
    toggles._dr_generation += 1


def _config() -> dict:
    entities = {f"light.den_{i}": {"state": "on", "brightness": 200} for i in range(1, 7)}
    return {
        "area_lighting": {
            "areas": [
                {
                    "id": "den",
                    "name": "Den",
                    "event_handlers": True,
                    "lights": [{"id": f"light.den_{i}", "roles": ["dimming"]} for i in range(1, 7)],
                    "scenes": [
                        {"id": "circadian", "name": "Circadian"},
                        {"id": "bright", "name": "Bright", "entities": entities},
                        {"id": "off", "name": "Off"},
                    ],
                }
            ]
        }
    }


async def _setup(hass: HomeAssistant, cfg: dict) -> None:
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "off", {})
    assert await async_setup_component(hass, "area_lighting", cfg)
    await hass.async_block_till_done()
    hass.bus.async_fire("homeassistant_started")
    await hass.async_block_till_done()


@pytest.mark.integration
async def test_reconcile_sheds_already_lit_kept_untouched(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    # Lights are physically on (simulate the scene being active pre-DR).
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    _toggles(hass)._demand_response_active = True
    service_calls.clear()
    await ctrl.async_reconcile_demand_response()
    await hass.async_block_till_done()

    off = {
        c.data["entity_id"]
        for c in service_calls
        if c.domain == "light" and c.service == "turn_off"
    }
    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    assert off == {f"light.den_{i}" for i in (3, 4, 5, 6)}  # shed tail turned off
    assert on == set()  # kept bulbs already on -> untouched


@pytest.mark.integration
async def test_reconcile_restores_on_clear(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    _toggles(hass)._demand_response_active = True
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()
    # Kept on, shed off (as DR produced).
    for i in (1, 2):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    for i in (3, 4, 5, 6):
        hass.states.async_set(f"light.den_{i}", "off", {})

    _toggles(hass)._demand_response_active = False
    service_calls.clear()
    await ctrl.async_reconcile_demand_response()
    await hass.async_block_till_done()

    on = {
        c.data["entity_id"] for c in service_calls if c.domain == "light" and c.service == "turn_on"
    }
    assert on == {f"light.den_{i}" for i in (3, 4, 5, 6)}  # shed bulbs restored


@pytest.mark.integration
async def test_reconcile_skips_manual_and_off(
    hass: HomeAssistant, helper_entities, service_calls
) -> None:
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    ctrl._state.transition_to_manual()
    _toggles(hass)._demand_response_active = True

    service_calls.clear()
    await ctrl.async_reconcile_demand_response()
    await hass.async_block_till_done()

    assert len(service_calls) == 0


def _register_stateful_light_recorder(hass: HomeAssistant) -> list[tuple[str, str]]:
    """Chronological light-command recorder that mirrors commands into states.

    Mirroring each turn_on/turn_off into the state machine makes a later
    diff-based converge see the effect of earlier commands, the way real
    bulbs would report back.
    """
    calls: list[tuple[str, str]] = []

    async def _record(call) -> None:
        eid = call.data["entity_id"]
        calls.append((call.service, eid))
        if call.service == "turn_on":
            hass.states.async_set(eid, "on", {"brightness": call.data.get("brightness", 200)})
        else:
            hass.states.async_set(eid, "off", {})

    hass.services.async_register("light", "turn_on", _record)
    hass.services.async_register("light", "turn_off", _record)
    return calls


def _gate_first_apply(ctrl, monkeypatch) -> tuple[asyncio.Event, asyncio.Event]:
    """Park the first _apply_light_state call until the test releases it.

    Because the call happens inside the reconcile's converge, the parked
    reconcile is pinned mid-execution while holding _dr_lock, letting the
    test overlap a second reconcile deterministically.
    """
    gate = asyncio.Event()
    reached = asyncio.Event()
    original_apply = ctrl._apply_light_state

    async def gated_apply(entity_id, state_data, transition=None):
        if not reached.is_set():
            reached.set()
            await gate.wait()
        await original_apply(entity_id, state_data, transition)

    monkeypatch.setattr(ctrl, "_apply_light_state", gated_apply)
    return gate, reached


@pytest.mark.integration
async def test_concurrent_reconciles_with_opposing_flags_serialize(
    hass: HomeAssistant, helper_entities, service_calls, monkeypatch
) -> None:
    """Overlapping reconciles with opposing flag states run strictly in turn.

    Reconcile A (DR on) is parked mid-converge while holding _dr_lock; the
    flag then flips off and reconcile B (DR off) starts. Without the lock,
    B would run during A's pause, see still-on bulbs, issue nothing, and
    A's stale turn_offs would land last: dark bulbs with empty tracking.
    With the lock, B runs strictly after A and relights everything A shed,
    so the final commands, states, and tracking all reflect the last flag.
    """
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    chrono = _register_stateful_light_recorder(hass)
    gate, reached = _gate_first_apply(ctrl, monkeypatch)

    _toggles(hass)._demand_response_active = True
    task_a = hass.async_create_task(ctrl.async_reconcile_demand_response())
    await reached.wait()  # A holds _dr_lock, parked mid-apply
    _toggles(hass)._demand_response_active = False
    task_b = hass.async_create_task(ctrl.async_reconcile_demand_response())
    await asyncio.sleep(0)  # let B start and queue on the lock
    gate.set()
    await asyncio.gather(task_a, task_b)
    await hass.async_block_till_done()

    # B acquired last, so the last command per shed bulb is its relight and
    # every bulb ends on: no interleaved "dark bulb, empty tracking" state.
    final = {eid: svc for svc, eid in chrono}
    for i in (3, 4, 5, 6):
        assert final[f"light.den_{i}"] == "turn_on"
    assert all(hass.states.get(f"light.den_{i}").state == "on" for i in range(1, 7))
    assert ctrl.dr_shed_ids == frozenset()
    assert ctrl._active_scene_targets["light.den_4"]["state"] == "on"


@pytest.mark.integration
async def test_reconcile_queued_on_lock_defers_when_alert_starts(
    hass: HomeAssistant, helper_entities, service_calls, monkeypatch
) -> None:
    """A reconcile that queued on _dr_lock re-checks the alert flag on entry.

    Reconcile A parks mid-converge holding the lock; an alert then starts
    while reconcile B waits on the lock. When B finally acquires it, the
    alert owns the lights, so B must defer instead of converging (the
    alert's finally block re-runs the reconcile later).
    """
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    chrono = _register_stateful_light_recorder(hass)
    gate, reached = _gate_first_apply(ctrl, monkeypatch)

    _toggles(hass)._demand_response_active = True
    task_a = hass.async_create_task(ctrl.async_reconcile_demand_response())
    await reached.wait()  # A holds _dr_lock, parked mid-apply
    # DR flips off and an alert starts while A still holds the lock.
    _toggles(hass)._demand_response_active = False
    task_b = hass.async_create_task(ctrl.async_reconcile_demand_response())
    await asyncio.sleep(0)  # let B start and queue on the lock
    ctrl._alert_active = True
    gate.set()
    await asyncio.gather(task_a, task_b)
    await hass.async_block_till_done()
    ctrl._alert_active = False

    # B deferred: nothing relit the bulbs A shed, and A's tracking stands
    # (the post-alert reconcile is responsible for the DR-off restore).
    final = {eid: svc for svc, eid in chrono}
    for i in (3, 4, 5, 6):
        assert final[f"light.den_{i}"] == "turn_off"
    assert not any(svc == "turn_on" for svc, _ in chrono)
    assert ctrl.dr_shed_ids == frozenset({f"light.den_{i}" for i in (3, 4, 5, 6)})
    assert ctrl._active_scene_targets["light.den_4"]["state"] == "off"


@pytest.mark.integration
async def test_scene_activation_mid_flip_ends_shed(
    hass: HomeAssistant, helper_entities, service_calls, monkeypatch
) -> None:
    """DR flipping on mid-activation still ends with the tail shed.

    Branch-level test: the flag is mutated directly inside a patched
    _apply_scene_data, so no setter-driven reconcile ever runs. The
    end-of-activation generation comparison alone must notice the flip
    and converge to the shed state.
    """
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})

    original = ctrl._apply_scene_data

    async def flip_after_apply(scene_slug, transition=None):
        await original(scene_slug, transition)
        _flip_flag(hass, True)

    monkeypatch.setattr(ctrl, "_apply_scene_data", flip_after_apply)

    # Chronological recorder: the service_calls fixture groups by service,
    # so it cannot assert which command landed last on a bulb.
    chrono: list[tuple[str, str]] = []

    async def _record(call) -> None:
        chrono.append((call.service, call.data["entity_id"]))

    hass.services.async_register("light", "turn_on", _record)
    hass.services.async_register("light", "turn_off", _record)

    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    final = {eid: svc for svc, eid in chrono}
    for i in (1, 2):
        assert final[f"light.den_{i}"] == "turn_on"
    for i in (3, 4, 5, 6):
        assert final[f"light.den_{i}"] == "turn_off"
    assert ctrl.dr_shed_ids == frozenset({f"light.den_{i}" for i in (3, 4, 5, 6)})
    assert ctrl._active_scene_targets["light.den_4"]["state"] == "off"


@pytest.mark.integration
async def test_scene_activation_aba_double_flip_converges(
    hass: HomeAssistant, helper_entities, service_calls, monkeypatch
) -> None:
    """DR flipping on AND back off mid-activation converges to the final flag.

    An off-on-off double flip returns the boolean to its starting value, so
    a boolean dr_at_start comparison misses it, leaving the apply's shed
    turn_offs standing against all-on tracking. Each flip bumps the setter's
    generation counter, so the end-of-activation generation comparison
    catches the ABA sequence and reconciles. As in the single-flip test, the
    flag is mutated directly (with the generation bump the setter performs)
    so no setter-driven reconcile ever runs.
    """
    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})

    chrono = _register_stateful_light_recorder(hass)
    original = ctrl._apply_scene_data

    async def flip_twice_around_apply(scene_slug, transition=None):
        _flip_flag(hass, True)  # the apply below sheds the tail
        await original(scene_slug, transition)
        _flip_flag(hass, False)  # back off before the activation ends

    monkeypatch.setattr(ctrl, "_apply_scene_data", flip_twice_around_apply)

    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    # The end-of-activation reconcile converged to the FINAL flag (DR off):
    # the bulbs the apply shed are relit and tracking carries no shed state.
    final = {eid: svc for svc, eid in chrono}
    for i in range(1, 7):
        assert final[f"light.den_{i}"] == "turn_on"
    assert ctrl.dr_shed_ids == frozenset()
    assert ctrl._active_scene_targets["light.den_4"]["state"] == "on"


@pytest.mark.integration
async def test_alert_restores_targets_from_reconcile_queued_before_it(
    hass: HomeAssistant, helper_entities, service_calls, monkeypatch
) -> None:
    """The alert's tracking snapshot reflects reconciles that beat it to the lock.

    Reconcile A (DR on) parks mid-converge holding _dr_lock with shed
    tracking already published; DR then flips off and reconcile B queues on
    the lock, followed by the alert. B converges (relights the tail, empties
    the shed set, rewrites all-on targets) strictly before the alert starts,
    so the snapshot the alert restores in its finally block must be B's
    all-on targets. A snapshot taken before the lock would capture A's shed
    targets and restore them over B's converge, leaving "off" tracking for
    physically-on bulbs with no repair reconcile (DR off, shed set empty).
    """
    from custom_components.area_lighting.alert import execute_alert
    from custom_components.area_lighting.models import AlertPattern, AlertStep

    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    chrono = _register_stateful_light_recorder(hass)
    gate, reached = _gate_first_apply(ctrl, monkeypatch)

    _toggles(hass)._demand_response_active = True
    task_a = hass.async_create_task(ctrl.async_reconcile_demand_response())
    await reached.wait()  # A holds _dr_lock, parked mid-apply, shed targets published
    _toggles(hass)._demand_response_active = False
    task_b = hass.async_create_task(ctrl.async_reconcile_demand_response())
    await asyncio.sleep(0)  # B queues on the lock

    pattern = AlertPattern(
        steps=[AlertStep(target="all", state="on", brightness=255)],
        repeat=1,
        restore=True,
    )
    alert_task = hass.async_create_task(execute_alert(hass, ctrl, pattern))
    await asyncio.sleep(0)  # the alert queues on the lock behind B

    gate.set()
    await asyncio.gather(task_a, task_b, alert_task)
    await hass.async_block_till_done()

    # B's DR-off converge ran before the alert began, so the alert restored
    # B's tracking: every bulb on, all-on targets, no shed state.
    final = {eid: svc for svc, eid in chrono}
    for i in range(1, 7):
        assert final[f"light.den_{i}"] == "turn_on"
    assert ctrl.dr_shed_ids == frozenset()
    for i in range(1, 7):
        assert ctrl._active_scene_targets[f"light.den_{i}"]["state"] == "on"


@pytest.mark.integration
async def test_alert_start_waits_for_inflight_reconcile(
    hass: HomeAssistant, helper_entities, service_calls, monkeypatch
) -> None:
    """An alert's start serializes with an in-flight reconcile on _dr_lock.

    Reconcile A parks mid-converge while holding _dr_lock; execute_alert
    then starts. The alert must not begin (no flag set, no capture, no
    flash command) until A releases the lock: the alert's first light
    command lands strictly after A's last.
    """
    from custom_components.area_lighting.alert import execute_alert
    from custom_components.area_lighting.models import AlertPattern, AlertStep

    await _setup(hass, _config())
    ctrl = hass.data["area_lighting"]["controllers"]["den"]
    for i in range(1, 7):
        hass.states.async_set(f"light.den_{i}", "on", {"brightness": 200})
    await ctrl._activate_scene("bright", ActivationSource.USER)
    await hass.async_block_till_done()

    chrono = _register_stateful_light_recorder(hass)
    gate, reached = _gate_first_apply(ctrl, monkeypatch)

    _toggles(hass)._demand_response_active = True
    task_a = hass.async_create_task(ctrl.async_reconcile_demand_response())
    await reached.wait()  # A holds _dr_lock, parked mid-apply

    pattern = AlertPattern(
        steps=[AlertStep(target="all", state="on", brightness=255)],
        repeat=1,
        restore=False,
    )
    alert_task = hass.async_create_task(execute_alert(hass, ctrl, pattern))
    for _ in range(5):
        await asyncio.sleep(0)  # give the alert every chance to (wrongly) start

    # The alert is parked on _dr_lock: not started, nothing flashed.
    assert ctrl._alert_active is False
    assert all(svc == "turn_off" for svc, _ in chrono)

    gate.set()
    await asyncio.gather(task_a, alert_task)
    await hass.async_block_till_done()

    # A's four shed turn_offs (the first four commands) all landed before
    # the alert's first flash turn_on.
    first_on = next(i for i, (svc, _) in enumerate(chrono) if svc == "turn_on")
    reconcile_offs = [i for i, (svc, _) in enumerate(chrono) if svc == "turn_off"][:4]
    assert len(reconcile_offs) == 4
    assert first_on > max(reconcile_offs)
