#!/usr/bin/env python3
"""Versioned persistent state for live-tour sessions."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
ROUTE_MATCH_STATUSES = {"unknown", "matching", "matched", "ambiguous", "unmatched", "failed"}
SESSION_STATUSES = {"inactive", "starting", "matching_route", "active", "ending"}


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
        "route": {
            "match_status": "unknown",
            "provider": None,
            "id": None,
            "name": None,
            "gpx_path": None,
            "verified": False,
        },
        "position": None,
        "events": {},
        "weather": None,
        "provider_health": {},
    }


def migrate_state(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return empty_state()
    if raw.get("schema_version") == SCHEMA_VERSION:
        validate_state(raw)
        return raw

    migrated = empty_state()
    old_tour = raw.get("tour") or {}
    old_route_verified = bool(old_tour.get("verified", False))
    migrated["route"].update(
        {
            "match_status": "matched" if old_route_verified else "unknown",
            "id": old_tour.get("id"),
            "name": old_tour.get("name"),
            "gpx_path": old_tour.get("gpx"),
            "verified": old_route_verified,
        }
    )
    migrated["position"] = raw.get("last_position")
    migrated["weather"] = raw.get("weather")
    migrated["events"] = {
        str(key): {"status": "reported"} for key in raw.get("reported_facts", [])
    }
    return migrated


def validate_state(state: dict[str, Any]) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported state schema")
    session = state.get("session") or {}
    route = state.get("route") or {}
    if session.get("status") not in SESSION_STATUSES:
        raise ValueError("invalid session status")
    if route.get("match_status") not in ROUTE_MATCH_STATUSES:
        raise ValueError("invalid route match status")
    if route.get("verified") and route.get("match_status") != "matched":
        raise ValueError("verified route must have match_status=matched")


def start_session(
    state: dict[str, Any],
    session_id: str,
    *,
    started_at: float | None = None,
    expires_at: float | None = None,
) -> dict[str, Any]:
    current = migrate_state(state)
    if current["session"].get("id") == session_id and current["session"].get("status") != "inactive":
        return current

    fresh = empty_state()
    fresh["session"] = {
        "id": session_id,
        "status": "starting",
        "started_at": time.time() if started_at is None else started_at,
        "expires_at": expires_at,
        "ended_at": None,
    }
    return fresh


def set_route_match(
    state: dict[str, Any],
    status: str,
    *,
    provider: str | None = None,
    route_id: str | int | None = None,
    name: str | None = None,
    gpx_path: str | None = None,
) -> dict[str, Any]:
    if status not in ROUTE_MATCH_STATUSES:
        raise ValueError("invalid route match status")
    current = migrate_state(state)
    current["route"] = {
        "match_status": status,
        "provider": provider,
        "id": route_id,
        "name": name,
        "gpx_path": gpx_path,
        "verified": status == "matched",
    }
    current["session"]["status"] = "active" if status == "matched" else "matching_route"
    validate_state(current)
    return current


def end_session(state: dict[str, Any], *, ended_at: float | None = None) -> dict[str, Any]:
    current = migrate_state(state)
    current["session"]["status"] = "inactive"
    current["session"]["ended_at"] = time.time() if ended_at is None else ended_at
    current["route"] = empty_state()["route"]
    current["position"] = None
    current["events"] = {}
    current["weather"] = None
    return current


class StateRepository:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return empty_state()
        return migrate_state(raw)

    def save(self, state: dict[str, Any]) -> None:
        validate_state(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
