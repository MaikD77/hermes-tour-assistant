from __future__ import annotations

import json
from pathlib import Path

import pytest


def _sample(module, *, lat=48.1, lon=11.5, observed_at=10.0, session="session-1"):
    return module.LocationSample(
        session_id=session,
        message_id="message-1",
        observed_at=observed_at,
        expires_at=10_000.0,
        lat=lat,
        lon=lon,
    )


def _fact(language: str = "de") -> dict[str, object]:
    return {
        "text": (
            "Der Ort entstand im 18. Jahrhundert. "
            "Seine heutige Gestalt entwickelte sich später. "
            "Viele Menschen nutzen den Platz täglich. "
            "Die Architektur zeigt mehrere Bauphasen. "
            "Ein weiteres Detail ist in den Fassaden erkennbar."
        ),
        "source": f"Wikipedia ({language})",
        "source_url": f"https://{language}.wikipedia.org/example",
        "language": language,
        "observed_at": 1.0,
        "dynamic": False,
        "confidence": 0.85,
    }


def _seed_runtime(module, tmp_path: Path):
    runtime = module.CityRuntime(tmp_path / "city_guide_state.json")
    stops = [
        module.GuideStop(
            stop_id=f"stop-{index}",
            name=f"Station {index}",
            lat=48.1000 + index * 0.001,
            lon=11.5000 + index * 0.001,
            category="historic",
            confidence=0.85,
            sources=[{"source": "Wikipedia"}],
            facts=[_fact()],
            route_progress_m=index * 150.0,
        )
        for index in range(3)
    ]
    state = module.empty_state()
    state["session"].update(
        {
            "id": "session-1",
            "status": "active",
            "started_at": 1.0,
            "expires_at": 10_000.0,
        }
    )
    state["preferences"]["start"] = [48.1, 11.5]
    state["itinerary"].update(
        {
            "status": "ready",
            "provider": "fixture",
            "revision": 1,
            "distance_m": 500.0,
            "walking_seconds": 4_000.0,
            "dwell_seconds": 720.0,
            "route_points": [
                [48.1, 11.5],
                [48.101, 11.501],
                [48.102, 11.502],
                [48.1, 11.5],
            ],
            "stops": [stop.to_dict() for stop in stops],
        }
    )
    state["position"] = {"lat": 48.099, "lon": 11.499, "observed_at": 1.0}
    state["stories"] = {
        stop.stop_id: module._story_for_stop(stop, language="de") for stop in stops
    }
    runtime.repository.save(state)
    return runtime


def test_gate_triggers_one_story_without_coordinates(
    city_runtime_module,
    tmp_path: Path,
) -> None:
    runtime = _seed_runtime(city_runtime_module, tmp_path)

    decision = runtime.evaluate_gate(_sample(city_runtime_module), now=10.0)
    payload = decision.to_cron_payload()

    assert payload["wakeAgent"] is True
    assert payload["context"]["reason"] == "guide_stop"
    assert "lat" not in json.dumps(payload)
    assert "11.5" not in json.dumps(payload)

    story = runtime.next_story()
    assert story.silent is False
    assert story.stop_id == "stop-0"
    assert len(story.sources) == 1
    assert runtime.next_story().silent is True
    assert runtime.more().stop_id == "stop-0"


def test_gate_ignores_duplicate_samples_and_pause(
    city_runtime_module,
    tmp_path: Path,
) -> None:
    runtime = _seed_runtime(city_runtime_module, tmp_path)
    sample = _sample(city_runtime_module, lat=48.099, lon=11.499)

    assert runtime.evaluate_gate(sample, now=10.0).wake_agent is False
    assert runtime.evaluate_gate(sample, now=11.0).wake_agent is False
    runtime.pause()
    later = _sample(
        city_runtime_module,
        lat=48.1,
        lon=11.5,
        observed_at=11.0,
    )
    assert runtime.evaluate_gate(later, now=12.0).wake_agent is False
    assert runtime.resume()["session"]["status"] == "active"


def test_sustained_deviation_replans_with_cooldown(
    city_runtime_module,
    tmp_path: Path,
) -> None:
    runtime = _seed_runtime(city_runtime_module, tmp_path)
    first = _sample(
        city_runtime_module,
        lat=48.2,
        lon=11.7,
        observed_at=20.0,
    )
    second = _sample(
        city_runtime_module,
        lat=48.201,
        lon=11.701,
        observed_at=21.0,
    )
    third = _sample(
        city_runtime_module,
        lat=48.202,
        lon=11.702,
        observed_at=22.0,
    )

    assert runtime.evaluate_gate(first, now=20.0).wake_agent is False
    decision = runtime.evaluate_gate(second, now=21.0)
    assert decision.reason == "replan_required"
    assert "sustained_deviation" in decision.flags
    assert runtime.evaluate_gate(third, now=22.0).wake_agent is False


def test_session_change_expiry_skip_and_end(
    city_runtime_module,
    tmp_path: Path,
) -> None:
    runtime = _seed_runtime(city_runtime_module, tmp_path)
    changed = _sample(city_runtime_module, session="different", observed_at=20.0)

    decision = runtime.evaluate_gate(changed, now=20.0)
    assert decision.error_code == "location_session_changed"
    assert runtime.skip_stop("stop-1")["itinerary"]["stops"][1]["status"] == "skipped"
    assert runtime.end(now=30.0)["session"]["status"] == "completed"
    assert runtime.evaluate_gate(changed, now=31.0).wake_agent is False

    runtime = _seed_runtime(city_runtime_module, tmp_path / "expired")
    assert runtime.evaluate_gate(
        _sample(city_runtime_module, observed_at=20.0),
        now=10_001.0,
    ).wake_agent is False
    assert runtime.repository.load()["session"]["status"] == "completed"


def test_gate_finishes_only_after_return_to_start(
    city_runtime_module,
    tmp_path: Path,
) -> None:
    runtime = _seed_runtime(city_runtime_module, tmp_path)
    state = runtime.repository.load()
    for stop in state["itinerary"]["stops"]:
        stop["status"] = "delivered"
    state["schedule"]["route_segment_index"] = 2
    state["schedule"]["route_progress_m"] = 500.0
    runtime.repository.save(state)

    decision = runtime.evaluate_gate(
        _sample(city_runtime_module, observed_at=20.0),
        now=20.0,
    )

    assert decision.reason == "guide_finished"
    assert runtime.repository.load()["session"]["status"] == "completed"


def test_operational_errors_are_rate_limited(
    city_runtime_module,
    tmp_path: Path,
) -> None:
    runtime = _seed_runtime(city_runtime_module, tmp_path)

    first = runtime.operational_decision("provider_unavailable", now=90_000.0)
    repeated = runtime.operational_decision("provider_unavailable", now=90_001.0)

    assert first.wake_agent is True
    assert repeated.wake_agent is False


def test_replan_rebuilds_remaining_route(
    city_runtime_module,
    providers_module,
    contracts_module,
    tmp_path: Path,
) -> None:
    runtime = _seed_runtime(city_runtime_module, tmp_path)
    registry = providers_module.ProviderRegistry()
    registry.register(
        "route.walking",
        "fixture-route",
        _RouteProvider(contracts_module.RouteGeometry),
    )

    state = runtime.replan(
        _sample(
            city_runtime_module,
            lat=48.11,
            lon=11.51,
            observed_at=20.0,
        ),
        registry,
        request_override=city_runtime_module.GuideRequest(
            interests=("food",),
            max_stops=3,
        ),
        now=20.0,
    )

    assert state["itinerary"]["revision"] == 2
    assert state["itinerary"]["status"] == "ready"
    assert state["schedule"]["off_route_samples"] == 0
    assert state["preferences"]["interests"] == ["food"]
    assert state["provider_health"]["openrouteservice"]["status"] == "healthy"


def test_english_story_is_marked_as_transferred(city_runtime_module) -> None:
    stop = city_runtime_module.GuideStop(
        stop_id="english",
        name="English Place",
        lat=48.1,
        lon=11.5,
        category="article",
        confidence=0.8,
        facts=[_fact("en")],
    )

    story = city_runtime_module._story_for_stop(stop, language="de")

    assert story is not None
    assert "englischen Quelle" in story["text"]


def test_story_escapes_untrusted_markdown(city_runtime_module) -> None:
    fact = _fact()
    fact["text"] = "Belegt [aber nicht ausführbar](javascript:alert(1))."
    fact["source"] = "Wiki [extern]"
    stop = city_runtime_module.GuideStop(
        stop_id="unsafe",
        name="Platz [Test]",
        lat=48.1,
        lon=11.5,
        category="square",
        confidence=0.8,
        facts=[fact],
    )

    story = city_runtime_module._story_for_stop(stop, language="de")

    assert story is not None
    assert "\\[" in story["title"]
    assert "\\[" in story["text"]
    assert "\\[" in story["sources"][0]["label"]


class _MapProvider:
    def reverse_geocode(self, lat, lon):
        return {}

    def search_corridor(self, centers, categories):
        return [
            {
                "source_id": f"osm:node:{index}",
                "name": f"Ort {index}",
                "lat": 48.1 + index * 0.0002,
                "lon": 11.5 + index * 0.0002,
                "category": category,
                "confidence": 0.8,
                "observed_at": 1.0,
                "tags": {},
            }
            for index, category in enumerate(("market", "historic", "square"))
        ]


class _KnowledgeProvider:
    def __init__(self, article_type) -> None:
        self.article_type = article_type

    def nearby(self, lat, lon, *, radius_m, limit):
        return [
            self.article_type(
                source_id=f"wikipedia:de:{index}",
                title=f"Ort {index}",
                language="de",
                url=f"https://de.wikipedia.org/Ort_{index}",
                extract=str(_fact()["text"]),
                lat=48.1 + index * 0.0002,
                lon=11.5 + index * 0.0002,
                observed_at=1.0,
                wikidata_id=f"Q{index}",
                confidence=0.85,
            )
            for index in range(3)
        ]


class _FailingKnowledgeProvider:
    def nearby(self, lat, lon, *, radius_m, limit):
        raise ValueError("untrusted raw provider detail")


class _RouteProvider:
    def __init__(self, geometry_type, *, duration=4_000.0) -> None:
        self.geometry_type = geometry_type
        self.duration = duration

    def route_through(self, coordinates):
        return self.geometry_type(
            points=tuple(coordinates),
            distance_m=2_000.0,
            duration_seconds=self.duration,
            provider="fixture",
        )


def test_end_to_end_plan_gate_story(
    city_runtime_module,
    providers_module,
    contracts_module,
    city_contracts_module,
    tmp_path: Path,
) -> None:
    registry = providers_module.ProviderRegistry()
    registry.register("map.nearby", "fixture-map", _MapProvider())
    registry.register(
        "knowledge.nearby",
        "fixture-knowledge",
        _KnowledgeProvider(providers_module.KnowledgeArticle),
    )
    registry.register(
        "knowledge.fallback",
        "fixture-failing-fallback",
        _FailingKnowledgeProvider(),
    )
    registry.register(
        "route.walking",
        "fixture-route",
        _RouteProvider(contracts_module.RouteGeometry),
    )
    runtime = city_runtime_module.CityRuntime(tmp_path / "city_guide_state.json")
    sample = _sample(city_runtime_module)

    state = runtime.plan_and_start(
        sample,
        city_contracts_module.GuideRequest(max_stops=3),
        registry,
        now=10.0,
    )
    assert state["session"]["status"] == "active"
    assert len(state["stories"]) == 3
    assert state["provider_health"]["wikipedia-fallback"]["status"] == "degraded"
    assert (
        state["provider_health"]["wikipedia-fallback"]["last_error_code"]
        == "invalid_provider_response"
    )
    assert "untrusted raw provider detail" not in json.dumps(state)
    assert runtime.evaluate_gate(
        _sample(city_runtime_module, observed_at=11.0),
        now=11.0,
    ).reason == "guide_stop"
    assert runtime.next_story().silent is False
    context = runtime.agent_context()
    assert "position" not in context
    assert "route_points" not in context["itinerary"]
    assert all("lat" not in stop for stop in context["itinerary"]["stops"])


def test_runtime_preserves_actionable_planning_error(
    city_runtime_module,
    providers_module,
    contracts_module,
    city_contracts_module,
    tmp_path: Path,
) -> None:
    registry = providers_module.ProviderRegistry()
    registry.register("map.nearby", "fixture-map", _MapProvider())
    registry.register(
        "knowledge.nearby",
        "fixture-knowledge",
        _KnowledgeProvider(providers_module.KnowledgeArticle),
    )
    registry.register(
        "route.walking",
        "fixture-route",
        _RouteProvider(contracts_module.RouteGeometry, duration=100.0),
    )

    with pytest.raises(
        city_runtime_module.PlanningError,
        match="route_is_too_short_for_budget",
    ):
        city_runtime_module.CityRuntime(
            tmp_path / "city_guide_state.json"
        ).plan_and_start(
            _sample(city_runtime_module),
            city_contracts_module.GuideRequest(max_stops=3),
            registry,
            now=10.0,
        )
