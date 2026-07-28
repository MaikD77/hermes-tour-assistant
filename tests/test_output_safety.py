from __future__ import annotations

import urllib.parse


def test_provider_label_is_markdown_escaped(output_safety_module) -> None:
    label = output_safety_module.safe_label("[Click](javascript:alert(1))\nCafe")

    assert label == r"\[Click\]\(javascript:alert\(1\)\) Cafe"


def test_navigation_url_is_generated_from_validated_coordinates(
    output_safety_module,
) -> None:
    url = output_safety_module.navigation_url(50.0, 10.0, 50.1, 10.2)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    assert query["origin"] == ["50.000000,10.000000"]
    assert query["destination"] == ["50.100000,10.200000"]
    assert query["travelmode"] == ["bicycling"]


def test_navigation_url_rejects_invalid_coordinate(output_safety_module) -> None:
    try:
        output_safety_module.navigation_url(500.0, 10.0, 50.1, 10.2)
    except ValueError:
        pass
    else:
        raise AssertionError("expected invalid coordinate rejection")
