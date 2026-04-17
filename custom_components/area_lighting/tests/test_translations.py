"""Pure-unit tests for the translations/en.json file.

HA's Developer Tools "Actions" view reads friendly names from
translations/en.json under services.<service_name>.name. These tests
verify the file exists and has an entry for every registered service.
"""

from __future__ import annotations

import json
from pathlib import Path

# Service names registered in services.py (from SERVICE_MAP + snapshot_scene + alert).
REGISTERED_SERVICES = {
    "lighting_on",
    "lighting_off",
    "lighting_off_fade",
    "lighting_favorite",
    "lighting_raise",
    "lighting_lower",
    "lighting_circadian",
    "snapshot_scene",
    "alert",
}


def _translations_path() -> Path:
    return Path(__file__).resolve().parent.parent / "translations" / "en.json"


def test_translations_file_exists():
    path = _translations_path()
    assert path.is_file(), f"expected translations file at {path}"


def test_translations_has_services_block():
    data = json.loads(_translations_path().read_text())
    assert "services" in data
    assert isinstance(data["services"], dict)


def test_every_registered_service_has_a_translation():
    data = json.loads(_translations_path().read_text())
    services = data.get("services", {})
    missing = REGISTERED_SERVICES - set(services.keys())
    assert not missing, f"services missing translations: {sorted(missing)}"


def test_every_translated_service_has_name_and_description():
    data = json.loads(_translations_path().read_text())
    services = data.get("services", {})
    for service_name in REGISTERED_SERVICES:
        entry = services.get(service_name, {})
        assert "name" in entry, f"{service_name}: missing name"
        assert "description" in entry, f"{service_name}: missing description"
        assert entry["name"].strip(), f"{service_name}: empty name"
        assert entry["description"].strip(), f"{service_name}: empty description"


def test_services_with_area_id_field_have_field_translation():
    data = json.loads(_translations_path().read_text())
    services = data.get("services", {})
    # All lighting_* services take area_id; snapshot_scene takes area_id + scene.
    services_with_area_id = REGISTERED_SERVICES
    for service_name in services_with_area_id:
        entry = services.get(service_name, {})
        fields = entry.get("fields", {})
        assert "area_id" in fields, f"{service_name}: missing fields.area_id translation"
        assert "name" in fields["area_id"], f"{service_name}: area_id missing name"


def test_snapshot_scene_has_scene_field_translation():
    data = json.loads(_translations_path().read_text())
    entry = data.get("services", {}).get("snapshot_scene", {})
    fields = entry.get("fields", {})
    assert "scene" in fields
    assert "name" in fields["scene"]
