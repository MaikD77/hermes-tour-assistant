from __future__ import annotations

from pathlib import Path


def test_runtime_resets_new_session_and_tracks_route(
    tour_runtime_module, event_engine_module, simple_gpx: Path, tmp_path: Path
) -> None:
    runtime = tour_runtime_module.TourRuntime(tmp_path / "state.json")
    runtime.begin_session("share-a", started_at=1.0, expires_at=100.0)
    runtime.attach_route(provider="komoot", route_id=123, name="Route A", gpx_path=simple_gpx)
    match = runtime.update_position(50.0, 10.0, observed_at=2.0)
    runtime.record_event(
        event_engine_module.TourEvent(
            event_id="poi:1",
            event_type="poi",
            severity="info",
            confidence=0.9,
            first_detected_at=2.0,
            last_detected_at=2.0,
            evidence=[{"source": "test"}],
        )
    )

    assert match.progress_m == 0.0
    assert runtime.next_notifications(now=3.0)[0]["event_id"] == "poi:1"

    state = runtime.begin_session("share-b", started_at=4.0, expires_at=200.0)

    assert state["session"]["id"] == "share-b"
    assert state["route"]["verified"] is False
    assert state["events"] == {}
    assert state["position"] is None


def test_runtime_rejects_position_without_verified_route(
    tour_runtime_module, tmp_path: Path
) -> None:
    runtime = tour_runtime_module.TourRuntime(tmp_path / "state.json")
    runtime.begin_session("share-a", started_at=1.0, expires_at=100.0)

    try:
        runtime.update_position(50.0, 10.0, observed_at=2.0)
    except RuntimeError as error:
        assert "no verified route" in str(error)
    else:
        raise AssertionError("expected route validation failure")


def test_prepared_route_is_preserved_for_next_session(
    tour_runtime_module, simple_gpx: Path, tmp_path: Path
) -> None:
    runtime = tour_runtime_module.TourRuntime(tmp_path / "state.json")
    runtime.prepare_route(
        provider="test",
        route_id="route-a",
        name="Route A",
        gpx_path=simple_gpx,
    )

    state = runtime.begin_session("share-a", started_at=1.0, expires_at=100.0)

    assert state["route"]["verified"] is True
    assert state["route"]["prepared"] is False
    assert state["session"]["status"] == "active"


def test_activity_profiles_have_distinct_validated_thresholds(
    tour_runtime_module,
) -> None:
    cycling = tour_runtime_module.TourProfile("cycling")
    walking = tour_runtime_module.TourProfile("walking")
    custom = tour_runtime_module.TourProfile(
        "walking",
        move_threshold_m=75,
        off_route_enter_m=80,
        off_route_exit_m=40,
        settlement_approach_m=900,
        finish_approach_m=1_000,
    )

    assert walking.move_threshold_m < cycling.move_threshold_m
    assert walking.finish_approach_m < cycling.finish_approach_m
    assert custom.cadence_minutes(2.0, 900, route_verified=True) == 2

    try:
        tour_runtime_module.TourProfile(
            "cycling",
            off_route_enter_m=50,
            off_route_exit_m=50,
        )
    except ValueError as error:
        assert "exit threshold" in str(error)
    else:
        raise AssertionError("expected invalid off-route hysteresis")
