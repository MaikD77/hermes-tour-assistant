#!/usr/bin/env python3
"""Deterministic GPX parsing and segment-based map matching."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class RoutePoint:
    lat: float
    lon: float
    cumulative_m: float


@dataclass(frozen=True)
class RouteMatch:
    segment_index: int
    fraction: float
    offset_m: float
    progress_m: float
    remaining_m: float
    direction: str
    confidence: float


def haversine_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def parse_gpx(path: Path) -> list[RoutePoint]:
    root = ET.parse(path).getroot()
    raw: list[tuple[float, float]] = []
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        if local_name not in {"trkpt", "rtept"}:
            continue
        if "lat" not in element.attrib or "lon" not in element.attrib:
            continue
        raw.append((float(element.attrib["lat"]), float(element.attrib["lon"])))
    if len(raw) < 2:
        raise ValueError("GPX route requires at least two points")

    points: list[RoutePoint] = []
    cumulative = 0.0
    previous: tuple[float, float] | None = None
    for lat, lon in raw:
        if previous is not None:
            cumulative += haversine_m(previous[0], previous[1], lat, lon)
        points.append(RoutePoint(lat, lon, cumulative))
        previous = (lat, lon)
    return points


def _local_xy(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    y = math.radians(lat - origin_lat) * EARTH_RADIUS_M
    x = (
        math.radians(lon - origin_lon)
        * EARTH_RADIUS_M
        * math.cos(math.radians(origin_lat))
    )
    return x, y


def _project_to_segment(
    lat: float,
    lon: float,
    start: RoutePoint,
    end: RoutePoint,
) -> tuple[float, float]:
    sx, sy = _local_xy(start.lat, start.lon, lat, lon)
    ex, ey = _local_xy(end.lat, end.lon, lat, lon)
    dx, dy = ex - sx, ey - sy
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return 0.0, math.hypot(sx, sy)
    fraction = max(0.0, min(1.0, -(sx * dx + sy * dy) / length_squared))
    px, py = sx + fraction * dx, sy + fraction * dy
    return fraction, math.hypot(px, py)


def match_position(
    lat: float,
    lon: float,
    route: list[RoutePoint],
    *,
    previous_segment_index: int | None = None,
    previous_progress_m: float | None = None,
    search_window: int = 250,
) -> RouteMatch:
    if len(route) < 2:
        raise ValueError("route requires at least two points")

    start_index = 0
    end_index = len(route) - 1
    if previous_segment_index is not None:
        start_index = max(0, previous_segment_index - search_window)
        end_index = min(len(route) - 1, previous_segment_index + search_window + 1)

    best: tuple[float, int, float] | None = None
    for index in range(start_index, end_index):
        fraction, offset = _project_to_segment(lat, lon, route[index], route[index + 1])
        candidate = (offset, index, fraction)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        raise ValueError("no route segment available")

    offset, index, fraction = best
    segment_length = route[index + 1].cumulative_m - route[index].cumulative_m
    progress = route[index].cumulative_m + fraction * segment_length
    total = route[-1].cumulative_m

    direction = "unknown"
    if previous_progress_m is not None:
        delta = progress - previous_progress_m
        if delta > 10:
            direction = "forward"
        elif delta < -10:
            direction = "reverse"

    confidence = max(0.0, min(1.0, 1.0 - offset / 250.0))
    return RouteMatch(
        segment_index=index,
        fraction=round(fraction, 6),
        offset_m=round(offset, 1),
        progress_m=round(progress, 1),
        remaining_m=round(max(0.0, total - progress), 1),
        direction=direction,
        confidence=round(confidence, 3),
    )
