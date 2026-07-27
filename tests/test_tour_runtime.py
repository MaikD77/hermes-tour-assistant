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
