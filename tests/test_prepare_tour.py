from __future__ import annotations

import json
import sys


def test_prepare_tour_with_valid_gpx(
    prepare_tour_module,
    simple_gpx,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_tour.py",
            "123",
            "--tour-name",
            "Test Tour",
            "--provider",
            "test",
            "--gpx-file",
            str(simple_gpx),
            "--state-dir",
            str(tmp_path),
            "--distance-km",
            "2",
            "--elevation",
            "100",
        ],
    )

    assert prepare_tour_module.main() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "prepared"
    assert output["schema_version"] == 3
    assert output["route"]["verified"] is True
    assert output["route"]["gpx_point_count"] == 3


def test_prepare_tour_without_gpx_stays_unverified(
    prepare_tour_module,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_tour.py",
            "123",
            "--state-dir",
            str(tmp_path),
        ],
    )

    assert prepare_tour_module.main() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "needs_gpx"
    assert output["route"]["verified"] is False
