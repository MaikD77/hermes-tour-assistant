from __future__ import annotations

import json
import urllib.error
import urllib.parse

import location_core.providers as core_providers
import pytest


class FakeResponse:
    def __init__(self, payload: object | bytes):
        self.raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size: int):
        return self.raw


def test_openrouteservice_adapter_normalizes_geojson(monkeypatch) -> None:
    captured = {}
    payload = {
        "features": [
            {
                "geometry": {
                    "coordinates": [[11.5, 48.1], [11.51, 48.11]],
                },
                "properties": {
                    "summary": {"distance": 1_234.0, "duration": 900.0}
                },
            }
        ]
    }

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(payload)

    monkeypatch.setattr(core_providers.urllib.request, "urlopen", urlopen)
    provider = core_providers.OpenRouteServiceProvider("secret", timeout_seconds=3)
    route = provider.route_through([(48.1, 11.5), (48.11, 11.51)])

    assert route.points == ((48.1, 11.5), (48.11, 11.51))
    assert route.duration_seconds == 900.0
    assert captured["request"].get_header("Authorization") == "secret"
    body = json.loads(captured["request"].data)
    assert body["coordinates"][0] == [11.5, 48.1]
    assert "quiet" in body["options"]["profile_params"]["weightings"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"features": ["invalid"]},
        {"features": [{"geometry": {}, "properties": {}}]},
        {
            "features": [
                {
                    "geometry": {"coordinates": [["invalid"]]},
                    "properties": {"summary": {"distance": 1, "duration": 1}},
                }
            ]
        },
    ],
)
def test_openrouteservice_rejects_invalid_payload(monkeypatch, payload) -> None:
    monkeypatch.setattr(
        core_providers.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(payload),
    )
    provider = core_providers.OpenRouteServiceProvider("secret")

    with pytest.raises((ValueError, TypeError)):
        provider.route_through([(48.1, 11.5), (48.11, 11.51)])


def test_openrouteservice_validates_auth_and_coordinates() -> None:
    with pytest.raises(core_providers.ProviderCallError):
        core_providers.OpenRouteServiceProvider("")
    provider = core_providers.OpenRouteServiceProvider("secret")
    with pytest.raises(ValueError):
        provider.route_through([(48.1, 11.5)])
    with pytest.raises(ValueError):
        provider.route_through([(100.0, 11.5), (48.1, 11.5)])


def test_wikipedia_geosearch_normalizes_and_sorts(monkeypatch) -> None:
    payload = {
        "query": {
            "pages": {
                "2": {
                    "pageid": 2,
                    "title": "Weiter weg",
                    "extract": "Ein belegter Artikel.",
                    "fullurl": "https://de.wikipedia.org/wiki/Weiter",
                    "coordinates": [{"lat": 48.2, "lon": 11.6}],
                    "pageprops": {},
                },
                "1": {
                    "pageid": 1,
                    "title": "Nah",
                    "extract": "Ein anderer belegter Artikel.",
                    "fullurl": "https://de.wikipedia.org/wiki/Nah",
                    "coordinates": [{"lat": 48.101, "lon": 11.501}],
                    "pageprops": {"wikibase_item": "Q1"},
                },
                "broken": {"pageid": 3},
            }
        }
    }
    captured = {}

    def urlopen(request, timeout):
        captured["url"] = request.full_url
        return FakeResponse(payload)

    monkeypatch.setattr(core_providers.urllib.request, "urlopen", urlopen)
    provider = core_providers.WikimediaKnowledgeProvider("de")
    articles = provider.nearby(48.1, 11.5, radius_m=1_500, limit=10)

    assert [article.title for article in articles] == ["Nah", "Weiter weg"]
    assert articles[0].wikidata_id == "Q1"
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(captured["url"]).query)
    assert query["ggscoord"] == ["48.1000|11.5000"]


@pytest.mark.parametrize(
    ("language", "radius", "limit"),
    [
        ("../de", 1_000, 10),
        ("de", 10, 10),
        ("de", 1_000, 0),
    ],
)
def test_wikipedia_validates_inputs(language, radius, limit) -> None:
    if language != "de":
        with pytest.raises(ValueError):
            core_providers.WikimediaKnowledgeProvider(language)
        return
    provider = core_providers.WikimediaKnowledgeProvider(language)
    with pytest.raises(ValueError):
        provider.nearby(48.1, 11.5, radius_m=radius, limit=limit)


def test_wikidata_adapter_uses_language_fallback(monkeypatch) -> None:
    payload = {
        "entities": {
            "Q1": {
                "labels": {"en": {"value": "Old market"}},
                "descriptions": {"en": {"value": "historic market square"}},
            },
            "Q2": {"missing": ""},
        }
    }
    monkeypatch.setattr(
        core_providers.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(payload),
    )
    provider = core_providers.WikidataEntityProvider()
    entities = provider.get_entities(
        ["Q1", "Q2"],
        language="de",
        fallback_language="en",
    )

    assert len(entities) == 1
    assert entities[0].language == "en"
    assert entities[0].url == "https://www.wikidata.org/wiki/Q1"


@pytest.mark.parametrize(
    "ids",
    [[], ["invalid"], [f"Q{index + 1}" for index in range(51)]],
)
def test_wikidata_rejects_invalid_entity_sets(ids) -> None:
    with pytest.raises(ValueError):
        core_providers.WikidataEntityProvider().get_entities(
            ids,
            language="de",
            fallback_language="en",
        )


def test_city_registry_reports_optional_route_capability() -> None:
    without_route = core_providers.build_city_registry(ors_api_key=None)
    with_route = core_providers.build_city_registry(ors_api_key="secret")

    assert "route.walking" not in without_route.capabilities()
    capabilities = with_route.capabilities()
    assert capabilities["route.walking"] == ("openrouteservice",)
    assert capabilities["knowledge.entities"] == ("wikidata",)
    assert core_providers._matches_category("local_shop", {"shop": "bakery"})
    assert not core_providers._matches_category("local_shop", {"shop": "supermarket"})


def test_provider_runner_normalizes_http_auth_and_rate_limit() -> None:
    auth = core_providers.normalize_provider_error(
        urllib.error.HTTPError("https://example", 401, "no", {}, None)
    )
    rate_limit = core_providers.normalize_provider_error(
        urllib.error.HTTPError("https://example", 429, "slow", {}, None)
    )

    assert auth.code == "provider_auth_error"
    assert auth.retryable is False
    assert rate_limit.code == "temporary_http_error"
    assert rate_limit.retryable is True
