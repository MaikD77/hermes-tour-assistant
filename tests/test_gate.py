from __future__ import annotations

import json


def configure_paths(gate_module, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(gate_module, "SNAPSHOT", tmp_path / "telegram_live_locations.json")
    monkeypatch.setattr(gate_module, "STATE", tmp_path / "live_tour_assistant.json")
    monkeypatch.setattr(gate_module, "GATE_STATE", tmp_path / "live_tour_gate.json")
    monkeypatch.setattr(gate_module, "CHAT_ID", "12345")
    monkeypatch.setattr(gate_module.time, "time", lambda: 1_000_000.0)


def test_no_active_live_share_does_not_wake_agent(
    gate_module, monkeypatch, tmp_path, capsys
) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    gate_module.SNAPSHOT.write_text('{"locations": []}\n', encoding="utf-8")

    gate_module.main()

    output = json.loads(capsys.readouterr().out)
    assert output == {"wakeAgent": False}


def test_new_live_share_wakes_agent(
    gate_module, monkeypatch, tmp_path, capsys
) -> None:
    configure_paths(gate_module, monkeypatch, tmp_path)
    snapshot = {
        "locations": [
            {
                "chat_id": "12345",
                "message_id": "67890",
                "lat": 50.0,
                "lon": 10.0,
                "updated_at": 999_999.0,
                "expires_at": 1_003_600.0,
            }
        ]
    }
    gate_module.SNAPSHOT.write_text(json.dumps(snapshot), encoding="utf-8")

    gate_module.main()

    output = json.loads(capsys.readouterr().out)
    assert output["wakeAgent"] is True
    assert output["context"]["reason"] == "live_location_started"
    assert "no_route" in output["context"]["flags"]
