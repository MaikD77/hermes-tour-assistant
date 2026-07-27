from __future__ import annotations

import json
import stat


def test_save_json_writes_complete_private_file(gate_module, tmp_path) -> None:
    target = tmp_path / "state" / "gate.json"
    payload = {"active": True, "cadence_minutes": 5}

    gate_module.save_json(target, payload)

    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not target.with_suffix(".tmp").exists()
