#!/usr/bin/env python3
"""Persistent event policy for live-tour notifications."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

PRIORITY = {
    "safety": 0,
    "off_route": 1,
    "weather_warning": 2,
    "supply_gap": 3,
    "settlement_approach": 4,
    "poi": 5,
}
EVIDENCE_REQUIRED = {
    "safety",
    "weather_warning",
    "supply_gap",
    "settlement_approach",
    "poi",
}


@dataclass
class TourEvent:
    event_id: str
    event_type: str
    severity: str
    confidence: float
    first_detected_at: float
    last_detected_at: float
    status: str = "active"
    last_sent_at: float | None = None
    cooldown_until: float = 0.0
    resolved_at: float | None = None
    route_distance_ahead_m: float | None = None
    route_offset_m: float | None = None
    payload: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def upsert_event(
    events: dict[str, dict[str, Any]],
    candidate: TourEvent,
) -> dict[str, dict[str, Any]]:
    current = events.get(candidate.event_id)
    if current is not None:
        candidate.first_detected_at = float(current["first_detected_at"])
        candidate.last_sent_at = current.get("last_sent_at")
        candidate.cooldown_until = float(current.get("cooldown_until", 0) or 0)
    events[candidate.event_id] = candidate.to_dict()
    return events


def resolve_event(
    events: dict[str, dict[str, Any]],
    event_id: str,
    now: float,
) -> dict[str, dict[str, Any]]:
    if event_id in events:
        events[event_id]["status"] = "resolved"
        events[event_id]["resolved_at"] = now
    return events


def eligible_events(events: dict[str, dict[str, Any]], now: float) -> list[dict[str, Any]]:
    return [
        event
        for event in events.values()
        if event.get("status") == "active"
        and float(event.get("cooldown_until", 0) or 0) <= now
        and float(event.get("confidence", 0) or 0) >= 0.5
        and (
            event.get("event_type") not in EVIDENCE_REQUIRED
            or bool(event.get("evidence"))
        )
    ]


def select_for_delivery(
    events: dict[str, dict[str, Any]],
    now: float,
    *,
    safety_limit: int = 3,
) -> list[dict[str, Any]]:
    eligible = eligible_events(events, now)
    eligible.sort(
        key=lambda event: (
            PRIORITY.get(str(event.get("event_type")), 99),
            -severity_score(str(event.get("severity"))),
            float(event.get("route_distance_ahead_m") or float("inf")),
        )
    )
    if not eligible:
        return []
    if eligible[0].get("event_type") == "safety":
        return [event for event in eligible if event.get("event_type") == "safety"][:safety_limit]
    return eligible[:1]


def mark_delivered(
    events: dict[str, dict[str, Any]],
    delivered: list[dict[str, Any]],
    now: float,
    *,
    cooldown_seconds: float = 600,
) -> dict[str, dict[str, Any]]:
    for event in delivered:
        stored = events[event["event_id"]]
        stored["last_sent_at"] = now
        stored["cooldown_until"] = now + cooldown_seconds
    return events


def severity_score(severity: str) -> int:
    return {"info": 1, "notice": 2, "warning": 3, "critical": 4}.get(severity, 0)


def route_checkpoints(progress_m: float, remaining_m: float, spacing_m: float = 5_000) -> list[dict[str, Any]]:
    """Return neutral route checkpoints. They are deliberately not settlements."""
    checkpoints: list[dict[str, Any]] = []
    next_distance = spacing_m
    while next_distance < remaining_m:
        checkpoints.append(
            {
                "kind": "route_checkpoint",
                "route_progress_m": progress_m + next_distance,
                "distance_ahead_m": next_distance,
            }
        )
        next_distance += spacing_m
    return checkpoints


def settlement_events(
    settlements: list[dict[str, Any]],
    *,
    now: float,
    max_distance_ahead_m: float = 3_000,
) -> list[TourEvent]:
    result: list[TourEvent] = []
    for settlement in settlements:
        distance = float(settlement.get("distance_ahead_m", -1))
        verified = bool(settlement.get("verified_place", False))
        if not verified or not 0 < distance <= max_distance_ahead_m:
            continue
        place_id = str(settlement.get("id") or settlement.get("name"))
        source = str(settlement.get("source") or "").strip()
        if not source:
            continue
        result.append(
            TourEvent(
                event_id=f"settlement:{place_id}",
                event_type="settlement_approach",
                severity="info",
                confidence=float(settlement.get("confidence", 0.8)),
                first_detected_at=now,
                last_detected_at=now,
                route_distance_ahead_m=distance,
                route_offset_m=settlement.get("route_offset_m"),
                payload={"name": settlement.get("name")},
                evidence=[
                    {
                        "source": source,
                        "kind": "verified_place",
                        "observed_at": settlement.get("observed_at", now),
                    }
                ],
            )
        )
    return result
