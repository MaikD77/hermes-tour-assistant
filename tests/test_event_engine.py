from __future__ import annotations


def test_safety_events_precede_comfort_events(event_engine_module) -> None:
    event = event_engine_module.TourEvent
    events = {}
    event_engine_module.upsert_event(
        events,
        event(
            "poi:1",
            "poi",
            "info",
            0.9,
            1.0,
            1.0,
            route_distance_ahead_m=500,
            evidence=[{"source": "osm"}],
        ),
    )
    event_engine_module.upsert_event(
        events,
        event(
            "safety:1",
            "safety",
            "critical",
            0.8,
            1.0,
            1.0,
            route_distance_ahead_m=2_000,
            evidence=[{"source": "verified-geometry"}],
        ),
    )

    selected = event_engine_module.select_for_delivery(events, 2.0)

    assert [item["event_id"] for item in selected] == ["safety:1"]


def test_cooldown_blocks_repeated_delivery(event_engine_module) -> None:
    event = event_engine_module.TourEvent
    events = {}
    event_engine_module.upsert_event(
        events,
        event("off:1", "off_route", "warning", 1.0, 1.0, 1.0),
    )
    selected = event_engine_module.select_for_delivery(events, 2.0)
    event_engine_module.mark_delivered(events, selected, 2.0, cooldown_seconds=600)

    assert event_engine_module.select_for_delivery(events, 500.0) == []
    assert event_engine_module.select_for_delivery(events, 603.0)


def test_route_checkpoints_are_not_settlements(event_engine_module) -> None:
    checkpoints = event_engine_module.route_checkpoints(10_000, 16_000)

    assert checkpoints
    assert all(item["kind"] == "route_checkpoint" for item in checkpoints)
    assert all("town" not in item for item in checkpoints)


def test_only_verified_places_create_settlement_events(event_engine_module) -> None:
    settlements = [
        {
            "id": 1,
            "name": "Verified Village",
            "verified_place": True,
            "distance_ahead_m": 2_000,
            "confidence": 0.9,
            "source": "osm",
        },
        {
            "id": 2,
            "name": "Random GPX Point",
            "verified_place": False,
            "distance_ahead_m": 1_000,
        },
    ]

    events = event_engine_module.settlement_events(settlements, now=10.0)

    assert len(events) == 1
    assert events[0].payload == {"name": "Verified Village"}


def test_external_event_without_evidence_is_not_delivered(event_engine_module) -> None:
    event = event_engine_module.TourEvent
    events = {}
    event_engine_module.upsert_event(
        events,
        event("poi:1", "poi", "info", 0.9, 1.0, 1.0),
    )

    assert event_engine_module.select_for_delivery(events, 2.0) == []


def test_event_refresh_preserves_delivery_state_and_can_resolve(
    event_engine_module,
) -> None:
    event = event_engine_module.TourEvent
    events = {}
    original = event("off:1", "off_route", "warning", 1.0, 1.0, 1.0)
    event_engine_module.upsert_event(events, original)
    event_engine_module.mark_delivered(events, [events["off:1"]], 2.0)
    refreshed = event("off:1", "off_route", "warning", 1.0, 3.0, 3.0)

    event_engine_module.upsert_event(events, refreshed)
    event_engine_module.resolve_event(events, "off:1", 4.0)

    assert events["off:1"]["first_detected_at"] == 1.0
    assert events["off:1"]["last_sent_at"] == 2.0
    assert events["off:1"]["status"] == "resolved"
    assert event_engine_module.severity_score("unknown") == 0
