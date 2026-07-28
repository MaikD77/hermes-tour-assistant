from __future__ import annotations


def test_canonical_parser_returns_cumulative_distance(
    route_engine_module, simple_gpx
) -> None:
    route = route_engine_module.parse_gpx(simple_gpx)

    assert len(route) == 3
    assert route[0].lat == 50.0
    assert route[0].lon == 10.0
    assert route[0].cumulative_m == 0.0
    assert route[2].cumulative_m > route[1].cumulative_m


def test_parser_rejects_invalid_coordinates(route_engine_module, tmp_path) -> None:
    invalid = tmp_path / "invalid.gpx"
    invalid.write_text(
        '<gpx><trk><trkseg><trkpt lat="999" lon="10"/>'
        '<trkpt lat="50" lon="10"/></trkseg></trk></gpx>',
        encoding="utf-8",
    )

    try:
        route_engine_module.parse_gpx(invalid)
    except ValueError as error:
        assert "latitude" in str(error)
    else:
        raise AssertionError("expected invalid GPX to be rejected")
