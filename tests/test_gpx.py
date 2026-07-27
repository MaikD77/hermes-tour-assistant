from __future__ import annotations


def test_parse_gpx_returns_track_with_cumulative_distance(gate_module, simple_gpx) -> None:
    track = gate_module.parse_gpx(simple_gpx)

    assert len(track) == 3
    assert track[0] == (50.0, 10.0, 0.0)
    assert track[1][2] > 0
    assert track[2][2] > track[1][2]
