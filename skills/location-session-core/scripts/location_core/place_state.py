"""Private, precision-minimized persistence for the Place & Stay engine."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .movement import DataQuality, MovementMode
from .place import (
    CandidateAccumulator,
    Place,
    PlaceEngineState,
    PlaceStatus,
    PlaceVisit,
    Stay,
    StayStatus,
)
from .repository import JsonStateRepository

SCHEMA_VERSION = 2


def empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "engine": None}


def migrate_state(raw: dict[str, Any]) -> dict[str, Any]:
    version = raw.get("schema_version", 0)
    if version == 0 and not raw.get("engine"):
        return empty_state()
    if version == 1:
        engine = raw.get("engine")
        if not isinstance(engine, dict):
            raise ValueError("legacy place engine state must be an object")
        engine = dict(engine)
        engine["schema_version"] = SCHEMA_VERSION
        for name in ("candidate", "active_stay"):
            value = engine.get(name)
            if not isinstance(value, dict):
                continue
            accumulator = dict(value)
            quality = accumulator.get("stay", {}).get("data_quality")
            count = int(accumulator.get("stay", {}).get("observation_count", 0))
            accumulator.setdefault("good_quality_count", count if quality == "good" else 0)
            accumulator.setdefault("limited_quality_count", count if quality == "limited" else 0)
            accumulator.setdefault("poor_quality_count", count if quality == "poor" else 0)
            accumulator["departure_observed_at"] = accumulator.pop("outside_since", None)
            accumulator["departure_mode"] = accumulator.pop("outside_mode", None)
            engine[name] = accumulator
        return {"schema_version": SCHEMA_VERSION, "engine": engine}
    if version != SCHEMA_VERSION:
        raise ValueError("unsupported place state schema")
    return raw


def validate_state(raw: dict[str, Any]) -> None:
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid place state schema")
    if raw.get("engine") is not None and not isinstance(raw["engine"], dict):
        raise ValueError("place engine state must be an object")


def _encode_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return getattr(value, "value")
    raise TypeError(type(value).__name__)


def encode_engine(state: PlaceEngineState, precision: int = 4) -> dict[str, Any]:
    """Serialize normalized aggregates only; coordinates are quantized (~11 m latitude)."""
    raw: dict[str, Any] = json.loads(json.dumps(asdict(state), default=_encode_default))

    def minimize_stay(stay: dict[str, Any]) -> None:
        stay["centroid"] = [round(float(value), precision) for value in stay["centroid"]]

    for place in raw["places"]:
        place["centroid"] = [round(float(value), precision) for value in place["centroid"]]
    for name in ("candidate", "active_stay"):
        accumulator = raw.get(name)
        if accumulator:
            minimize_stay(accumulator["stay"])
            count = int(accumulator["stay"]["observation_count"])
            accumulator["latitude_sum"] = accumulator["stay"]["centroid"][0] * count
            accumulator["longitude_sum"] = accumulator["stay"]["centroid"][1] * count
    return raw


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return result


def _required_dt(value: str) -> datetime:
    result = _dt(value)
    if result is None:
        raise ValueError("timestamp is required")
    return result


def _stay(raw: dict[str, Any]) -> Stay:
    return Stay(raw["stay_id"], raw.get("place_id"), _required_dt(raw["started_at"]),
                _dt(raw.get("ended_at")), StayStatus(raw["status"]),
                float(raw["duration_seconds"]), int(raw["observation_count"]),
                (float(raw["centroid"][0]), float(raw["centroid"][1])),
                float(raw["radius_m"]), float(raw["confidence"]),
                DataQuality(raw["data_quality"]),
                MovementMode(raw["arrival_mode"]) if raw.get("arrival_mode") else None,
                MovementMode(raw["departure_mode"]) if raw.get("departure_mode") else None)


def _accumulator(raw: dict[str, Any] | None) -> CandidateAccumulator | None:
    if not raw:
        return None
    return CandidateAccumulator(
        _stay(raw["stay"]), float(raw["latitude_sum"]), float(raw["longitude_sum"]),
        int(raw.get("good_quality_count", 0)), int(raw.get("limited_quality_count", 0)),
        int(raw.get("poor_quality_count", 0)), _dt(raw.get("departure_observed_at")),
        MovementMode(raw["departure_mode"]) if raw.get("departure_mode") else None,
    )


def decode_engine(raw: dict[str, Any]) -> PlaceEngineState:
    places = tuple(Place(item["place_id"], (float(item["centroid"][0]),
                         float(item["centroid"][1])), float(item["radius_m"]),
                         float(item["confidence"]), _required_dt(item["first_seen_at"]),
                         _required_dt(item["last_seen_at"]), int(item["visit_count"]),
                         float(item["total_dwell_seconds"]),
                         float(item["typical_dwell_seconds"]),
                         DataQuality(item["data_quality"]), PlaceStatus(item["status"]))
                   for item in raw.get("places", []))
    visits = tuple(PlaceVisit(item["visit_id"], item["place_id"], item["stay_id"],
                             _required_dt(item["arrived_at"]), _required_dt(item["departed_at"]),
                             float(item["duration_seconds"]),
                             MovementMode(item["arrival_mode"]) if item.get("arrival_mode") else None,
                             MovementMode(item["departure_mode"]) if item.get("departure_mode") else None,
                             float(item["confidence"])) for item in raw.get("visits", []))
    return PlaceEngineState(int(raw.get("schema_version", SCHEMA_VERSION)),
                            _accumulator(raw.get("candidate")),
                            _accumulator(raw.get("active_stay")), places, visits,
                            tuple(raw.get("seen_ids", [])),
                            tuple(raw.get("emitted_event_ids", [])),
                            raw.get("last_observation_id"), _dt(raw.get("last_observed_at")))


class PlaceStateRepository:
    def __init__(self, directory: Path, *, precision: int = 4):
        self.precision = precision
        self.repository = JsonStateRepository(directory / "place-state.json",
                                              empty_factory=empty_state,
                                              migrate=migrate_state, validate=validate_state)

    def load(self) -> PlaceEngineState:
        raw = self.repository.load()
        return PlaceEngineState() if raw.get("engine") is None else decode_engine(raw["engine"])

    def save(self, state: PlaceEngineState) -> None:
        self.repository.save({"schema_version": SCHEMA_VERSION,
                              "engine": encode_engine(state, self.precision)})

    def forget(self, place_id: str) -> bool:
        state = self.load()
        exists = any(place.place_id == place_id for place in state.places)
        if not exists:
            return False
        candidate = state.candidate
        active = state.active_stay
        if candidate and candidate.stay.place_id == place_id:
            candidate = None
        if active and active.stay.place_id == place_id:
            active = None
        self.save(PlaceEngineState(state.schema_version, candidate, active,
                  tuple(p for p in state.places if p.place_id != place_id),
                  tuple(v for v in state.visits if v.place_id != place_id), state.seen_ids,
                  state.emitted_event_ids, state.last_observation_id, state.last_observed_at))
        return True

    def reset(self) -> None:
        self.repository.save(empty_state())
