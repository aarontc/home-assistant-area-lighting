"""Component-local pytest conftest.

Drives pytest-homeassistant-custom-component from inside the component
directory. Three jobs:

1. Put the repo root on sys.path so `from custom_components.area_lighting.*`
   imports resolve in both pure-unit and integration tests.
2. Load pytest-homeassistant-custom-component as a plugin so its fixtures
   (`hass`, `enable_custom_integrations`, etc.) are available.
3. Symlink this component into the HA test harness's `testing_config/
   custom_components/` directory so HA's integration loader can discover
   `area_lighting`. The symlink is created lazily at session start and is
   only needed by the integration layer, but creating it here avoids
   racing with pytest-xdist workers.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_COMPONENT_DIR = Path(__file__).resolve().parent          # custom_components/area_lighting
_CUSTOM_COMPONENTS_DIR = _COMPONENT_DIR.parent             # custom_components
_REPO_ROOT = _CUSTOM_COMPONENTS_DIR.parent                 # repo root

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _ensure_component_symlink_in_testing_config() -> None:
    """Symlink this component into pytest-homeassistant-custom-component's
    testing_config/custom_components/ so HA's loader finds it.

    Runs at conftest import time so it's in place before any test collects.
    Idempotent: a correct existing symlink is left alone.
    """
    try:
        import pytest_homeassistant_custom_component as phcc  # noqa: WPS433
    except ImportError:
        # Plugin not installed → pure-unit tests still run, integration
        # tests will fail loudly with their own error.
        return

    phcc_dir = Path(phcc.__file__).resolve().parent
    target_dir = phcc_dir / "testing_config" / "custom_components" / "area_lighting"

    if target_dir.is_symlink():
        try:
            if target_dir.resolve() == _COMPONENT_DIR.resolve():
                return  # already correct
        except OSError:
            pass
        target_dir.unlink()
    elif target_dir.exists():
        # Something non-symlink is there — leave it alone and log to stderr
        print(
            f"[area_lighting conftest] warning: {target_dir} exists and is "
            "not a symlink; skipping auto-link.",
            file=sys.stderr,
        )
        return

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(_COMPONENT_DIR, target_dir)


_ensure_component_symlink_in_testing_config()

# Loads pytest-homeassistant-custom-component's fixtures (hass, enable_custom_integrations, etc.)
pytest_plugins = ["pytest_homeassistant_custom_component"]
