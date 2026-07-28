from __future__ import annotations


def test_session_route_event_and_reset_pipeline(
    contracts_module,
    event_engine_module,
    tour_runtime_module,
    simple_gpx,
    tmp_path,
) -> None:
    runtime = tour_runtime_module.TourRuntime(tmp_path / "state.json")
    runtime.prepare_route(
        provider="test",
        route_id="route-a",
        name="Route A",
        gpx_path=simple_gpx,
    )
    sample_a = contracts_module.LocationSample(
        session_id="share-a",
        message_id="1",
        observed_at=1.0,
        expires_at=100.0,
        lat=50.0,
        lon=10.0,
    )

    decision = runtime.evaluate_gate(sample_a, now=2.0)
    runtime.record_event(
        event_engine_module.TourEvent(
            event_id="off-route:1",
            event_type="off_route",
            severity="warning",
            confidence=1.0,
            first_detected_at=2.0,
            last_detected_at=2.0,
            payload={"offset_m": 180},
        )
    )
    alert = runtime.next_alert(now=3.0)

    assert decision.reason == "live_location_started"
    assert alert.silent is False
    assert alert.events[0]["event_id"] == "off-route:1"

    sample_b = contracts_module.LocationSample(
        session_id="share-b",
        message_id="2",
        observed_at=4.0,
        expires_at=200.0,
        lat=50.0,
        lon=10.0,
    )
    runtime.evaluate_gate(sample_b, now=5.0)
    state = runtime.repository.load()

    assert state["session"]["id"] == "share-b"
    assert state["route"]["verified"] is False
    assert state["events"] == {}
    assert state["weather"] is None
