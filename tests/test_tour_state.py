from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_new_session_resets_route_events_and_weather(tour_state_module) -> None:
    previous = tour_state_module.empty_state()
    previous = tour_state_module.start_session(previous, "share-a", started_at=1.0)
    previous = tour_state_module.set_route_match(
        previous,
        "matched",
        provider="komoot",
        route_id=123,
        name="Tour A",
        gpx_path="/tmp/a.gpx",
    )
    previous["events"] = {"event": {"status": "reported"}}
    previous["weather"] = {"temperature_c": 20}

    current = tour_state_module.start_session(previous, "share-b", started_at=2.0)

    assert current["session"]["id"] == "share-b"
    assert current["route"]["match_status"] == "unknown"
    assert current["route"]["verified"] is False
    assert current["events"] == {}
    assert current["weather"] is None


def test_unmatched_route_is_never_verified(tour_state_module) -> None:
    state = tour_state_module.start_session(
        tour_state_module.empty_state(), "share-a", started_at=1.0
    )

    state = tour_state_module.set_route_match(state, "unmatched")

    assert state["route"]["match_status"] == "unmatched"
    assert state["route"]["verified"] is False


def test_end_session_removes_route_state(tour_state_module) -> None:
    state = tour_state_module.start_session(
        tour_state_module.empty_state(), "share-a", started_at=1.0
    )
    state = tour_state_module.set_route_match(
        state,
        "matched",
        route_id=123,
        gpx_path="/tmp/route.gpx",
    )

    state = tour_state_module.end_session(state, ended_at=3.0)

    assert state["session"]["status"] == "inactive"
    assert state["route"]["id"] is None
    assert state["route"]["verified"] is False


def test_repository_writes_atomically_with_private_permissions(
    tour_state_module, tmp_path: Path
) -> None:
    repository = tour_state_module.StateRepository(tmp_path / "state.json")
    state = tour_state_module.empty_state()

    repository.save(state)

    assert repository.load() == state
    assert (repository.path.stat().st_mode & 0o777) == 0o600
    assert (repository.path.parent.stat().st_mode & 0o777) == 0o700
    assert (repository.lock_path.stat().st_mode & 0o777) == 0o600


def test_repository_serializes_updates_between_processes(
    tour_state_module, tmp_path: Path
) -> None:
    repository = tour_state_module.StateRepository(tmp_path / "state.json")
    repository.save(tour_state_module.empty_state())
    runtime_dir = Path(__file__).parents[1] / "skills" / "outdoor-tour-assistant" / "scripts"
    worker = """
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from tour_state import StateRepository

repository = StateRepository(Path(sys.argv[2]))

def increment(state):
    item = state["provider_health"].setdefault("concurrency-test", {})
    item["count"] = int(item.get("count", 0)) + 1
    return state

for _ in range(25):
    repository.update(increment)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", worker, str(runtime_dir), str(repository.path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]

    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stdout + stderr

    state = repository.load()
    assert state["provider_health"]["concurrency-test"]["count"] == 50


def test_legacy_v1_migrates_to_v3(tour_state_module) -> None:
    legacy = {
        "tour": {
            "id": 123,
            "name": "Legacy",
            "verified": True,
            "gpx": "/tmp/legacy.gpx",
        },
        "reported_facts": ["weather:rain"],
    }

    migrated = tour_state_module.migrate_state(legacy)

    assert migrated["schema_version"] == 3
    assert migrated["route"]["verified"] is True
    assert migrated["route"]["prepared"] is True
    assert migrated["schedule"]["cadence_minutes"] == 5
    assert migrated["events"]["weather:rain"]["status"] == "resolved"


def test_v2_migration_is_idempotent(tour_state_module) -> None:
    v2 = {
        "schema_version": 2,
        "session": {
            "id": "share-a",
            "status": "active",
            "started_at": 1.0,
            "expires_at": 100.0,
            "ended_at": None,
        },
        "route": {
            "match_status": "matched",
            "provider": "test",
            "id": "route-a",
            "name": "Route A",
            "gpx_path": "/tmp/a.gpx",
            "verified": True,
        },
        "position": None,
        "events": {},
        "weather": None,
        "provider_health": {},
    }

    migrated = tour_state_module.migrate_state(v2)

    assert tour_state_module.migrate_state(migrated) == migrated


def test_corrupt_state_is_quarantined(tour_state_module, tmp_path) -> None:
    repository = tour_state_module.StateRepository(tmp_path / "state.json")
    repository.path.write_text("{broken", encoding="utf-8")

    try:
        repository.load()
    except tour_state_module.CorruptStateError as error:
        assert error.quarantine_path.exists()
        assert not repository.path.exists()
    else:
        raise AssertionError("expected corrupt state to be quarantined")
