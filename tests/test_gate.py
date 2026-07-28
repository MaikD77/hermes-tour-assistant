from __future__ import annotations

import json
from pathlib import Path


def configure_paths(gate_module, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gate_module, "SNAPSHOT", tmp_path / "telegram_live_locations.json")
    monkeypatch.setattr(gate_module, "STATE", tmp_path / "live_tour_assistant.json")
    monkeypatch.setattr(gate_module, "CHAT_ID", "12345")
    monkeypatch.setattr(gate_module, "MAX_LOCATION_AGE_SECONDS", 300.0)


def set_non_lunch_local_time(gate_module, monkeypatch) -> None:
    runtime_time = gate_module.TourRuntime.evaluate_gate.__globals__["time"]
    six_am = runtime_time.struct_time((2024, 1, 1, 6, 0, 0, 0, 1, -1))
    monkeypatch.setattr(runtime_time, "localtime", lambda _: six_am)


def write_snapshot(
    gate_module,
    *,
    updated_at: float,
    expires_at: float,
    lat: float = 50.0,
    lon: float = 10.0,
    message_id: str = "67890",
) -> None:
    snapshot = {
        "locations": [
            {
                "chat_id": "12345",
                "message_id": message_id,
                "lat": lat,
                "lon": lon,
                "updated_at": updated_at,
                "expires_at": expires_at,
            }
        ]
    }
    gate_module.SNAPSHOT.write_text(json.dumps(snapshot), encoding="utf-8")


def read_output(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_no_active_live_share_does_not_wake_agent(
    gate_module, monkeypatch, tmp_path, capsys
) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    gate_module.SNAPSHOT.write_text('{"locations": []}\n', encoding="utf-8")

    gate_module.main(now=1_000_000.0)

    assert read_output(capsys) == {"wakeAgent": False}


def test_new_live_share_wakes_without_leaking_coordinates(
    gate_module, monkeypatch, tmp_path, capsys
) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    write_snapshot(gate_module, updated_at=999_999.0, expires_at=1_003_600.0)

    gate_module.main(now=1_000_000.0)

    raw = capsys.readouterr().out
    output = json.loads(raw)
    assert output["wakeAgent"] is True
    assert output["context"]["reason"] == "live_location_started"
    assert "no_route" in output["context"]["flags"]
    assert "50.0" not in raw
    assert "10.0" not in raw


def test_skip_preserves_last_wake(
    gate_module, monkeypatch, tmp_path, capsys
) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    set_non_lunch_local_time(gate_module, monkeypatch)
    write_snapshot(gate_module, updated_at=999_999.0, expires_at=1_003_600.0)
    gate_module.main(now=1_000_000.0)
    read_output(capsys)
    first = gate_module._runtime().repository.load()

    write_snapshot(gate_module, updated_at=1_000_049.0, expires_at=1_003_600.0)
    gate_module.main(now=1_000_050.0)

    assert read_output(capsys) == {"wakeAgent": False}
    second = gate_module._runtime().repository.load()
    assert second["schedule"]["last_wake_at"] == first["schedule"]["last_wake_at"]
    assert second["schedule"]["last_wake_position"] == first["schedule"]["last_wake_position"]


def test_wakes_when_due(gate_module, monkeypatch, tmp_path, capsys) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    set_non_lunch_local_time(gate_module, monkeypatch)
    write_snapshot(gate_module, updated_at=999_999.0, expires_at=1_003_600.0)
    gate_module.main(now=1_000_000.0)
    read_output(capsys)

    write_snapshot(gate_module, updated_at=1_000_299.0, expires_at=1_003_600.0)
    gate_module.main(now=1_000_300.0)

    output = read_output(capsys)
    assert output["wakeAgent"] is True
    assert output["context"]["reason"] == "check_in"


def test_stale_location_reports_once(
    gate_module, monkeypatch, tmp_path, capsys
) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    write_snapshot(gate_module, updated_at=900_000.0, expires_at=1_003_600.0)

    gate_module.main(now=1_000_000.0)
    first = read_output(capsys)
    gate_module.main(now=1_000_060.0)
    second = read_output(capsys)

    assert first["context"]["error_code"] == "invalid_or_stale_location"
    assert second == {"wakeAgent": False}


def test_missing_chat_id_reports_once(
    gate_module, monkeypatch, tmp_path, capsys
) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    monkeypatch.setattr(gate_module, "CHAT_ID", "")

    gate_module.main(now=1_000_000.0)
    first = read_output(capsys)
    gate_module.main(now=1_000_060.0)
    second = read_output(capsys)

    assert first["context"]["error_code"] == "missing_chat_id"
    assert second == {"wakeAgent": False}


def test_invalid_profile_configuration_reports_operational_error(
    gate_module, monkeypatch, tmp_path, capsys
) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    monkeypatch.setenv("HERMES_TOUR_OFF_ROUTE_ENTER_M", "50")
    monkeypatch.setenv("HERMES_TOUR_OFF_ROUTE_EXIT_M", "100")

    gate_module.main(now=1_000_000.0)

    output = read_output(capsys)
    assert output["context"]["error_code"] == "invalid_tour_profile"


def test_prepared_route_uses_segment_matching(
    gate_module,
    tour_runtime_module,
    simple_gpx,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    monkeypatch.setitem(
        gate_module.TourRuntime.evaluate_gate.__globals__,
        "FINISH_APPROACH_M",
        100.0,
    )
    runtime = tour_runtime_module.TourRuntime(gate_module.STATE)
    runtime.prepare_route(
        provider="test",
        route_id="route-a",
        name="Route A",
        gpx_path=simple_gpx,
    )
    write_snapshot(
        gate_module,
        updated_at=999_999.0,
        expires_at=1_003_600.0,
        lon=10.005,
    )

    gate_module.main(now=1_000_000.0)

    output = read_output(capsys)
    state = runtime.repository.load()
    assert output["context"]["reason"] == "live_location_started"
    assert "no_route" not in output["context"]["flags"]
    assert 300 < state["position"]["progress_m"] < 400


def test_only_verified_settlement_triggers_town_approach(
    gate_module,
    tour_runtime_module,
    simple_gpx,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    set_non_lunch_local_time(gate_module, monkeypatch)
    monkeypatch.setitem(
        gate_module.TourRuntime.evaluate_gate.__globals__,
        "FINISH_APPROACH_M",
        100.0,
    )
    runtime = tour_runtime_module.TourRuntime(gate_module.STATE)
    runtime.prepare_route(
        provider="test",
        route_id="route-a",
        name="Route A",
        gpx_path=simple_gpx,
    )
    runtime.set_verified_settlements(
        [
            {
                "id": "place-1",
                "name": "Verified Village",
                "source": "osm",
                "route_progress_m": 1_000,
                "confidence": 0.9,
                "verified_place": True,
            },
            {
                "id": "checkpoint",
                "name": "~5 km",
                "source": "route",
                "route_progress_m": 500,
                "confidence": 1.0,
                "verified_place": False,
            },
        ]
    )
    write_snapshot(gate_module, updated_at=999_999.0, expires_at=1_003_600.0)
    gate_module.main(now=1_000_000.0)
    read_output(capsys)

    write_snapshot(gate_module, updated_at=1_000_059.0, expires_at=1_003_600.0)
    gate_module.main(now=1_000_060.0)

    output = read_output(capsys)
    assert output["context"]["reason"] == "town_approach"
    assert output["context"]["flags"] == ["town_approach"]


def test_corrupt_state_is_quarantined_and_reported_once(
    gate_module, monkeypatch, tmp_path, capsys
) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    gate_module.STATE.write_text("{not-json", encoding="utf-8")
    write_snapshot(gate_module, updated_at=999_999.0, expires_at=1_003_600.0)

    gate_module.main(now=1_000_000.0)

    output = read_output(capsys)
    assert output["context"]["error_code"] == "corrupt_state_recovered"
    assert list(tmp_path.glob("live_tour_assistant.json.corrupt-*"))
