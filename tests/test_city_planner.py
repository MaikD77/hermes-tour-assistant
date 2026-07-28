from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class FakeMap:
    results: list[dict]

    def search_corridor(self, centers, categories):
        assert centers[0][2] == 0.0
        assert "historic" in categories
        return self.results

    def reverse_geocode(self, lat, lon):
        return {}


@dataclass
class FakeKnowledge:
    articles: list

    def nearby(self, lat, lon, *, radius_m, limit):
        assert radius_m == 2_000
        return self.articles[:limit]


class FakeRoute:
    def __init__(self, contracts, *, duration: float = 4_200) -> None:
        self.contracts = contracts
        self.duration = duration
        self.calls = 0

    def route_through(self, coordinates):
        self.calls += 1
        return self.contracts.RouteGeometry(
            points=tuple(coordinates),
            distance_m=2_500,
            duration_seconds=self.duration,
            provider="fixture-route",
        )


def _stop(contracts, index: int, category: str = "historic"):
    return contracts.GuideStop(
        stop_id=f"stop-{index}",
        name=f"Station {index}",
        lat=48.1000 + index * 0.001,
        lon=11.5000 + index * 0.001,
        category=category,
        confidence=0.8,
        sources=[{"source": "fixture"}],
        facts=[
            {
                "text": "Ein belegter Fakt.",
                "source": "Wikipedia (de)",
                "source_url": "https://de.wikipedia.org/example",
                "language": "de",
                "observed_at": 1.0,
                "dynamic": False,
                "confidence": 0.8,
            }
        ],
    )


def test_discovery_prefers_german_and_merges_by_wikidata(
    city_planner_module,
    city_contracts_module,
    providers_module,
) -> None:
    article_type = providers_module.KnowledgeArticle
    german = article_type(
        "wikipedia:de:1",
        "Alter Markt",
        "de",
        "https://de.wikipedia.org/Alter_Markt",
        "Der Markt ist seit Jahrhunderten ein Mittelpunkt der Stadt.",
        48.101,
        11.501,
        10.0,
        "Q1",
        0.85,
    )
    english_duplicate = article_type(
        "wikipedia:en:1",
        "Old Market",
        "en",
        "https://en.wikipedia.org/Old_Market",
        "The market is old.",
        48.101,
        11.501,
        10.0,
        "Q1",
        0.85,
    )
    others = [
        article_type(
            f"wikipedia:de:{index}",
            f"Ort {index}",
            "de",
            f"https://de.wikipedia.org/Ort_{index}",
            f"Ort {index} hat eine belegte Geschichte.",
            48.101 + index * 0.001,
            11.501 + index * 0.001,
            10.0,
            f"Q{index}",
            0.8,
        )
        for index in range(2, 5)
    ]
    osm = {
        "source_id": "osm:node:10",
        "name": "Alter Markt",
        "lat": 48.101,
        "lon": 11.501,
        "category": "market",
        "confidence": 0.7,
        "observed_at": 10.0,
        "tags": {"opening_hours": "Mo-Sa"},
    }
    request = city_contracts_module.GuideRequest(max_stops=6)

    stops = city_planner_module.discover_stops(
        start=(48.1, 11.5),
        request=request,
        map_provider=FakeMap([osm]),
        knowledge_provider=FakeKnowledge([german, *others]),
        fallback_provider=FakeKnowledge([english_duplicate]),
    )

    market = next(stop for stop in stops if stop.stop_id == "osm:node:10")
    assert any(fact["language"] == "de" for fact in market.facts)
    assert not any(stop.name == "Old Market" for stop in stops)
    assert any(fact["dynamic"] for fact in market.facts)


def test_build_itinerary_stays_within_budget(
    city_planner_module,
    city_contracts_module,
    contracts_module,
) -> None:
    request = city_contracts_module.GuideRequest(duration_minutes=90, max_stops=6)
    candidates = [_stop(city_contracts_module, index) for index in range(6)]
    provider = FakeRoute(contracts_module, duration=4_000)

    itinerary = city_planner_module.build_itinerary(
        start=(48.1, 11.5),
        request=request,
        candidates=candidates,
        route_provider=provider,
    )

    assert 90 * 60 * 0.85 <= itinerary.total_seconds <= 90 * 60 * 1.15
    assert len(itinerary.stops) == 6
    assert all(stop.route_progress_m is not None for stop in itinerary.stops)


def test_planner_rejects_too_short_too_long_and_too_few(
    city_planner_module,
    city_contracts_module,
    contracts_module,
) -> None:
    request = city_contracts_module.GuideRequest(duration_minutes=90, max_stops=6)
    candidates = [_stop(city_contracts_module, index) for index in range(6)]

    with pytest.raises(city_planner_module.PlanningError, match="too_short"):
        city_planner_module.build_itinerary(
            start=(48.1, 11.5),
            request=request,
            candidates=candidates,
            route_provider=FakeRoute(contracts_module, duration=100),
        )
    with pytest.raises(city_planner_module.PlanningError, match="exceeds"):
        city_planner_module.build_itinerary(
            start=(48.1, 11.5),
            request=request,
            candidates=candidates,
            route_provider=FakeRoute(contracts_module, duration=8_000),
        )
    with pytest.raises(city_planner_module.PlanningError, match="not_enough"):
        city_planner_module.build_itinerary(
            start=(48.1, 11.5),
            request=request,
            candidates=candidates[:2],
            route_provider=FakeRoute(contracts_module),
        )


def test_rank_stops_preserves_category_variety(
    city_planner_module,
    city_contracts_module,
) -> None:
    categories = ["cafe", "cafe", "cafe", "historic", "market", "square"]
    stops = [
        _stop(city_contracts_module, index, category)
        for index, category in enumerate(categories)
    ]

    ranked = city_planner_module.rank_stops(
        stops,
        request=city_contracts_module.GuideRequest(max_stops=6),
        start=(48.1, 11.5),
    )

    assert sum(stop.category == "cafe" for stop in ranked[:5]) == 2
    assert {"historic", "market", "square"} <= {stop.category for stop in ranked}


def test_discovery_omits_explicitly_closed_community_place(
    city_planner_module,
    city_contracts_module,
) -> None:
    closed = {
        "source_id": "osm:node:closed",
        "name": "Geschlossenes Café",
        "lat": 48.101,
        "lon": 11.501,
        "category": "cafe",
        "confidence": 0.7,
        "observed_at": 1.0,
        "tags": {"opening_hours": "closed"},
    }

    stops = city_planner_module.discover_stops(
        start=(48.1, 11.5),
        request=city_contracts_module.GuideRequest(),
        map_provider=FakeMap([closed]),
        knowledge_provider=FakeKnowledge([]),
    )

    assert all(stop.stop_id != "osm:node:closed" for stop in stops)
