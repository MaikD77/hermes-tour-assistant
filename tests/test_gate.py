from __future__ import annotations

import json
from pathlib import Path


def configure_paths(gate_module, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gate_module, "SNAPSHOT", tmp_path / "telegram_live_locations.json")
    monkeypatch.setattr(gate_module, "STATE", tmp_path / "live_tour_assistant.json")
    monkeypatch.setattr(gate_module, "GATE_STATE", tmp_path / "live_tour_gate.json")
    monkeypatch.setattr(gate_module, "CHAT_ID", "12345")


def write_snapshot(gate_module, *, updated_at: float, expires_at: float, lat: float = 50.0) -> None:
    snapshot = {
        "locations": [
            {
                "chat_id": "12345",
                "message_id": "67890",
                "lat": lat,
                "lon": 10.0,
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


def test_new_live_share_wakes_agent(gate_module, monkeypatch, tmp_path, capsys) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    write_snapshot(gate_module, updated_at=999_999.0, expires_at=1_003_600.0)

    gate_module.main(now=1_000_000.0)

    output = read_output(capsys)
    assert output["wakeAgent"] is True
    assert output["context"]["reason"] == "live_location_started"
    assert "no_route" in output["context"]["flags"]


def test_skip_preserves_last_wake(gate_module, monkeypatch, tmp_path, capsys) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    write_snapshot(gate_module, updated_at=1_000_050.0, expires_at=1_003_600.0)
    gate_module.save_json(
        gate_module.GATE_STATE,
        {
            "active": True,
            "message_id": "67890",
            "last_wake_lat": 50.0,
            "last_wake_lon": 10.0,
            "last_wake_at": 1_000_000.0,
            "next_due_at": 1_000_300.0,
            "last_position_lat": 50.0,
            "last_position_lon": 10.0,
            "last_position_time": 1_000_000.0,
        },
    )

    gate_module.main(now=1_000_050.0)

    assert read_output(capsys) == {"wakeAgent": False}
    state = gate_module.load_json(gate_module.GATE_STATE, {})
    assert state["last_wake_at"] == 1_000_000.0
    assert state["last_wake_lat"] == 50.0
    assert state["next_due_at"] == 1_000_900.0


def test_no_wake_before_next_due_at(gate_module, monkeypatch, tmp_path, capsys) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    write_snapshot(gate_module, updated_at=1_000_100.0, expires_at=1_003_600.0)
    gate_module.save_json(
        gate_module.GATE_STATE,
        {
            "active": True,
            "message_id": "67890",
            "last_wake_lat": 50.0,
            "last_wake_lon": 10.0,
            "last_wake_at": 1_000_000.0,
            "next_due_at": 1_000_300.0,
            "last_position_lat": 50.0,
            "last_position_lon": 10.0,
            "last_position_time": 1_000_000.0,
        },
    )

    gate_module.main(now=1_000_100.0)

    assert read_output(capsys) == {"wakeAgent": False}


def test_wakes_when_due(gate_module, monkeypatch, tmp_path, capsys) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    write_snapshot(gate_module, updated_at=1_000_300.0, expires_at=1_003_600.0)
    gate_module.save_json(
        gate_module.GATE_STATE,
        {
            "active": True,
            "message_id": "67890",
            "last_wake_lat": 50.0,
            "last_wake_lon": 10.0,
            "last_wake_at": 1_000_000.0,
            "next_due_at": 1_000_300.0,
            "last_position_lat": 50.0,
            "last_position_lon": 10.0,
            "last_position_time": 1_000_000.0,
        },
    )

    gate_module.main(now=1_000_300.0)

    output = read_output(capsys)
    assert output["wakeAgent"] is True
    assert output["context"]["reason"] == "check_in"


def test_cadence_for_fast_and_finish_approach(gate_module) -> None:
    assert gate_module.cadence_for(25.0, 20.0) == 3
    assert gate_module.cadence_for(12.0, 4.9) == 2
    assert gate_module.cadence_for(2.0, 20.0) == 15


def test_off_route_hysteresis(gate_module) -> None:
    state = {"off_route_active": True}
    track = [(50.0, 10.0, 0.0), (50.01, 10.0, 1.1)]

    still_off = gate_module.compute_dynamic_params(50.0008, 10.0, 1_000_000.0, state, track, [])
    resolved = gate_module.compute_dynamic_params(50.0005, 10.0, 1_000_000.0, state, track, [])

    assert still_off["off_route"] is True
    assert resolved["off_route"] is False
