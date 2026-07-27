#!/usr/bin/env python3
"""Provider contracts, health tracking, caching, and retry policy."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class ProviderHealth:
    provider: str
    status: str = "unknown"
    last_success_at: float | None = None
    last_failure_at: float | None = None
    consecutive_failures: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RouteSummary:
    route_id: str
    name: str
    provider: str


@dataclass(frozen=True)
class WeatherSnapshot:
    observed_at: float
    valid_until: float | None
    temperature_c: float | None
    precipitation_rate_mm_h: float | None
    wind_kmh: float | None
    gust_kmh: float | None
    source: str
    confidence: float


class RouteProvider(ABC):
    @abstractmethod
    def list_planned_routes(self) -> list[RouteSummary]: ...

    @abstractmethod
    def download_route(self, route_id: str) -> Path: ...


class MapProvider(ABC):
    @abstractmethod
    def reverse_geocode(self, lat: float, lon: float) -> dict[str, Any]: ...

    @abstractmethod
    def search_corridor(
        self, centers: list[tuple[float, float, float]], categories: list[str]
    ) -> list[dict[str, Any]]: ...


class WeatherProvider(ABC):
    @abstractmethod
    def current_conditions(self, lat: float, lon: float) -> WeatherSnapshot: ...

    @abstractmethod
    def active_warnings(self, lat: float, lon: float) -> list[dict[str, Any]]: ...


class TTLCache(Generic[T]):
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, T]] = {}

    def get(self, key: str, now: float) -> T | None:
        cached = self._values.get(key)
        if cached is None:
            return None
        expires_at, value = cached
        if now >= expires_at:
            self._values.pop(key, None)
            return None
        return value

    def set(self, key: str, value: T, *, now: float, ttl_seconds: float) -> None:
        self._values[key] = (now + ttl_seconds, value)


class ProviderRunner:
    def __init__(
        self,
        name: str,
        *,
        retries: int = 2,
        backoff_seconds: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = name
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep
        self.health = ProviderHealth(provider=name)

    def call(self, operation: Callable[[], T], *, now: float | None = None) -> T:
        timestamp = time.time() if now is None else now
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                result = operation()
            except Exception as error:  # Provider boundary deliberately normalizes failures.
                last_error = error
                self.health.status = "degraded"
                self.health.last_failure_at = timestamp
                self.health.consecutive_failures += 1
                self.health.last_error = f"{type(error).__name__}: {error}"
                if attempt < self.retries:
                    self.sleep(self.backoff_seconds * (2**attempt))
                continue
            self.health.status = "healthy"
            self.health.last_success_at = timestamp
            self.health.consecutive_failures = 0
            self.health.last_error = None
            return result
        raise RuntimeError(f"provider {self.name} failed") from last_error


def corridor_search_centers(
    route_points: list[tuple[float, float, float]],
    current_progress_m: float,
    *,
    distances_ahead_m: tuple[float, ...] = (3_000, 6_000, 10_000),
) -> list[tuple[float, float, float]]:
    """Return (lat, lon, distance_ahead_m) centers sampled forward on a route."""
    if not route_points:
        return []
    centers: list[tuple[float, float, float]] = []
    for distance_ahead in distances_ahead_m:
        target = current_progress_m + distance_ahead
        candidate = next((point for point in route_points if point[2] >= target), None)
        if candidate is not None:
            centers.append((candidate[0], candidate[1], distance_ahead))
    return centers


def rank_corridor_results(
    results: list[dict[str, Any]],
    *,
    max_route_offset_m: float = 500,
) -> list[dict[str, Any]]:
    """Discard results behind or far from route and rank forward/on-route options first."""
    filtered = [
        item
        for item in results
        if float(item.get("distance_ahead_m", -1)) >= 0
        and float(item.get("route_offset_m", float("inf"))) <= max_route_offset_m
    ]
    filtered.sort(
        key=lambda item: (
            float(item.get("route_offset_m", float("inf"))),
            float(item.get("distance_ahead_m", float("inf"))),
            -float(item.get("confidence", 0)),
        )
    )
    return filtered
