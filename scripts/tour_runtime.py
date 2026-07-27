#!/usr/bin/env python3
"""Integration layer joining session state, route matching, and event policy."""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from event_engine import TourEvent, mark_delivered, select_for_delivery, upsert_event
from route_engine import RouteMatch, match_position, parse_gpx
from tour_state import StateRepository, set_route_match, start_session


class TourRuntime:
    def __init__(self, state_path: Path):
        self.repository = StateRepository(state_path)

    def begin_session(
        self,
        session_id: str,
        *,
        started_at: float,
        expires_at: float | None,
    ) -> dict[str, Any]:
        state = start_session(
            self.repository.load(),
            session_id,
            started_at=started_at,
            expires_at=expires_at,
        )
        self.repository.save(state)
        return state

    def attach_route(
        self,
        *,
        provider: str,
        route_id: str | int,
        name: str,
        gpx_path: Path,
    ) -> dict[str, Any]:
        parse_gpx(gpx_path)
        state = set_route_match(
            self.repository.load(),
            "matched",
            provider=provider,
            route_id=route_id,
            name=name,
            gpx_path=str(gpx_path.resolve()),
        )
        self.repository.save(state)
        return state

    def update_position(self, lat: float, lon: float, *, observed_at: float) -> RouteMatch:
        state = self.repository.load()
        route_state = state["route"]
        if not route_state.get("verified") or not route_state.get("gpx_path"):
            raise RuntimeError("no verified route attached")
        route = parse_gpx(Path(route_state["gpx_path"]))
        previous = state.get("position") or {}
        match = match_position(
            lat,
            lon,
            route,
            previous_segment_index=previous.get("segment_index"),
            previous_progress_m=previous.get("progress_m"),
        )
        state["position"] = {
            "observed_at": observed_at,
            "lat": lat,
            "lon": lon,
            **asdict(match),
        }
        self.repository.save(state)
        return match

    def record_event(self, event: TourEvent) -> None:
        state = self.repository.load()
        upsert_event(state["events"], event)
        self.repository.save(state)

    def next_notifications(self, *, now: float) -> list[dict[str, Any]]:
        state = self.repository.load()
        selected = select_for_delivery(state["events"], now)
        mark_delivered(state["events"], selected, now)
        self.repository.save(state)
        return selected
