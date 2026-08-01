"""Private persistence and sanitized export for the mobility profile."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .movement import DataQuality, MovementMode
from .profile import (
    FactEvidence,
    FactStatus,
    FactType,
    PersonalContextFact,
    PlaceStatistics,
    PlaceTransitionPattern,
    ProfileState,
    TransitionSample,
)
from .repository import JsonStateRepository

SCHEMA_VERSION = 1


def empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "profile": None}


def migrate_state(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("schema_version", 0) == 0 and not raw.get("profile"):
        return empty_state()
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported profile state schema")
    return raw


def validate_state(raw: dict[str, Any]) -> None:
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid profile state schema")
    if raw.get("profile") is not None and not isinstance(raw["profile"], dict):
        raise ValueError("profile state must be an object")


def _default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return getattr(value, "value")
    raise TypeError(type(value).__name__)


def encode_state(state: ProfileState) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(state), default=_default))


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("profile timestamp must include timezone")
    return result


def _required_dt(value: str) -> datetime:
    result = _dt(value)
    if result is None:
        raise ValueError("profile timestamp is required")
    return result


def decode_state(raw: dict[str, Any]) -> ProfileState:
    facts = tuple(PersonalContextFact(item["fact_id"], FactType(item["fact_type"]),
        item["subject_id"], item["value"], float(item["confidence"]),
        FactEvidence(**item["evidence"]), _required_dt(item["first_observed_at"]),
        _required_dt(item["last_observed_at"]), _required_dt(item["computed_at"]),
        int(item["sample_count"]), FactStatus(item["status"])) for item in raw.get("facts", []))
    statistics = tuple(PlaceStatistics(item["place_id"], int(item["visit_count"]),
        int(item["distinct_days"]), int(item["observed_days"]), float(item["visits_per_week"]),
        float(item["active_day_fraction"]), float(item["total_dwell_seconds"]),
        float(item["mean_dwell_seconds"]), float(item["median_dwell_seconds"]),
        tuple(item["arrival_minutes"]), tuple(item["departure_minutes"]),
        tuple(item["dwell_samples"]), tuple(item["active_dates"]),
        tuple(item["weekday_distribution"]), int(item["weekday_count"]),
        int(item["weekend_count"]), int(item["overnight_count"]), float(item["quality_sum"]),
        _required_dt(item["first_observed_at"]), _required_dt(item["last_observed_at"]))
        for item in raw.get("place_statistics", []))
    samples = tuple(TransitionSample(item["sample_id"], item["from_place_id"],
        item["to_place_id"], _required_dt(item["departed_at"]),
        _required_dt(item["arrived_at"]), float(item["duration_seconds"]),
        MovementMode(item["mode"]), DataQuality(item["quality"]))
        for item in raw.get("transition_samples", []))
    transitions = tuple(PlaceTransitionPattern(item["transition_id"], item["from_place_id"],
        item["to_place_id"], int(item["sample_count"]), _required_dt(item["first_seen_at"]),
        _required_dt(item["last_seen_at"]), float(item["typical_duration_seconds"]),
        MovementMode(item["typical_mode"]), float(item["confidence"]), FactStatus(item["status"]),
        tuple(item["duration_range_seconds"]), tuple(item["weekday_distribution"]))
        for item in raw.get("transitions", []))
    return ProfileState(int(raw.get("schema_version", SCHEMA_VERSION)), facts, transitions,
        statistics, samples, tuple(raw.get("seen_visit_ids", [])),
        tuple(raw.get("seen_transition_ids", [])), _dt(raw.get("last_computed_at")))


class ProfileStateRepository:
    def __init__(self, directory: Path):
        self.repository = JsonStateRepository(directory / "profile-state.json",
            empty_factory=empty_state, migrate=migrate_state, validate=validate_state)

    def load(self) -> ProfileState:
        raw = self.repository.load()
        return ProfileState() if raw.get("profile") is None else decode_state(raw["profile"])

    def save(self, state: ProfileState) -> None:
        self.repository.save({"schema_version": SCHEMA_VERSION, "profile": encode_state(state)})

    def reset(self) -> None:
        self.repository.save(empty_state())

    def forget_place(self, place_id: str) -> bool:
        state = self.load()
        exists = any(s.place_id == place_id for s in state.place_statistics)
        if not exists:
            return False
        transition_ids = {p.transition_id for p in state.transitions
                          if place_id in (p.from_place_id, p.to_place_id)}
        state = replace(state,
            facts=tuple(f for f in state.facts if f.subject_id != place_id
                        and f.subject_id not in transition_ids),
            transitions=tuple(p for p in state.transitions
                              if place_id not in (p.from_place_id, p.to_place_id)),
            place_statistics=tuple(s for s in state.place_statistics if s.place_id != place_id),
            transition_samples=tuple(s for s in state.transition_samples
                if place_id not in (s.from_place_id, s.to_place_id)))
        self.save(state)
        return True

    def export(self) -> dict[str, Any]:
        state = self.load()
        raw = encode_state(replace(state, transition_samples=()))
        raw.pop("seen_visit_ids", None)
        raw.pop("seen_transition_ids", None)
        return raw
