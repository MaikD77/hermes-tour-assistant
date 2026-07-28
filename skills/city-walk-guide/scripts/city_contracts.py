#!/usr/bin/env python3
"""Validated public contracts for the city-walk guide."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_INTERESTS = ("local_life", "food", "history", "architecture")
STOP_STATUSES = {"planned", "approaching", "delivered", "skipped", "unreachable"}


def _coordinate(value: Any, *, latitude: bool) -> float:
    number = float(value)
    minimum, maximum = (-90.0, 90.0) if latitude else (-180.0, 180.0)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError("invalid coordinate")
    return number


@dataclass(frozen=True)
class GuideRequest:
    duration_minutes: int = 90
    start: tuple[float, float] | None = None
    round_trip: bool = True
    destination: tuple[float, float] | None = None
    interests: tuple[str, ...] = DEFAULT_INTERESTS
    language: str = "de"
    fallback_language: str = "en"
    max_stops: int = 8

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "GuideRequest":
        duration = int(value.get("duration_minutes", 90))
        if not 30 <= duration <= 240:
            raise ValueError("duration must be between 30 and 240 minutes")
        interests_value = value.get("interests", DEFAULT_INTERESTS)
        if not isinstance(interests_value, (list, tuple)):
            raise ValueError("interests must be a list")
        interests = tuple(
            item.strip().lower()
            for item in map(str, interests_value)
            if item.strip()
        )
        if not interests or len(interests) > 8:
            raise ValueError("one to eight interests are required")
        language = str(value.get("language", "de")).strip().lower()
        fallback = str(value.get("fallback_language", "en")).strip().lower()
        for item in (language, fallback):
            if (
                not item
                or len(item) > 12
                or any(character not in "abcdefghijklmnopqrstuvwxyz-" for character in item)
            ):
                raise ValueError("invalid guide language")
        max_stops = int(value.get("max_stops", max(3, min(12, round(duration / 12)))))
        if not 3 <= max_stops <= 12:
            raise ValueError("max_stops must be between 3 and 12")
        destination_value = value.get("destination")
        start_value = value.get("start")
        start = None
        if start_value is not None:
            if not isinstance(start_value, (list, tuple)) or len(start_value) != 2:
                raise ValueError("start must contain latitude and longitude")
            start = (
                _coordinate(start_value[0], latitude=True),
                _coordinate(start_value[1], latitude=False),
            )
        destination = None
        if destination_value is not None:
            if not isinstance(destination_value, (list, tuple)) or len(destination_value) != 2:
                raise ValueError("destination must contain latitude and longitude")
            destination = (
                _coordinate(destination_value[0], latitude=True),
                _coordinate(destination_value[1], latitude=False),
            )
        round_trip_value = value.get("round_trip", destination is None)
        if not isinstance(round_trip_value, bool):
            raise ValueError("round_trip must be a boolean")
        round_trip = round_trip_value
        if destination is None and not round_trip:
            raise ValueError("a non-round-trip guide requires a destination")
        return cls(
            duration_minutes=duration,
            start=start,
            round_trip=round_trip,
            destination=destination,
            interests=interests,
            language=language,
            fallback_language=fallback,
            max_stops=max_stops,
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["interests"] = list(self.interests)
        if self.start is not None:
            value["start"] = list(self.start)
        if self.destination is not None:
            value["destination"] = list(self.destination)
        return value


@dataclass(frozen=True)
class KnowledgeFact:
    text: str
    source: str
    source_url: str
    language: str
    observed_at: float
    dynamic: bool = False
    confidence: float = 0.8

    def __post_init__(self) -> None:
        if not self.text.strip() or not self.source.strip():
            raise ValueError("knowledge fact needs text and source")
        if not self.source_url.startswith("https://"):
            raise ValueError("knowledge source must use HTTPS")
        if not 0 <= self.confidence <= 1:
            raise ValueError("knowledge confidence is out of range")


@dataclass
class GuideStop:
    stop_id: str
    name: str
    lat: float
    lon: float
    category: str
    confidence: float
    sources: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    route_progress_m: float | None = None
    status: str = "planned"

    def __post_init__(self) -> None:
        if not self.stop_id.strip() or not self.name.strip():
            raise ValueError("guide stop needs id and name")
        self.lat = _coordinate(self.lat, latitude=True)
        self.lon = _coordinate(self.lon, latitude=False)
        if not 0 <= self.confidence <= 1:
            raise ValueError("guide stop confidence is out of range")
        if self.status not in STOP_STATUSES:
            raise ValueError("invalid guide stop status")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Itinerary:
    stops: tuple[GuideStop, ...]
    route_points: tuple[tuple[float, float], ...]
    distance_m: float
    walking_seconds: float
    dwell_seconds: float
    revision: int = 1
    provider: str = "openrouteservice"

    def __post_init__(self) -> None:
        if not 1 <= len(self.stops) <= 12:
            raise ValueError("itinerary needs one to twelve stops")
        if len(self.route_points) < 2:
            raise ValueError("itinerary route is missing")
        if self.distance_m <= 0 or self.walking_seconds <= 0 or self.dwell_seconds < 0:
            raise ValueError("itinerary timing is invalid")
        if self.revision < 1:
            raise ValueError("itinerary revision is invalid")

    @property
    def total_seconds(self) -> float:
        return self.walking_seconds + self.dwell_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "stops": [stop.to_dict() for stop in self.stops],
            "route_points": [list(point) for point in self.route_points],
            "distance_m": self.distance_m,
            "walking_seconds": self.walking_seconds,
            "dwell_seconds": self.dwell_seconds,
            "revision": self.revision,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class GuideDecision:
    silent: bool
    stop_id: str | None = None
    title: str | None = None
    text: str | None = None
    sources: tuple[dict[str, str], ...] = ()
    translation_required: bool = False

    def __post_init__(self) -> None:
        if self.silent and any(
            (
                self.stop_id,
                self.title,
                self.text,
                self.sources,
                self.translation_required,
            )
        ):
            raise ValueError("silent guide decision cannot contain a story")
        if not self.silent and not all((self.stop_id, self.title, self.text)):
            raise ValueError("guide story needs stop, title and text")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
