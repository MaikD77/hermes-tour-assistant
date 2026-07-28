from __future__ import annotations

import json
import os
import sys
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


def test_cleanup_only_removes_expired_runtime_files(
    tourctl_module, tour_runtime_module, tmp_path: Path
) -> None:
    old_gpx = tmp_path / "current-tour-1.gpx"
    new_gpx = tmp_path / "current-tour-2.gpx"
    state = tmp_path / "live_tour_assistant.json"
    for path in (old_gpx, new_gpx):
        path.write_text("data", encoding="utf-8")
    tour_runtime_module.TourRuntime(state).repository.save(
        tour_runtime_module.empty_state()
    )
    os.utime(old_gpx, (1_000.0, 1_000.0))
    os.utime(new_gpx, (9_900.0, 9_900.0))
    os.utime(state, (1_000.0, 1_000.0))

    deleted = tourctl_module.cleanup(tmp_path, older_than_hours=1, now=10_000.0)

    assert deleted == [str(old_gpx)]
    assert new_gpx.exists()
    assert state.exists()


def test_cleanup_preserves_active_route(
    tourctl_module, tour_runtime_module, simple_gpx, tmp_path: Path
) -> None:
    runtime = tour_runtime_module.TourRuntime(
        tmp_path / "live_tour_assistant.json"
    )
    runtime.begin_session("share-a", started_at=1.0, expires_at=20_000.0)
    state = runtime.attach_route(
        provider="test",
        route_id="active",
        name="Active",
        gpx_path=simple_gpx,
    )
    active_gpx = Path(state["route"]["gpx_path"])
    os.utime(active_gpx, (1_000.0, 1_000.0))

    deleted = tourctl_module.cleanup(
        tmp_path,
        older_than_hours=1,
        now=10_000.0,
    )

    assert str(active_gpx) not in deleted
    assert active_gpx.exists()


def test_harden_permissions_secures_directory_and_files(
    tourctl_module, tmp_path: Path
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o755)
    state = state_dir / "state.json"
    state.write_text("{}", encoding="utf-8")
    os.chmod(state, 0o644)

    tourctl_module.harden_permissions(state_dir)

    assert state_dir.stat().st_mode & 0o777 == 0o700
    assert state.stat().st_mode & 0o777 == 0o600


def _run_cli(tourctl_module, monkeypatch, capsys, *arguments: str):
    monkeypatch.setattr(sys, "argv", ["tourctl.py", *arguments])
    return_code = tourctl_module.main()
    return return_code, json.loads(capsys.readouterr().out)


def test_cli_diagnostics_hardening_and_capabilities(
    tourctl_module, monkeypatch, tmp_path: Path, capsys
) -> None:
    state_dir = str(tmp_path)

    code, hardened = _run_cli(
        tourctl_module,
        monkeypatch,
        capsys,
        "--state-dir",
        state_dir,
        "harden-permissions",
    )
    assert code == 0
    assert hardened["changed"] == []

    code, report = _run_cli(
        tourctl_module,
        monkeypatch,
        capsys,
        "--state-dir",
        state_dir,
        "diagnose",
    )
    assert code == 0
    assert report["state_dir_private"] is True

    code, capabilities = _run_cli(
        tourctl_module,
        monkeypatch,
        capsys,
        "--state-dir",
        state_dir,
        "capabilities",
    )
    assert code == 0
    assert capabilities == {
        "map.corridor": ["openstreetmap"],
        "map.reverse": ["openstreetmap"],
        "water.search": ["openstreetmap"],
        "weather.current": ["open-meteo"],
    }


def test_cli_route_event_and_context_flow(
    tourctl_module,
    event_engine_module,
    simple_gpx,
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    runtime = tourctl_module._runtime(tmp_path)
    runtime.begin_session("share-a", started_at=1.0, expires_at=100.0)
    state_dir = str(tmp_path)

    code, attached = _run_cli(
        tourctl_module,
        monkeypatch,
        capsys,
        "--state-dir",
        state_dir,
        "attach-route",
        "--provider",
        "test",
        "--route-id",
        "route-a",
        "--name",
        "Route A",
        "--gpx-path",
        str(simple_gpx),
    )
    assert code == 0
    assert attached["route"]["verified"] is True

    runtime.update_position(50.0, 10.0, observed_at=2.0)
    code, sanitized = _run_cli(
        tourctl_module,
        monkeypatch,
        capsys,
        "--state-dir",
        state_dir,
        "context",
    )
    assert code == 0
    assert "lat" not in sanitized["position"]

    code, precise = _run_cli(
        tourctl_module,
        monkeypatch,
        capsys,
        "--state-dir",
        state_dir,
        "context",
        "--include-location",
    )
    assert code == 0
    assert precise["position"]["lat"] == 50.0

    settlements = tmp_path / "settlements.json"
    settlements.write_text(
        json.dumps(
            [
                {
                    "id": "place-1",
                    "name": "Village",
                    "source": "osm",
                    "route_progress_m": 1_000,
                    "confidence": 0.9,
                    "verified_place": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    code, settlement_state = _run_cli(
        tourctl_module,
        monkeypatch,
        capsys,
        "--state-dir",
        state_dir,
        "set-settlements",
        "--input",
        str(settlements),
    )
    assert code == 0
    assert settlement_state["route"]["settlements"][0]["name"] == "Village"

    event_input = tmp_path / "event.json"
    event_input.write_text(
        json.dumps(
            {
                "event_id": "poi:1",
                "event_type": "poi",
                "severity": "info",
                "confidence": 0.9,
                "first_detected_at": 2.0,
                "last_detected_at": 2.0,
                "evidence": [{"source": "osm"}],
            }
        ),
        encoding="utf-8",
    )
    code, recorded = _run_cli(
        tourctl_module,
        monkeypatch,
        capsys,
        "--state-dir",
        state_dir,
        "record-event",
        "--input",
        str(event_input),
    )
    assert code == 0
    assert recorded["event_id"] == "poi:1"

    code, alert = _run_cli(
        tourctl_module,
        monkeypatch,
        capsys,
        "--state-dir",
        state_dir,
        "next-alert",
        "--now",
        "3",
    )
    assert code == 0
    assert alert["silent"] is False
    assert alert["events"][0]["event_id"] == "poi:1"

    code, status = _run_cli(
        tourctl_module,
        monkeypatch,
        capsys,
        "--state-dir",
        state_dir,
        "route-status",
        "unmatched",
        "--provider",
        "test",
    )
    assert code == 0
    assert status["route"]["verified"] is False


def test_cli_weather_current_uses_structured_provider(
    tourctl_module,
    providers_module,
    simple_gpx,
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    runtime = tourctl_module._runtime(tmp_path)
    runtime.begin_session("share-a", started_at=1.0, expires_at=100.0)
    runtime.attach_route(
        provider="test",
        route_id="route-a",
        name="Route A",
        gpx_path=simple_gpx,
    )
    runtime.update_position(50.0, 10.0, observed_at=2.0)
    snapshot = providers_module.WeatherSnapshot(
        observed_at=3.0,
        valid_until=903.0,
        temperature_c=18.0,
        precipitation_rate_mm_h=0.0,
        wind_kmh=10.0,
        gust_kmh=20.0,
        source="test",
        confidence=0.9,
    )
    registry = tourctl_module.ProviderRegistry()
    provider = type(
        "WeatherProvider",
        (),
        {"current_conditions": lambda *_args, **_kwargs: snapshot},
    )()
    registry.register("weather.current", "test", provider)
    monkeypatch.setattr(
        tourctl_module,
        "build_default_registry",
        lambda **_kwargs: registry,
    )

    code, output = _run_cli(
        tourctl_module,
        monkeypatch,
        capsys,
        "--state-dir",
        str(tmp_path),
        "weather-current",
        "--now",
        "3",
    )

    assert code == 0
    assert output["weather"]["temperature_c"] == 18.0
