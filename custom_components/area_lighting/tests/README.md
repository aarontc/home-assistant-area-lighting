# Area Lighting Tests

Two layers:

- `tests/test_*.py` — pure unit tests, no HA dependency.
  Cover `scene_machine`, `area_state`, `timer_manager` and the circadian
  curve in `circadian`.
- `tests/integration/test_*.py` — integration tests using
  `pytest-homeassistant-custom-component`. Cover controller behavior,
  event handlers, persistence, validation, and edge cases.

## Running

From inside the component directory (`custom_components/area_lighting/`):

```bash
uv sync --extra dev
uv run pytest -n auto
```

Run a single layer:

```bash
# Pure unit only (fast, ~1.5s)
uv run pytest tests/test_scene_machine.py tests/test_area_state.py tests/test_timer_manager.py

# Integration only
uv run pytest tests/integration/ -n auto
```

Markers:

```bash
uv run pytest -m integration -n auto
uv run pytest -m unit
```

## Python version

The test harness uses Python 3.14 (pinned via `.python-version`), which
Home Assistant 2026.3 and later require. If `uv sync` fails with sqlite3
or build errors, install uv-managed Python 3.14:

```bash
uv python install 3.14
```

## Adding tests

- Pure-unit tests: import directly from `custom_components.area_lighting.*`
  and use plain pytest. No fixtures needed for the pure modules.
- Integration tests: use the `hass`, `helper_entities`,
  `network_room_config`, and `service_calls` fixtures from
  `tests/integration/conftest.py`.
- `service_calls` mocks `light.turn_on` without Home Assistant's argument
  schema, so it accepts calls the real service rejects. To check the
  arguments themselves, mock with `LIGHT_TURN_ON_SCHEMA` as
  `integration/test_scene_color_attributes.py` does.
- Tests that assert circadian payloads or kelvin routes use the
  `pin_circadian` fixture. `await pin_circadian(percent, kelvin)` makes
  every `CircadianClock` return those values and runs one tick. Pass
  `tick=False` to set the values before setup without ticking. Other
  tests run on the real clock and assert no circadian values.
- The harness fails any test that leaves an event-loop timer running. The
  autouse `shutdown_controllers` fixture runs `async_teardown` after each
  test. It unsubscribes the event handlers, stops the circadian clock,
  removes the entities the integration added, and closes every controller
  with its timers and listeners. A test that fires a scheduled callback
  by hand must cancel its handle first.
