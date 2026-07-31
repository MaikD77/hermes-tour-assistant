#!/usr/bin/env python3
"""Deterministic runtime for private, source-backed city-walk sessions."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

CORE_SCRIPTS = Path(
    os.environ.get(
        "HERMES_LOCATION_CORE_DIR",
        str(Path(__file__).resolve().parents[2] / "location-session-core" / "scripts"),
    )
).expanduser()
if str(CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CORE_SCRIPTS))

from city_contracts import GuideDecision, GuideRequest, GuideStop  # noqa: E402
from city_planner import (  # noqa: E402
    PlanningError,
    build_itinerary,
    discover_stops,
    rank_stops,
)
from city_state import StateRepository, empty_state  # noqa: E402
from location_core.contracts import (  # noqa: E402
    GateDecision,
    LocationSample,  # noqa: F401 - compatibility export for callers
    RouteGeometry,
)
from location_core.location_sources import LocationObservation  # noqa: E402
from location_core.output_safety import safe_label, safe_prose  # noqa: E402
from location_core.providers import (  # noqa: E402
    EntityKnowledgeProvider,
    KnowledgeArticle,
    KnowledgeEntity,
    KnowledgeProvider,
    ProviderCallError,
    ProviderRegistry,
    ProviderRunner,
    WalkingRouteProvider,
)
from location_core.route_engine import (  # noqa: E402
    RoutePoint,
    haversine_m,
    match_position,
)

STOP_APPROACH_M = 80.0
OFF_ROUTE_M = 120.0
OFF_ROUTE_SAMPLES = 2
REPLAN_COOLDOWN_SECONDS = 5 * 60
OPERATIONAL_ERROR_COOLDOWN_SECONDS = 24 * 60 * 60
STOP_DIRECTION_TOLERANCE_M = 15.0
PROVIDER_ENV = {
    "map.nearby": "HERMES_CITY_GUIDE_MAP_PROVIDER",
    "knowledge.nearby": "HERMES_CITY_GUIDE_KNOWLEDGE_PROVIDER",
    "knowledge.fallback": "HERMES_CITY_GUIDE_FALLBACK_PROVIDER",
    "knowledge.entities": "HERMES_CITY_GUIDE_ENTITY_PROVIDER",
    "route.walking": "HERMES_CITY_GUIDE_ROUTE_PROVIDER",
}


def _preferred_provider(capability: str) -> str | None:
    value = os.environ.get(PROVIDER_ENV[capability], "").strip()
    return value or None


class _OptionalKnowledgeProvider(KnowledgeProvider):
    def __init__(self, provider: KnowledgeProvider, runner: ProviderRunner, now: float):
        self.provider = provider
        self.runner = runner
        self.now = now

    def nearby(
        self,
        lat: float,
        lon: float,
        *,
        radius_m: int,
        limit: int,
    ) -> list[KnowledgeArticle]:
        try:
            return self.runner.call(
                lambda: self.provider.nearby(
                    lat,
                    lon,
                    radius_m=radius_m,
                    limit=limit,
                ),
                now=self.now,
            )
        except ProviderCallError:
            return []


class _OptionalEntityProvider(EntityKnowledgeProvider):
    def __init__(
        self,
        provider: EntityKnowledgeProvider,
        runner: ProviderRunner,
        now: float,
    ):
        self.provider = provider
        self.runner = runner
        self.now = now

    def get_entities(
        self,
        entity_ids: list[str],
        *,
        language: str,
        fallback_language: str,
    ) -> list[KnowledgeEntity]:
        try:
            return self.runner.call(
                lambda: self.provider.get_entities(
                    entity_ids,
                    language=language,
                    fallback_language=fallback_language,
                ),
                now=self.now,
            )
        except ProviderCallError:
            return []


class _RunningRouteProvider(WalkingRouteProvider):
    def __init__(
        self,
        provider: WalkingRouteProvider,
        runner: ProviderRunner,
        now: float,
    ):
        self.provider = provider
        self.runner = runner
        self.now = now

    def route_through(
        self,
        coordinates: list[tuple[float, float]],
    ) -> RouteGeometry:
        return self.runner.call(
            lambda: self.provider.route_through(coordinates),
            now=self.now,
        )


def _route_points(raw_points: list[list[float]]) -> list[RoutePoint]:
    points: list[RoutePoint] = []
    cumulative = 0.0
    previous: tuple[float, float] | None = None
    for raw in raw_points:
        lat, lon = float(raw[0]), float(raw[1])
        if previous is not None:
            cumulative += haversine_m(previous[0], previous[1], lat, lon)
        points.append(RoutePoint(lat, lon, cumulative))
        previous = (lat, lon)
    return points


def _sentences(text: str, *, maximum: int = 4) -> list[str]:
    clean = " ".join(text.split())
    if not clean:
        return []
    parts = re.split(r"(?<=[.!?])\s+", clean)
    return [part for part in parts if part][:maximum]


def _story_for_stop(stop: GuideStop, *, language: str) -> dict[str, Any] | None:
    source_facts = [
        fact
        for fact in stop.facts
        if isinstance(fact, dict)
        and str(fact.get("text", "")).strip()
        and str(fact.get("source_url", "")).startswith("https://")
    ]
    if not source_facts:
        return None
    permanent = [fact for fact in source_facts if not bool(fact.get("dynamic"))]
    selected = (permanent or source_facts)[:4]
    fact_sentences: list[str] = []
    for fact in selected:
        fact_text = safe_prose(fact["text"])
        if fact.get("dynamic"):
            fact_text = f"Nicht aktuell verifizierte Community-Angabe: {fact_text}"
        fact_sentences.extend(_sentences(fact_text, maximum=4))
        if len(fact_sentences) >= 8:
            break
    if not fact_sentences:
        return None
    category_openings = {
        "market": "Hier zeigt sich ein Stück lokaler Alltag.",
        "cafe": "Dieser Halt steht für die heutige Genusskultur der Stadt.",
        "local_food": "Hier verbindet sich Stadtgeschichte mit heutiger Esskultur.",
        "local_shop": "Dieser Halt zeigt ein Stück lokaler Handels- und Genusskultur.",
        "historic": "Dieser Ort hilft, die Entwicklung der Stadt einzuordnen.",
        "viewpoint": "Von hier lässt sich die Gestalt der Stadt besonders gut lesen.",
        "artwork": "Dieses Werk setzt einen Akzent im heutigen Stadtraum.",
        "square": "Dieser Platz ist Bühne des täglichen Stadtlebens.",
        "article": "Dieser Ort erzählt ein markantes Kapitel der Stadt.",
    }
    opening = category_openings.get(
        stop.category,
        "Dieser Halt eröffnet einen besonderen Blick auf die Stadt.",
    )
    fallback_used = any(str(fact.get("language")) != language for fact in selected)
    text = f"{opening} {' '.join(fact_sentences[:4])}"
    if fallback_used:
        text += " Die Hintergrundinformationen wurden aus einer englischen Quelle übertragen."
    text += " Achte auch darauf, wie der Ort heute genutzt wird – darin lebt seine Geschichte weiter."
    detail_sentences = fact_sentences[4:8] or fact_sentences[:2]
    detail = (
        f"Noch etwas zu {safe_prose(stop.name, maximum_length=160)}: "
        f"{' '.join(detail_sentences)} "
        "Solche Details zeigen, wie viele Zeitschichten an einem einzigen Ort "
        "zusammenkommen."
    )
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for fact in selected:
        url = str(fact["source_url"])
        if url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append({"label": safe_label(fact["source"]), "url": url})
    return {
        "title": safe_label(stop.name),
        "text": text,
        "detail": detail,
        "sources": sources,
        "language": language,
        "translation_required": fallback_used,
        "prepared_at": time.time(),
    }


class CityRuntime:
    def __init__(self, state_path: Path):
        self.repository = StateRepository(state_path)

    def plan_and_start(
        self,
        sample: LocationObservation,
        request: GuideRequest,
        registry: ProviderRegistry,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = time.time() if now is None else now
        map_provider = registry.resolve(
            "map.nearby",
            _preferred_provider("map.nearby"),
        )
        knowledge_provider = registry.resolve(
            "knowledge.nearby",
            _preferred_provider("knowledge.nearby"),
        )
        fallback_preference = _preferred_provider("knowledge.fallback")
        try:
            fallback_provider = registry.resolve(
                "knowledge.fallback",
                fallback_preference,
            )
        except ProviderCallError:
            if fallback_preference is not None:
                raise
            fallback_provider = None
        entity_preference = _preferred_provider("knowledge.entities")
        try:
            entity_provider = registry.resolve(
                "knowledge.entities",
                entity_preference,
            )
        except ProviderCallError:
            if entity_preference is not None:
                raise
            entity_provider = None
        route_provider = registry.resolve(
            "route.walking",
            _preferred_provider("route.walking"),
        )
        planning_start = request.start or (sample.lat, sample.lon)
        discovery_runner = ProviderRunner("city-discovery")
        route_runner = ProviderRunner("openrouteservice")
        optional_runners: dict[str, ProviderRunner] = {}
        if fallback_provider is not None:
            fallback_runner = ProviderRunner("wikipedia-fallback")
            optional_runners["wikipedia-fallback"] = fallback_runner
            fallback_provider = _OptionalKnowledgeProvider(
                fallback_provider,
                fallback_runner,
                timestamp,
            )
        if entity_provider is not None:
            entity_runner = ProviderRunner("wikidata")
            optional_runners["wikidata"] = entity_runner
            entity_provider = _OptionalEntityProvider(
                entity_provider,
                entity_runner,
                timestamp,
            )
        provider_health = {
            "city-discovery": discovery_runner.health.to_dict(),
            "openrouteservice": route_runner.health.to_dict(),
        }
        try:
            candidates = discovery_runner.call(
                lambda: discover_stops(
                    start=planning_start,
                    request=request,
                    map_provider=map_provider,
                    knowledge_provider=knowledge_provider,
                    fallback_provider=fallback_provider,
                    entity_provider=entity_provider,
                ),
                now=timestamp,
            )
            itinerary = build_itinerary(
                start=planning_start,
                request=request,
                candidates=candidates,
                route_provider=_RunningRouteProvider(
                    route_provider,
                    route_runner,
                    timestamp,
                ),
            )
        except (ProviderCallError, PlanningError):
            provider_health.update(
                {
                    name: runner.health.to_dict()
                    for name, runner in optional_runners.items()
                }
            )
            provider_health["city-discovery"] = discovery_runner.health.to_dict()
            provider_health["openrouteservice"] = route_runner.health.to_dict()
            self._record_health(provider_health)
            raise
        stories = {
            stop.stop_id: story
            for stop in itinerary.stops
            if (story := _story_for_stop(stop, language=request.language)) is not None
        }
        if not stories:
            raise PlanningError("no_evidenced_stories")

        def operation(_: dict[str, Any]) -> dict[str, Any]:
            state = empty_state()
            state["session"] = {
                "id": sample.session_id,
                "status": "active",
                "started_at": timestamp,
                "expires_at": sample.expires_at,
                "ended_at": None,
            }
            state["preferences"].update(request.to_dict())
            state["preferences"]["start"] = list(planning_start)
            state["itinerary"] = {"status": "ready", **itinerary.to_dict()}
            state["position"] = {
                "observed_at": sample.observed_at,
                "lat": sample.lat,
                "lon": sample.lon,
            }
            state["stories"] = stories
            provider_health.update(
                {
                    name: runner.health.to_dict()
                    for name, runner in optional_runners.items()
                }
            )
            provider_health["city-discovery"] = discovery_runner.health.to_dict()
            provider_health["openrouteservice"] = route_runner.health.to_dict()
            state["provider_health"] = provider_health
            return state

        return self.repository.update(operation)

    def _record_health(self, health: dict[str, dict[str, Any]]) -> None:
        def operation(state: dict[str, Any]) -> dict[str, Any]:
            state["provider_health"].update(health)
            return state

        self.repository.update(operation)

    def evaluate_gate(
        self,
        sample: LocationObservation,
        *,
        now: float | None = None,
    ) -> GateDecision:
        timestamp = time.time() if now is None else now
        decisions: list[GateDecision] = []

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            session = state["session"]
            schedule = state["schedule"]
            if session["status"] in {"inactive", "completed", "failed"}:
                decisions.append(GateDecision(wake_agent=False))
                return state
            if sample.session_id != session["id"]:
                decisions.append(
                    GateDecision(
                        wake_agent=True,
                        session_id=str(session["id"]),
                        reason="operational_error",
                        flags=("session_changed",),
                        error_code="location_session_changed",
                    )
                )
                return state
            if timestamp >= float(session["expires_at"]):
                session["status"] = "completed"
                session["ended_at"] = timestamp
                decisions.append(GateDecision(wake_agent=False))
                return state
            if session["status"] == "paused":
                decisions.append(GateDecision(wake_agent=False))
                return state
            previous_observed_at = schedule.get("last_observed_at")
            if previous_observed_at is not None and sample.observed_at <= float(
                previous_observed_at
            ):
                decisions.append(GateDecision(wake_agent=False))
                return state

            route = _route_points(state["itinerary"]["route_points"])
            match = match_position(
                sample.lat,
                sample.lon,
                route,
                previous_segment_index=schedule.get("route_segment_index"),
                previous_progress_m=schedule.get("route_progress_m"),
            )
            state["position"] = {
                "observed_at": sample.observed_at,
                "lat": sample.lat,
                "lon": sample.lon,
                "route_offset_m": match.offset_m,
                "route_progress_m": match.progress_m,
            }
            schedule["last_observed_at"] = sample.observed_at
            schedule["route_segment_index"] = match.segment_index
            schedule["route_progress_m"] = match.progress_m

            next_stop = None
            for stop in state["itinerary"]["stops"]:
                if stop["status"] != "planned":
                    continue
                if stop["stop_id"] not in state["stories"]:
                    stop_progress = stop.get("route_progress_m")
                    if (
                        stop_progress is not None
                        and match.progress_m >= float(stop_progress) - STOP_APPROACH_M
                    ):
                        stop["status"] = "skipped"
                    continue
                next_stop = stop
                break
            if next_stop is not None:
                distance = haversine_m(
                    sample.lat,
                    sample.lon,
                    float(next_stop["lat"]),
                    float(next_stop["lon"]),
                )
                previous_distance = (
                    float(schedule["last_stop_distance_m"])
                    if schedule.get("last_stop_id") == next_stop["stop_id"]
                    and schedule.get("last_stop_distance_m") is not None
                    else None
                )
                direction_plausible = (
                    previous_distance is None
                    or distance <= previous_distance + STOP_DIRECTION_TOLERANCE_M
                    or match.direction == "forward"
                )
                schedule["last_stop_id"] = next_stop["stop_id"]
                schedule["last_stop_distance_m"] = round(distance, 1)
                if (
                    distance <= STOP_APPROACH_M
                    and direction_plausible
                    and next_stop["stop_id"] in state["stories"]
                ):
                    next_stop["status"] = "approaching"
                    schedule["last_wake_at"] = timestamp
                    schedule["last_trigger"] = "guide_stop"
                    decisions.append(
                        GateDecision(
                            wake_agent=True,
                            session_id=str(session["id"]),
                            reason="guide_stop",
                            cadence_minutes=1,
                            flags=("story_ready",),
                        )
                    )
                    return state
            else:
                preferences = state["preferences"]
                finish_target = (
                    preferences.get("start")
                    if preferences.get("round_trip")
                    else preferences.get("destination")
                )
                if (
                    isinstance(finish_target, list)
                    and len(finish_target) == 2
                    and match.remaining_m <= 150
                    and haversine_m(
                        sample.lat,
                        sample.lon,
                        float(finish_target[0]),
                        float(finish_target[1]),
                    )
                    <= STOP_APPROACH_M
                ):
                    session["status"] = "completed"
                    session["ended_at"] = timestamp
                    schedule["last_wake_at"] = timestamp
                    schedule["last_trigger"] = "guide_finished"
                    decisions.append(
                        GateDecision(
                            wake_agent=True,
                            session_id=str(session["id"]),
                            reason="guide_finished",
                            cadence_minutes=1,
                            flags=("destination_reached",),
                        )
                    )
                    return state

            if match.offset_m >= OFF_ROUTE_M:
                schedule["off_route_samples"] = int(
                    schedule.get("off_route_samples", 0)
                ) + 1
            else:
                schedule["off_route_samples"] = 0
            if (
                schedule["off_route_samples"] >= OFF_ROUTE_SAMPLES
                and timestamp >= float(schedule.get("replan_not_before", 0))
            ):
                schedule["replan_not_before"] = timestamp + REPLAN_COOLDOWN_SECONDS
                schedule["last_wake_at"] = timestamp
                schedule["last_trigger"] = "replan_required"
                state["itinerary"]["status"] = "replanning"
                decisions.append(
                    GateDecision(
                        wake_agent=True,
                        session_id=str(session["id"]),
                        reason="replan_required",
                        cadence_minutes=1,
                        flags=("sustained_deviation",),
                    )
                )
                return state
            decisions.append(GateDecision(wake_agent=False))
            return state

        self.repository.update(operation)
        return decisions[0]

    def next_story(self) -> GuideDecision:
        decisions: list[GuideDecision] = []

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            stop = next(
                (
                    item
                    for item in state["itinerary"]["stops"]
                    if item["status"] == "approaching"
                ),
                None,
            )
            if stop is None:
                decisions.append(GuideDecision(silent=True))
                return state
            story = state["stories"].get(stop["stop_id"])
            if not isinstance(story, dict):
                stop["status"] = "skipped"
                decisions.append(GuideDecision(silent=True))
                return state
            stop["status"] = "delivered"
            state["schedule"]["last_delivered_stop_id"] = stop["stop_id"]
            decisions.append(
                GuideDecision(
                    silent=False,
                    stop_id=stop["stop_id"],
                    title=str(story["title"]),
                    text=str(story["text"]),
                    sources=tuple(story.get("sources", [])),
                    translation_required=bool(story.get("translation_required")),
                )
            )
            return state

        self.repository.update(operation)
        return decisions[0]

    def more(self) -> GuideDecision:
        state = self.repository.load()
        stop_id = state["schedule"].get("last_delivered_stop_id")
        story = state["stories"].get(stop_id) if stop_id else None
        if not isinstance(story, dict):
            return GuideDecision(silent=True)
        return GuideDecision(
            silent=False,
            stop_id=str(stop_id),
            title=str(story["title"]),
            text=str(story.get("detail") or story["text"]),
            sources=tuple(story.get("sources", [])),
            translation_required=bool(story.get("translation_required")),
        )

    def skip_stop(self, stop_id: str | None = None) -> dict[str, Any]:
        def operation(state: dict[str, Any]) -> dict[str, Any]:
            stop = next(
                (
                    item
                    for item in state["itinerary"]["stops"]
                    if (stop_id is None and item["status"] in {"planned", "approaching"})
                    or item["stop_id"] == stop_id
                ),
                None,
            )
            if stop is None:
                raise ValueError("no matching stop")
            if stop["status"] not in {"planned", "approaching"}:
                raise ValueError("stop cannot be skipped")
            stop["status"] = "skipped"
            return state

        return self.repository.update(operation)

    def pause(self) -> dict[str, Any]:
        return self._set_session_status("paused", allowed={"active"})

    def resume(self) -> dict[str, Any]:
        return self._set_session_status("active", allowed={"paused"})

    def end(self, *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else now

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            state["session"]["status"] = "completed"
            state["session"]["ended_at"] = timestamp
            return state

        return self.repository.update(operation)

    def _set_session_status(
        self,
        status: str,
        *,
        allowed: set[str],
    ) -> dict[str, Any]:
        def operation(state: dict[str, Any]) -> dict[str, Any]:
            if state["session"]["status"] not in allowed:
                raise ValueError("city-guide session is not in the required state")
            state["session"]["status"] = status
            return state

        return self.repository.update(operation)

    def replan(
        self,
        sample: LocationObservation,
        registry: ProviderRegistry,
        *,
        request_override: GuideRequest | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = time.time() if now is None else now
        state = self.repository.load()
        stored_request = request_override or GuideRequest.from_mapping(
            state["preferences"]
        )
        request_mapping = stored_request.to_dict()
        request_mapping["start"] = state["preferences"].get("start")
        if request_mapping.get("round_trip"):
            original_start = request_mapping.get("start")
            if not isinstance(original_start, list) or len(original_start) != 2:
                raise PlanningError("missing_original_start")
            request_mapping.update(
                {
                    "start": None,
                    "round_trip": False,
                    "destination": original_start,
                }
            )
        request = GuideRequest.from_mapping(request_mapping)
        candidates = [
            GuideStop(**{**stop, "status": "planned"})
            for stop in state["itinerary"]["stops"]
            if stop["status"] in {"planned", "approaching"}
        ]
        if len(candidates) < 3:
            raise PlanningError("not_enough_remaining_stops")
        candidates = rank_stops(
            candidates,
            request=stored_request,
            start=(sample.lat, sample.lon),
        )
        provider = registry.resolve(
            "route.walking",
            _preferred_provider("route.walking"),
        )
        runner = ProviderRunner("openrouteservice")
        itinerary = build_itinerary(
            start=(sample.lat, sample.lon),
            request=request,
            candidates=candidates,
            route_provider=_RunningRouteProvider(provider, runner, timestamp),
            revision=int(state["itinerary"]["revision"]) + 1,
        )

        def operation(current: dict[str, Any]) -> dict[str, Any]:
            original_start = current["preferences"].get("start")
            current["preferences"].update(stored_request.to_dict())
            current["preferences"]["start"] = original_start
            current["itinerary"] = {"status": "ready", **itinerary.to_dict()}
            current["position"] = {
                "observed_at": sample.observed_at,
                "lat": sample.lat,
                "lon": sample.lon,
            }
            current["schedule"]["off_route_samples"] = 0
            current["schedule"]["route_segment_index"] = None
            current["schedule"]["route_progress_m"] = None
            current["provider_health"]["openrouteservice"] = runner.health.to_dict()
            return current

        return self.repository.update(operation)

    def agent_context(self, *, include_location: bool = False) -> dict[str, Any]:
        state = self.repository.load()
        context: dict[str, Any] = {
            "schema_version": state["schema_version"],
            "session": state["session"],
            "preferences": {
                key: value
                for key, value in state["preferences"].items()
                if key not in {"start", "destination"}
            },
            "itinerary": {
                **{
                    key: value
                    for key, value in state["itinerary"].items()
                    if key not in {"route_points", "stops"}
                },
                "stops": [
                    {
                        key: (
                            safe_label(value)
                            if key in {"name", "category"}
                            else value
                        )
                        for key, value in stop.items()
                        if key not in {"lat", "lon", "facts"}
                    }
                    for stop in state["itinerary"]["stops"]
                ],
            },
            "provider_health": state["provider_health"],
        }
        if include_location:
            context["position"] = state["position"]
        return context

    def operational_decision(self, code: str, *, now: float) -> GateDecision:
        decisions: list[GateDecision] = []

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            errors = state["schedule"]["operational_errors"]
            last_at = float(errors.get(code, 0))
            should_wake = now - last_at >= OPERATIONAL_ERROR_COOLDOWN_SECONDS
            if should_wake:
                errors[code] = now
            decisions.append(
                GateDecision(
                    wake_agent=should_wake,
                    session_id=state["session"].get("id"),
                    reason="operational_error" if should_wake else None,
                    flags=(code,) if should_wake else (),
                    error_code=code if should_wake else None,
                )
            )
            return state

        self.repository.update(operation)
        return decisions[0]


__all__ = [
    "CityRuntime",
    "OFF_ROUTE_M",
    "OFF_ROUTE_SAMPLES",
    "REPLAN_COOLDOWN_SECONDS",
    "STOP_APPROACH_M",
]
