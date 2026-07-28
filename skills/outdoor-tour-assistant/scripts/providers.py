#!/usr/bin/env python3
"""Provider contracts, health tracking, caching, and retry policy."""

from __future__ import annotations

import json
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
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
    last_error_code: str | None = None
    circuit_open_until: float | None = None

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


class ProviderCallError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def normalize_provider_error(error: Exception) -> ProviderCallError:
    if isinstance(error, ProviderCallError):
        return error
    if isinstance(error, urllib.error.HTTPError):
        if error.code in {408, 425, 429} or 500 <= error.code < 600:
            return ProviderCallError("temporary_http_error", retryable=True)
        if error.code in {401, 403}:
            return ProviderCallError("provider_auth_error", retryable=False)
        return ProviderCallError("provider_http_error", retryable=False)
    if isinstance(error, (TimeoutError, ConnectionError, urllib.error.URLError)):
        return ProviderCallError("provider_unavailable", retryable=True)
    if isinstance(error, (ValueError, TypeError, json.JSONDecodeError)):
        return ProviderCallError("invalid_provider_response", retryable=False)
    return ProviderCallError("provider_error", retryable=False)


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
        jitter: Callable[[], float] = random.random,
        circuit_failure_threshold: int = 3,
        circuit_cooldown_seconds: float = 60,
    ) -> None:
        self.name = name
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep
        self.jitter = jitter
        self.circuit_failure_threshold = circuit_failure_threshold
        self.circuit_cooldown_seconds = circuit_cooldown_seconds
        self.health = ProviderHealth(provider=name)

    def call(self, operation: Callable[[], T], *, now: float | None = None) -> T:
        timestamp = time.time() if now is None else now
        if (
            self.health.circuit_open_until is not None
            and timestamp < self.health.circuit_open_until
        ):
            raise ProviderCallError("provider_circuit_open", retryable=False)
        last_error: ProviderCallError | None = None
        for attempt in range(self.retries + 1):
            try:
                result = operation()
            except Exception as error:
                normalized = normalize_provider_error(error)
                last_error = normalized
                self.health.status = "degraded"
                self.health.last_failure_at = timestamp
                self.health.consecutive_failures += 1
                self.health.last_error_code = normalized.code
                if self.health.consecutive_failures >= self.circuit_failure_threshold:
                    self.health.circuit_open_until = (
                        timestamp + self.circuit_cooldown_seconds
                    )
                if attempt < self.retries and normalized.retryable:
                    delay = self.backoff_seconds * (2**attempt)
                    self.sleep(delay + delay * 0.25 * self.jitter())
                    continue
                break
            self.health.status = "healthy"
            self.health.last_success_at = timestamp
            self.health.consecutive_failures = 0
            self.health.last_error_code = None
            self.health.circuit_open_until = None
            return result
        if last_error is None:
            last_error = ProviderCallError("provider_error", retryable=False)
        raise last_error


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, dict[str, Any]] = {}

    def register(self, capability: str, name: str, provider: Any) -> None:
        if not capability or not name:
            raise ValueError("capability and provider name are required")
        self._providers.setdefault(capability, {})[name] = provider

    def resolve(self, capability: str, preferred: str | None = None) -> Any:
        providers = self._providers.get(capability, {})
        if preferred:
            if preferred not in providers:
                raise ProviderCallError("provider_not_configured", retryable=False)
            return providers[preferred]
        if not providers:
            raise ProviderCallError("capability_unavailable", retryable=False)
        return next(iter(providers.values()))

    def capabilities(self) -> dict[str, tuple[str, ...]]:
        return {
            capability: tuple(sorted(providers))
            for capability, providers in sorted(self._providers.items())
        }


class DirectoryRouteProvider(RouteProvider):
    """Safe adapter for GPX files produced by Komoot or another route exporter."""

    MAX_ROUTES = 500

    def __init__(self, route_directory: Path) -> None:
        self.route_directory = route_directory.expanduser().resolve()

    def _route_files(self) -> list[Path]:
        if not self.route_directory.is_dir():
            raise ProviderCallError("route_directory_unavailable", retryable=False)
        return [
            path
            for path in sorted(self.route_directory.glob("*.gpx"))
            if path.is_file() and not path.is_symlink()
        ][: self.MAX_ROUTES]

    def list_planned_routes(self) -> list[RouteSummary]:
        return [
            RouteSummary(
                route_id=path.name,
                name=path.stem,
                provider="local-gpx",
            )
            for path in self._route_files()
        ]

    def download_route(self, route_id: str) -> Path:
        if not route_id or Path(route_id).name != route_id or not route_id.endswith(".gpx"):
            raise ProviderCallError("invalid_route_id", retryable=False)
        source = self.route_directory / route_id
        candidate = source.resolve()
        if (
            candidate.parent != self.route_directory
            or not candidate.is_file()
            or source.is_symlink()
        ):
            raise ProviderCallError("route_not_found", retryable=False)
        return candidate


class OpenStreetMapProvider(MapProvider):
    """Structured OSM adapter using fixed Nominatim and Overpass endpoints."""

    REVERSE_ENDPOINT = "https://nominatim.openstreetmap.org/reverse"
    OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
    MAX_RESPONSE_BYTES = 2_000_000
    CATEGORY_FILTERS = {
        "settlement": '["place"~"^(city|town|village|hamlet)$"]',
        "water": '["amenity"="drinking_water"]',
        "supply": '["shop"~"^(supermarket|convenience)$"]',
        "hazard": '["hazard"]',
        "poi": '["tourism"]',
    }

    def __init__(self, *, timeout_seconds: float = 15, radius_m: int = 750) -> None:
        if not 50 <= radius_m <= 2_000:
            raise ValueError("map search radius must be between 50 and 2000 metres")
        self.timeout_seconds = timeout_seconds
        self.radius_m = radius_m

    @staticmethod
    def _validate_coordinate(lat: float, lon: float) -> None:
        OpenMeteoWeatherProvider._validate_coordinate(lat, lon)

    def _request_json(self, request: urllib.request.Request) -> dict[str, Any]:
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            raw = response.read(self.MAX_RESPONSE_BYTES + 1)
        if len(raw) > self.MAX_RESPONSE_BYTES:
            raise ValueError("map response is too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("map response must be an object")
        return payload

    def reverse_geocode(self, lat: float, lon: float) -> dict[str, Any]:
        self._validate_coordinate(lat, lon)
        query = urllib.parse.urlencode(
            {"lat": lat, "lon": lon, "format": "jsonv2", "addressdetails": 1}
        )
        request = urllib.request.Request(
            f"{self.REVERSE_ENDPOINT}?{query}",
            headers={"User-Agent": "Hermes-Tour-Assistant/1.3"},
        )
        payload = self._request_json(request)
        result_lat = float(payload["lat"])
        result_lon = float(payload["lon"])
        self._validate_coordinate(result_lat, result_lon)
        return {
            "source": self.REVERSE_ENDPOINT,
            "source_id": f"osm:{payload.get('osm_type')}:{payload.get('osm_id')}",
            "observed_at": time.time(),
            "name": str(payload.get("display_name") or ""),
            "lat": result_lat,
            "lon": result_lon,
            "confidence": 0.75,
        }

    def search_corridor(
        self,
        centers: list[tuple[float, float, float]],
        categories: list[str],
    ) -> list[dict[str, Any]]:
        if not centers or len(centers) > 8:
            raise ValueError("one to eight route centers are required")
        selected = tuple(dict.fromkeys(categories))
        if not selected or any(item not in self.CATEGORY_FILTERS for item in selected):
            raise ValueError("unsupported or empty map category")
        results: dict[tuple[str, str], dict[str, Any]] = {}
        observed_at = time.time()
        for center_lat, center_lon, distance_ahead_m in centers:
            self._validate_coordinate(center_lat, center_lon)
            if not math.isfinite(distance_ahead_m) or distance_ahead_m < 0:
                raise ValueError("invalid route distance")
            selectors = "".join(
                (
                    f"nwr(around:{self.radius_m},{center_lat:.7f},{center_lon:.7f})"
                    f"{self.CATEGORY_FILTERS[category]};"
                )
                for category in selected
            )
            query = f"[out:json][timeout:15];({selectors});out center tags;"
            request = urllib.request.Request(
                self.OVERPASS_ENDPOINT,
                data=urllib.parse.urlencode({"data": query}).encode(),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Hermes-Tour-Assistant/1.3",
                },
            )
            payload = self._request_json(request)
            elements = payload.get("elements")
            if not isinstance(elements, list):
                raise ValueError("map response has no elements list")
            for element in elements:
                normalized = self._normalize_element(
                    element,
                    center=(center_lat, center_lon, distance_ahead_m),
                    categories=selected,
                    observed_at=observed_at,
                )
                if normalized is not None:
                    key = (normalized["source_id"], normalized["category"])
                    existing = results.get(key)
                    if existing is None or normalized["route_offset_m"] < existing["route_offset_m"]:
                        results[key] = normalized
        return rank_corridor_results(list(results.values()), max_route_offset_m=self.radius_m)

    def _normalize_element(
        self,
        element: Any,
        *,
        center: tuple[float, float, float],
        categories: tuple[str, ...],
        observed_at: float,
    ) -> dict[str, Any] | None:
        if not isinstance(element, dict) or "id" not in element:
            return None
        tags = element.get("tags")
        if not isinstance(tags, dict):
            tags = {}
        category = next(
            (item for item in categories if _matches_category(item, tags)),
            None,
        )
        if category is None:
            return None
        center_value = element.get("center")
        location = center_value if isinstance(center_value, dict) else element
        try:
            lat = float(location["lat"])
            lon = float(location["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        self._validate_coordinate(lat, lon)
        center_lat, center_lon, distance_ahead_m = center
        offset = _haversine_m(center_lat, center_lon, lat, lon)
        return {
            "source": self.OVERPASS_ENDPOINT,
            "source_id": f"osm:{element.get('type')}:{element['id']}",
            "observed_at": observed_at,
            "category": category,
            "name": str(tags.get("name") or tags.get("amenity") or category),
            "lat": lat,
            "lon": lon,
            "distance_ahead_m": distance_ahead_m,
            "route_offset_m": offset,
            "confidence": 0.7,
            "tags": {
                key: str(tags[key])
                for key in ("access", "drinking_water", "opening_hours")
                if key in tags
            },
        }


class OpenMeteoWeatherProvider(WeatherProvider):
    """Structured current-weather adapter with a fixed, auditable endpoint."""

    ENDPOINT = "https://api.open-meteo.com/v1/forecast"
    MAX_RESPONSE_BYTES = 1_000_000

    def __init__(self, *, timeout_seconds: float = 10) -> None:
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _validate_coordinate(lat: float, lon: float) -> None:
        if not math.isfinite(lat) or not -90 <= lat <= 90:
            raise ValueError("invalid latitude")
        if not math.isfinite(lon) or not -180 <= lon <= 180:
            raise ValueError("invalid longitude")

    def current_conditions(self, lat: float, lon: float) -> WeatherSnapshot:
        self._validate_coordinate(lat, lon)
        query = urllib.parse.urlencode(
            {
                "latitude": lat,
                "longitude": lon,
                "current": (
                    "temperature_2m,precipitation,wind_speed_10m,"
                    "wind_gusts_10m"
                ),
                "timezone": "UTC",
            }
        )
        request = urllib.request.Request(
            f"{self.ENDPOINT}?{query}",
            headers={"User-Agent": "Hermes-Tour-Assistant/1.3"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            raw = response.read(self.MAX_RESPONSE_BYTES + 1)
        if len(raw) > self.MAX_RESPONSE_BYTES:
            raise ValueError("weather response is too large")
        payload = json.loads(raw)
        current = payload.get("current")
        if not isinstance(current, dict):
            raise ValueError("weather response has no current block")
        observed_at = time.time()
        return WeatherSnapshot(
            observed_at=observed_at,
            valid_until=observed_at + 900,
            temperature_c=_optional_float(current.get("temperature_2m")),
            precipitation_rate_mm_h=_optional_float(current.get("precipitation")),
            wind_kmh=_optional_float(current.get("wind_speed_10m")),
            gust_kmh=_optional_float(current.get("wind_gusts_10m")),
            source=self.ENDPOINT,
            confidence=0.9,
        )

    def active_warnings(self, lat: float, lon: float) -> list[dict[str, Any]]:
        self._validate_coordinate(lat, lon)
        raise ProviderCallError("warnings_capability_unavailable", retryable=False)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("provider returned a non-finite number")
    return result


def _matches_category(category: str, tags: dict[str, Any]) -> bool:
    if category == "settlement":
        return tags.get("place") in {"city", "town", "village", "hamlet"}
    if category == "water":
        return tags.get("amenity") == "drinking_water"
    if category == "supply":
        return tags.get("shop") in {"supermarket", "convenience"}
    if category == "hazard":
        return "hazard" in tags
    if category == "poi":
        return "tourism" in tags
    return False


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def build_default_registry(*, route_directory: Path | None = None) -> ProviderRegistry:
    registry = ProviderRegistry()
    weather = OpenMeteoWeatherProvider()
    maps = OpenStreetMapProvider()
    registry.register("weather.current", "open-meteo", weather)
    registry.register("map.reverse", "openstreetmap", maps)
    registry.register("map.corridor", "openstreetmap", maps)
    registry.register("water.search", "openstreetmap", maps)
    if route_directory is not None:
        registry.register(
            "route.planned",
            "local-gpx",
            DirectoryRouteProvider(route_directory),
        )
    return registry


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
