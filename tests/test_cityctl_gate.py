from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import city_state
import pytest


def _args(tmp_path: Path, command: str, **values):
    return SimpleNamespace(state_dir=tmp_path, command=command, **values)


def _seed_cli_state(cityctl_module, tmp_path: Path, *, status="active", stop_status="approaching"):
    runtime = cityctl_module.CityRuntime(tmp_path / "city_guide_state.json")
    state = city_state.empty_state()
    state["session"].update(
        {
            "id": "session-safe",
            "status": status,
            "started_at": 1.0,
            "expires_at": 10_000.0,
        }
    )
    state["itinerary"].update(
        {
            "status": "ready",
            "provider": "fixture",
            "revision": 1,
            "distance_m": 500.0,
            "walking_seconds": 300.0,
            "dwell_seconds": 240.0,
            "route_points": [[48.1, 11.5], [48.101, 11.501]],
            "stops": [
                {
                    "stop_id": "stop-1",
                    "name": "Testplatz",
                    "lat": 48.1,
                    "lon": 11.5,
                    "category": "square",
                    "confidence": 0.8,
                    "sources": [],
                    "facts": [],
                    "route_progress_m": 0.0,
                    "status": stop_status,
                }
            ],
        }
    )
    state["position"] = {"lat": 48.1, "lon": 11.5, "observed_at": 1.0}
    state["stories"] = {
        "stop-1": {
            "title": "Testplatz",
            "text": "Eine belegte Geschichte.",
            "detail": "Ein belegtes Detail.",
            "sources": [
                {
                    "label": "Wikipedia",
                    "url": "https://de.wikipedia.org/Testplatz",
                }
            ],
        }
    }
    runtime.repository.save(state)
    return runtime


def test_redaction_removes_location_and_ids(cityctl_module) -> None:
    value = {
        "lat": 48.1,
        "nested": {"chat_id": "123", "safe": "ok"},
        "route_points": [[48.1, 11.5]],
        "tuple": ({"longitude": 11.5},),
    }

    redacted = cityctl_module.redact(value)

    assert redacted["lat"] == "[redacted]"
    assert redacted["nested"]["chat_id"] == "[redacted]"
    assert redacted["nested"]["safe"] == "ok"
    assert "48.1" not in json.dumps(redacted)


def test_latest_location_reads_private_snapshot(
    cityctl_module,
    monkeypatch,
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "telegram_live_locations.json"
    snapshot.write_text(
        json.dumps(
            {
                "locations": [
                    {
                        "chat_id": "42",
                        "message_id": "live-1",
                        "lat": 48.1,
                        "lon": 11.5,
                        "updated_at": 100.0,
                        "expires_at": 1_000.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cityctl_module, "SNAPSHOT", snapshot)
    monkeypatch.setenv("HERMES_CITY_GUIDE_CHAT_ID", "42")

    sample = cityctl_module.latest_location(now=101.0, state_dir=tmp_path)

    assert sample.session_id.startswith("telegram-")
    assert sample.lat == 48.1


def test_latest_location_requires_configuration(cityctl_module, monkeypatch) -> None:
    monkeypatch.delenv("HERMES_CITY_GUIDE_CHAT_ID", raising=False)
    monkeypatch.delenv("HERMES_TOUR_CHAT_ID", raising=False)

    with pytest.raises(ValueError, match="missing_chat_id"):
        cityctl_module.latest_location(now=1.0)


def test_cityctl_context_story_more_pause_resume_and_end(
    cityctl_module,
    tmp_path: Path,
) -> None:
    _seed_cli_state(cityctl_module, tmp_path)

    context = cityctl_module.run(_args(tmp_path, "context"), now=10.0)
    assert "position" not in context
    assert all("lat" not in stop for stop in context["itinerary"]["stops"])
    assert cityctl_module.run(_args(tmp_path, "pause"), now=10.0)["status"] == "paused"
    assert cityctl_module.run(_args(tmp_path, "resume"), now=11.0)["status"] == "active"
    story = cityctl_module.run(_args(tmp_path, "next-story"), now=12.0)
    assert story["silent"] is False
    assert cityctl_module.run(_args(tmp_path, "more"), now=13.0)["text"] == (
        "Ein belegtes Detail."
    )
    assert cityctl_module.run(_args(tmp_path, "end"), now=14.0)["status"] == "completed"


def test_cityctl_skip_capabilities_and_diagnose(
    cityctl_module,
    monkeypatch,
    tmp_path: Path,
) -> None:
    _seed_cli_state(cityctl_module, tmp_path, stop_status="planned")
    monkeypatch.setenv("HERMES_CITY_GUIDE_CHAT_ID", "configured")
    monkeypatch.setenv("OPENROUTESERVICE_API_KEY", "secret")
    monkeypatch.setattr(cityctl_module.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    skipped = cityctl_module.run(
        _args(tmp_path, "skip-stop", stop_id="stop-1"),
        now=10.0,
    )
    capabilities = cityctl_module.run(_args(tmp_path, "capabilities"), now=10.0)
    report = cityctl_module.run(_args(tmp_path, "diagnose"), now=10.0)

    assert skipped["status"] == "skipped"
    assert "route.walking" in capabilities
    assert report["ok"] is True
    assert "secret" not in json.dumps(report)


def test_cityctl_start_and_replan_dispatch(
    cityctl_module,
    city_runtime_module,
    monkeypatch,
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text("{}", encoding="utf-8")
    sample = city_runtime_module.LocationSample(
        "session-1",
        "message-1",
        10.0,
        1_000.0,
        48.1,
        11.5,
    )
    monkeypatch.setattr(
        cityctl_module,
        "latest_location",
        lambda now, state_dir=None: sample,
    )
    monkeypatch.setattr(cityctl_module, "_registry", lambda request: object())

    def planned(self, sample, request, registry, *, now):
        state = city_state.empty_state()
        state["session"]["status"] = "active"
        state["itinerary"].update(
            {
                "status": "ready",
                "revision": 1,
                "stops": [{}, {}, {}],
                "walking_seconds": 3_600,
                "dwell_seconds": 720,
            }
        )
        return state

    monkeypatch.setattr(cityctl_module.CityRuntime, "plan_and_start", planned)
    started = cityctl_module.run(
        _args(tmp_path, "start", request=request_path),
        now=10.0,
    )
    assert started["stop_count"] == 3

    _seed_cli_state(cityctl_module, tmp_path, stop_status="planned")

    def replanned(self, sample, registry, *, request_override=None, now):
        state = self.repository.load()
        state["itinerary"]["revision"] = 2
        return state

    monkeypatch.setattr(cityctl_module.CityRuntime, "replan", replanned)
    output = cityctl_module.run(_args(tmp_path, "replan"), now=11.0)
    assert output["revision"] == 2


def test_cleanup_resets_old_completed_state_and_files(
    cityctl_module,
    tmp_path: Path,
) -> None:
    runtime = _seed_cli_state(cityctl_module, tmp_path, status="completed")
    state = runtime.repository.load()
    state["session"]["ended_at"] = 1.0
    runtime.repository.save(state)
    stale = tmp_path / "city_guide_state.json.corrupt-old"
    stale.write_text("{}", encoding="utf-8")
    os.utime(stale, (1, 1))

    deleted = cityctl_module.cleanup(
        tmp_path,
        older_than_hours=24,
        now=100_000.0,
    )

    assert "completed_city_guide_state" in deleted
    assert stale.name in deleted
    assert runtime.repository.load()["session"]["status"] == "inactive"


def test_request_reader_rejects_symlink_and_large_file(
    cityctl_module,
    tmp_path: Path,
) -> None:
    original = tmp_path / "request.json"
    original.write_text("{}", encoding="utf-8")
    link = tmp_path / "request-link.json"
    link.symlink_to(original)
    with pytest.raises(ValueError):
        cityctl_module._read_json_input(link)

    large = tmp_path / "large.json"
    large.write_bytes(b"x" * (cityctl_module.MAX_INPUT_BYTES + 1))
    with pytest.raises(ValueError):
        cityctl_module._read_json_input(large)


def test_gate_snapshot_validation_and_selection(
    city_gate_module,
    tmp_path: Path,
) -> None:
    assert city_gate_module.read_snapshot(tmp_path / "missing.json") == {
        "locations": []
    }
    broken = tmp_path / "broken.json"
    broken.write_text("[]", encoding="utf-8")
    with pytest.raises(city_gate_module.SnapshotError):
        city_gate_module.read_snapshot(broken)
    assert (
        city_gate_module.select_location(
            {"locations": []},
            chat_id="42",
            now=10.0,
            max_age_seconds=300,
        )
        is None
    )
    with pytest.raises(city_gate_module.SnapshotError):
        city_gate_module.select_location(
            {
                "locations": [
                    {
                        "chat_id": "42",
                        "message_id": "old",
                        "lat": 48.1,
                        "lon": 11.5,
                        "updated_at": 1.0,
                        "expires_at": 2.0,
                    }
                ]
            },
            chat_id="42",
            now=10.0,
            max_age_seconds=5,
        )


def test_gate_main_is_silent_without_location(
    city_gate_module,
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.setattr(city_gate_module, "CHAT_ID", "42")
    monkeypatch.setattr(city_gate_module, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(city_gate_module, "SNAPSHOT", tmp_path / "missing.json")

    city_gate_module.main(now=100.0)

    output = json.loads(capsys.readouterr().out)
    assert output == {"wakeAgent": False}


def test_gate_main_reports_sanitized_configuration_error(
    city_gate_module,
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.setattr(city_gate_module, "CHAT_ID", "")
    monkeypatch.setattr(city_gate_module, "STATE", tmp_path / "state.json")

    city_gate_module.main(now=100_000.0)

    output = json.loads(capsys.readouterr().out)
    assert output["context"]["error_code"] == "missing_chat_id"
    assert "lat" not in json.dumps(output)
