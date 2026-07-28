#!/usr/bin/env python3
"""Private schema-v1 state for personal city-guide sessions."""

from __future__ import annotations

import copy
import math
import os
import sys
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

from location_core.repository import (  # noqa: E402,F401
    CorruptStateError,
    JsonStateRepository,
)

SCHEMA_VERSION = 1
SESSION_STATUSES = {"inactive", "planning", "active", "paused", "completed", "failed"}
ITINERARY_STATUSES = {"empty", "planning", "ready", "replanning", "failed"}
STOP_STATUSES = {"planned", "approaching", "delivered", "skipped", "unreachable"}


def empty_schedule() -> dict[str, Any]:
    return {
        "last_wake_at": None,
        "next_due_at": None,
        "last_trigger": None,
        "last_wake_position": None,
        "last_observed_at": None,
        "last_stop_id": None,
        "last_stop_distance_m": None,
        "last_delivered_stop_id": None,
        "route_segment_index": None,
        "route_progress_m": None,
        "off_route_samples": 0,
        "replan_not_before": 0.0,
        "operational_errors": {},
    }


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "session": {
            "id": None,
            "status": "inactive",
            "started_at": None,
            "expires_at": None,
            "ended_at": None,
        },
        "preferences": {
            "duration_minutes": 90,
            "start": None,
            "round_trip": True,
            "destination": None,
            "interests": ["local_life", "food", "history", "architecture"],
            "language": "de",
            "fallback_language": "en",
            "max_stops": 8,
            "narrative_style": "classic",
            "audio": True,
        },
        "itinerary": {
            "status": "empty",
            "provider": None,
            "revision": 0,
            "distance_m": None,
            "walking_seconds": None,
            "dwell_seconds": None,
            "route_points": [],
            "stops": [],
        },
        "position": None,
        "schedule": empty_schedule(),
        "stories": {},
        "provider_health": {},
    }


def migrate_state(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return empty_state()
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported city-guide state schema")
    state = copy.deepcopy(raw)
    validate_state(state)
    return state


def _valid_coordinate(value: Any, minimum: float, maximum: float) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and minimum <= number <= maximum


def validate_state(state: dict[str, Any]) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported city-guide state schema")
    session = state.get("session")
    preferences = state.get("preferences")
    itinerary = state.get("itinerary")
    schedule = state.get("schedule")
    if not isinstance(session, dict) or session.get("status") not in SESSION_STATUSES:
        raise ValueError("invalid city-guide session")
    if session.get("status") not in {"inactive", "completed"} and not session.get("id"):
        raise ValueError("active city-guide session needs an id")
    if not isinstance(preferences, dict):
        raise ValueError("city-guide preferences are missing")
    duration = int(preferences.get("duration_minutes", 0))
    if not 30 <= duration <= 240:
        raise ValueError("city-guide duration is invalid")
    if not isinstance(preferences.get("interests"), list):
        raise ValueError("city-guide interests must be a list")
    for key in ("start", "destination"):
        point = preferences.get(key)
        if point is not None and (
            not isinstance(point, list)
            or len(point) != 2
            or not _valid_coordinate(point[0], -90, 90)
            or not _valid_coordinate(point[1], -180, 180)
        ):
            raise ValueError(f"invalid city-guide {key}")
    if not 3 <= int(preferences.get("max_stops", 0)) <= 12:
        raise ValueError("city-guide max stops are invalid")
    for key in ("language", "fallback_language"):
        language = preferences.get(key)
        if (
            not isinstance(language, str)
            or not language
            or len(language) > 12
            or not language.replace("-", "").isalpha()
        ):
            raise ValueError("invalid city-guide language")
    if not isinstance(itinerary, dict) or itinerary.get("status") not in ITINERARY_STATUSES:
        raise ValueError("invalid city-guide itinerary")
    route_points = itinerary.get("route_points")
    stops = itinerary.get("stops")
    if not isinstance(route_points, list) or not isinstance(stops, list):
        raise ValueError("city-guide route and stops must be lists")
    for point in route_points:
        if (
            not isinstance(point, list)
            or len(point) != 2
            or not _valid_coordinate(point[0], -90, 90)
            or not _valid_coordinate(point[1], -180, 180)
        ):
            raise ValueError("invalid city-guide route point")
    for stop in stops:
        if not isinstance(stop, dict) or stop.get("status") not in STOP_STATUSES:
            raise ValueError("invalid city-guide stop")
        if not _valid_coordinate(stop.get("lat"), -90, 90):
            raise ValueError("invalid city-guide stop latitude")
        if not _valid_coordinate(stop.get("lon"), -180, 180):
            raise ValueError("invalid city-guide stop longitude")
    position = state.get("position")
    if position is not None:
        if not isinstance(position, dict):
            raise ValueError("invalid city-guide position")
        if not _valid_coordinate(position.get("lat"), -90, 90):
            raise ValueError("invalid city-guide position latitude")
        if not _valid_coordinate(position.get("lon"), -180, 180):
            raise ValueError("invalid city-guide position longitude")
    if not isinstance(schedule, dict):
        raise ValueError("city-guide schedule is missing")
    if not isinstance(schedule.get("operational_errors"), dict):
        raise ValueError("operational error state must be an object")
    if not isinstance(state.get("stories"), dict):
        raise ValueError("city-guide stories must be an object")
    if not isinstance(state.get("provider_health"), dict):
        raise ValueError("city-guide provider health must be an object")


class StateRepository(JsonStateRepository):
    def __init__(self, path: Path):
        super().__init__(
            path,
            empty_factory=empty_state,
            migrate=migrate_state,
            validate=validate_state,
        )
