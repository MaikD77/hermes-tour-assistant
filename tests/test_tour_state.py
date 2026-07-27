from __future__ import annotations

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
    state = tour_state_module.set_route_match(state, "matched", route_id=123)

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
