#!/usr/bin/env python3
"""Candidate discovery, scoring and time-bounded walking itineraries."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

CORE_SCRIPTS = Path(
    os.environ.get(
        "HERMES_LOCATION_CORE_DIR",
        str(Path(__file__).resolve().parents[2] / "location-session-core" / "scripts"),
    )
).expanduser()
if str(CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CORE_SCRIPTS))

from city_contracts import GuideRequest, GuideStop, Itinerary  # noqa: E402
from location_core.providers import (  # noqa: E402
    EntityKnowledgeProvider,
    KnowledgeArticle,
    KnowledgeProvider,
    MapProvider,
    WalkingRouteProvider,
)
from location_core.route_engine import haversine_m  # noqa: E402

CITY_CATEGORIES = (
    "market",
    "cafe",
    "local_food",
    "local_shop",
    "historic",
    "viewpoint",
    "artwork",
    "square",
    "toilets",
)
INTEREST_CATEGORIES = {
    "local_life": {"market", "square", "artwork", "local_shop"},
    "food": {"market", "cafe", "local_food", "local_shop"},
    "history": {"historic", "article"},
    "architecture": {"historic", "square", "article"},
}
DWELL_SECONDS_PER_STOP = 4 * 60
ARTICLE_MATCH_RADIUS_M = 120.0


class PlanningError(RuntimeError):
    pass


def _osm_url(source_id: str) -> str:
    parts = source_id.split(":")
    if len(parts) == 3 and parts[0] == "osm" and parts[1] in {"node", "way", "relation"}:
        return f"https://www.openstreetmap.org/{parts[1]}/{parts[2]}"
    return "https://www.openstreetmap.org"


def _nearest_article(
    lat: float,
    lon: float,
    articles: list[KnowledgeArticle],
) -> KnowledgeArticle | None:
    candidates = [
        (haversine_m(lat, lon, article.lat, article.lon), article)
        for article in articles
    ]
    if not candidates:
        return None
    distance, article = min(candidates, key=lambda item: item[0])
    return article if distance <= ARTICLE_MATCH_RADIUS_M else None


def discover_stops(
    *,
    start: tuple[float, float],
    request: GuideRequest,
    map_provider: MapProvider,
    knowledge_provider: KnowledgeProvider,
    fallback_provider: KnowledgeProvider | None = None,
    entity_provider: EntityKnowledgeProvider | None = None,
) -> list[GuideStop]:
    lat, lon = start
    osm_results = map_provider.search_corridor(
        [(lat, lon, 0.0)],
        list(CITY_CATEGORIES),
    )
    articles = knowledge_provider.nearby(lat, lon, radius_m=2_000, limit=40)
    fallback_articles: list[KnowledgeArticle] = []
    if fallback_provider is not None:
        fallback_articles = fallback_provider.nearby(
            lat,
            lon,
            radius_m=2_000,
            limit=40,
        )
    by_identity = {
        article.wikidata_id or f"{article.language}:{article.title.casefold()}": article
        for article in articles
    }
    for article in fallback_articles:
        identity = article.wikidata_id or f"{article.language}:{article.title.casefold()}"
        by_identity.setdefault(identity, article)
    all_articles = list(by_identity.values())
    entities = {}
    if entity_provider is not None:
        entity_ids = [
            article.wikidata_id
            for article in all_articles
            if article.wikidata_id is not None
        ]
        if entity_ids:
            entities = {
                entity.source_id: entity
                for entity in entity_provider.get_entities(
                    entity_ids,
                    language=request.language,
                    fallback_language=request.fallback_language,
                )
            }
    used_articles: set[str] = set()
    stops: list[GuideStop] = []

    for item in osm_results:
        try:
            item_lat = float(item["lat"])
            item_lon = float(item["lon"])
            source_id = str(item["source_id"])
            category = str(item["category"])
            name = str(item["name"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not name:
            continue
        raw_tags = item.get("tags")
        tags: dict[str, Any] = dict(raw_tags) if isinstance(raw_tags, dict) else {}
        if str(tags.get("opening_hours", "")).strip().casefold() in {
            "closed",
            "off",
        }:
            continue
        matched_article = _nearest_article(item_lat, item_lon, all_articles)
        sources = [
            {
                "source": "OpenStreetMap",
                "source_id": source_id,
                "url": _osm_url(source_id),
                "observed_at": item.get("observed_at"),
                "confidence": item.get("confidence", 0.7),
            }
        ]
        facts: list[dict[str, Any]] = []
        confidence = float(item.get("confidence", 0.7))
        if matched_article is not None:
            used_articles.add(matched_article.source_id)
            sources.append(
                {
                    "source": f"Wikipedia ({matched_article.language})",
                    "source_id": matched_article.source_id,
                    "url": matched_article.url,
                    "observed_at": matched_article.observed_at,
                    "confidence": matched_article.confidence,
                    "wikidata_id": matched_article.wikidata_id,
                }
            )
            facts.append(
                {
                    "text": matched_article.extract,
                    "source": f"Wikipedia ({matched_article.language})",
                    "source_url": matched_article.url,
                    "language": matched_article.language,
                    "observed_at": matched_article.observed_at,
                    "dynamic": False,
                    "confidence": matched_article.confidence,
                }
            )
            entity = (
                entities.get(matched_article.wikidata_id)
                if matched_article.wikidata_id
                else None
            )
            if entity is not None:
                sources.append(
                    {
                        "source": f"Wikidata ({entity.language})",
                        "source_id": entity.source_id,
                        "url": entity.url,
                        "observed_at": entity.observed_at,
                        "confidence": entity.confidence,
                    }
                )
                facts.append(
                    {
                        "text": entity.description,
                        "source": f"Wikidata ({entity.language})",
                        "source_url": entity.url,
                        "language": entity.language,
                        "observed_at": entity.observed_at,
                        "dynamic": False,
                        "confidence": entity.confidence,
                    }
                )
            confidence = max(confidence, matched_article.confidence)
        if tags:
            facts.append(
                {
                    "text": "Community-Metadaten: "
                    + ", ".join(f"{key}={value}" for key, value in sorted(tags.items())),
                    "source": "OpenStreetMap",
                    "source_url": _osm_url(source_id),
                    "language": request.language,
                    "observed_at": item.get("observed_at"),
                    "dynamic": True,
                    "confidence": 0.6,
                }
            )
        stops.append(
            GuideStop(
                stop_id=source_id,
                name=name,
                lat=item_lat,
                lon=item_lon,
                category=category,
                confidence=confidence,
                sources=sources,
                facts=facts,
            )
        )

    for article in all_articles:
        if article.source_id in used_articles:
            continue
        article_facts = [
            {
                "text": article.extract,
                "source": f"Wikipedia ({article.language})",
                "source_url": article.url,
                "language": article.language,
                "observed_at": article.observed_at,
                "dynamic": False,
                "confidence": article.confidence,
            }
        ]
        entity = entities.get(article.wikidata_id) if article.wikidata_id else None
        article_sources = [
            {
                "source": f"Wikipedia ({article.language})",
                "source_id": article.source_id,
                "url": article.url,
                "observed_at": article.observed_at,
                "confidence": article.confidence,
                "wikidata_id": article.wikidata_id,
            }
        ]
        if entity is not None:
            article_sources.append(
                {
                    "source": f"Wikidata ({entity.language})",
                    "source_id": entity.source_id,
                    "url": entity.url,
                    "observed_at": entity.observed_at,
                    "confidence": entity.confidence,
                }
            )
            article_facts.append(
                {
                    "text": entity.description,
                    "source": f"Wikidata ({entity.language})",
                    "source_url": entity.url,
                    "language": entity.language,
                    "observed_at": entity.observed_at,
                    "dynamic": False,
                    "confidence": entity.confidence,
                }
            )
        stops.append(
            GuideStop(
                stop_id=article.source_id,
                name=article.title,
                lat=article.lat,
                lon=article.lon,
                category="article",
                confidence=article.confidence,
                sources=article_sources,
                facts=article_facts,
            )
        )
    return rank_stops(stops, request=request, start=start)


def stop_score(
    stop: GuideStop,
    *,
    request: GuideRequest,
    start: tuple[float, float],
) -> float:
    matched_interests = sum(
        1
        for interest in request.interests
        if stop.category in INTEREST_CATEGORIES.get(interest, set())
    )
    source_bonus = min(1.0, len(stop.sources) * 0.3)
    knowledge_bonus = 1.0 if stop.facts else 0.0
    distance_km = haversine_m(start[0], start[1], stop.lat, stop.lon) / 1_000
    distance_penalty = max(0.0, distance_km - 0.2) * 0.35
    return (
        stop.confidence * 2
        + matched_interests * 1.5
        + source_bonus
        + knowledge_bonus
        - distance_penalty
    )


def rank_stops(
    stops: list[GuideStop],
    *,
    request: GuideRequest,
    start: tuple[float, float],
) -> list[GuideStop]:
    unique: dict[str, GuideStop] = {}
    for stop in stops:
        existing = unique.get(stop.stop_id)
        if existing is None or stop.confidence > existing.confidence:
            unique[stop.stop_id] = stop
    ranked = sorted(
        unique.values(),
        key=lambda stop: (
            -stop_score(stop, request=request, start=start),
            haversine_m(start[0], start[1], stop.lat, stop.lon),
        ),
    )
    selected: list[GuideStop] = []
    category_counts: dict[str, int] = {}
    for stop in ranked:
        limit = 2 if stop.category in {"cafe", "local_food", "article"} else 1
        if category_counts.get(stop.category, 0) >= limit:
            continue
        selected.append(stop)
        category_counts[stop.category] = category_counts.get(stop.category, 0) + 1
        if len(selected) >= request.max_stops:
            break
    if len(selected) < request.max_stops:
        selected_ids = {stop.stop_id for stop in selected}
        for stop in ranked:
            if stop.stop_id in selected_ids:
                continue
            selected.append(stop)
            if len(selected) >= request.max_stops:
                break
    return selected


def _nearest_neighbor_order(
    stops: list[GuideStop],
    start: tuple[float, float],
) -> list[GuideStop]:
    remaining = list(stops)
    ordered: list[GuideStop] = []
    current = start
    while remaining:
        nearest = min(
            remaining,
            key=lambda stop: haversine_m(current[0], current[1], stop.lat, stop.lon),
        )
        ordered.append(nearest)
        remaining.remove(nearest)
        current = (nearest.lat, nearest.lon)
    return ordered


def _route_progress(
    route_points: tuple[tuple[float, float], ...],
    stop: GuideStop,
) -> float:
    cumulative = 0.0
    best_distance = float("inf")
    best_progress = 0.0
    previous = route_points[0]
    for point in route_points:
        cumulative += haversine_m(previous[0], previous[1], point[0], point[1])
        distance = haversine_m(stop.lat, stop.lon, point[0], point[1])
        if distance < best_distance:
            best_distance = distance
            best_progress = cumulative
        previous = point
    return best_progress


def build_itinerary(
    *,
    start: tuple[float, float],
    request: GuideRequest,
    candidates: list[GuideStop],
    route_provider: WalkingRouteProvider,
    revision: int = 1,
) -> Itinerary:
    if len(candidates) < 3:
        raise PlanningError("not_enough_evidenced_stops")
    target_seconds = request.duration_minutes * 60
    selected = _nearest_neighbor_order(candidates[: request.max_stops], start)
    while len(selected) >= 3:
        destination = start if request.round_trip else request.destination
        if destination is None:
            raise PlanningError("missing_destination")
        route = route_provider.route_through(
            [start]
            + [(stop.lat, stop.lon) for stop in selected]
            + [destination]
        )
        walking_seconds = float(route.duration_seconds or 0)
        dwell_seconds = len(selected) * DWELL_SECONDS_PER_STOP
        total_seconds = walking_seconds + dwell_seconds
        if total_seconds <= target_seconds * 1.15:
            if total_seconds < target_seconds * 0.85:
                raise PlanningError("route_is_too_short_for_budget")
            stops_with_progress = tuple(
                replace(
                    stop,
                    route_progress_m=_route_progress(route.points, stop),
                )
                for stop in selected
            )
            return Itinerary(
                stops=stops_with_progress,
                route_points=route.points,
                distance_m=route.distance_m,
                walking_seconds=walking_seconds,
                dwell_seconds=dwell_seconds,
                revision=revision,
                provider=route.provider or "route-provider",
            )
        selected.pop()
    raise PlanningError("route_exceeds_time_budget")


def route_offset_m(
    position: tuple[float, float],
    route_points: list[list[float]] | tuple[tuple[float, float], ...],
) -> float:
    if not route_points:
        return float("inf")
    return min(
        haversine_m(position[0], position[1], float(point[0]), float(point[1]))
        for point in route_points
    )
