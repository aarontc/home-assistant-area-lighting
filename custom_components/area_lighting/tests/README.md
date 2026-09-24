# Area Lighting Tests

Two layers:

- `tests/test_*.py` — pure unit tests, no HA dependency.
  Cover `scene_machine`, `area_state`, and `timer_manager`.
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
- The harness fails any test that leaves an event-loop timer running. The
  autouse `shutdown_controllers` fixture cancels the controllers' timers;
  a test that fires a scheduled callback by hand must cancel its handle
  first.
