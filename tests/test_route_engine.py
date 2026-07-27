from __future__ import annotations

from pathlib import Path


def test_parser_accepts_namespace_and_attribute_order(route_engine_module, repository_root: Path) -> None:
    route = route_engine_module.parse_gpx(
        repository_root / "tests" / "fixtures" / "attribute-order-route.gpx"
    )

    assert len(route) == 3
    assert route[-1].cumulative_m > 1_000


def test_match_projects_to_segment_not_only_trackpoint(route_engine_module) -> None:
    point = route_engine_module.RoutePoint
    route = [point(50.0, 10.0, 0.0), point(50.0, 10.02, 1_429.0)]

    match = route_engine_module.match_position(50.0001, 10.01, route)

    assert match.segment_index == 0
    assert 0.45 < match.fraction < 0.55
    assert match.offset_m < 20
    assert 650 < match.progress_m < 780


def test_direction_uses_previous_progress(route_engine_module) -> None:
    point = route_engine_module.RoutePoint
    route = [
        point(50.0, 10.0, 0.0),
        point(50.0, 10.01, 714.0),
        point(50.0, 10.02, 1_428.0),
    ]

    forward = route_engine_module.match_position(
        50.0, 10.015, route, previous_segment_index=0, previous_progress_m=500
    )
    reverse = route_engine_module.match_position(
        50.0, 10.005, route, previous_segment_index=1, previous_progress_m=1_000
    )

    assert forward.direction == "forward"
    assert reverse.direction == "reverse"


def test_search_window_prefers_previous_route_area(route_engine_module) -> None:
    point = route_engine_module.RoutePoint
    route = [
        point(50.0, 10.0, 0.0),
        point(50.0, 10.01, 714.0),
        point(50.0001, 10.01, 725.0),
        point(50.0001, 10.0, 1_439.0),
    ]

    match = route_engine_module.match_position(
        50.00008,
        10.005,
        route,
        previous_segment_index=2,
        previous_progress_m=1_000,
        search_window=1,
    )

    assert match.segment_index == 2
    assert match.direction == "forward"
