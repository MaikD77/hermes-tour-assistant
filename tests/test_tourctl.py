from __future__ import annotations

import json
import os
from pathlib import Path


def test_redaction_removes_coordinates(tourctl_module) -> None:
    value = {"lat": 50.0, "nested": {"longitude": 10.0, "name": "Erfurt"}}

    redacted = tourctl_module.redact(value)

    assert redacted["lat"] == "[redacted]"
    assert redacted["nested"]["longitude"] == "[redacted]"
    assert redacted["nested"]["name"] == "Erfurt"


def test_diagnose_detects_public_permissions(tourctl_module, tmp_path: Path) -> None:
    path = tmp_path / "live_tour_assistant.json"
    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    os.chmod(path, 0o644)

    report = tourctl_module.diagnose(tmp_path)

    assert report["ok"] is False
    assert report["files"]["assistant_state"]["private_permissions"] is False


def test_cleanup_only_removes_expired_runtime_files(tourctl_module, tmp_path: Path) -> None:
    old_gpx = tmp_path / "current-tour-1.gpx"
    new_gpx = tmp_path / "current-tour-2.gpx"
    state = tmp_path / "live_tour_assistant.json"
    for path in (old_gpx, new_gpx, state):
        path.write_text("data", encoding="utf-8")
    os.utime(old_gpx, (1_000.0, 1_000.0))
    os.utime(new_gpx, (9_900.0, 9_900.0))
    os.utime(state, (1_000.0, 1_000.0))

    deleted = tourctl_module.cleanup(tmp_path, older_than_hours=1, now=10_000.0)

    assert deleted == [str(old_gpx)]
    assert new_gpx.exists()
    assert state.exists()
