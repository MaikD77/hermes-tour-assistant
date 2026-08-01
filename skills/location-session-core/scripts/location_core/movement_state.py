"""Validated private persistence for the bounded movement read model."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .movement import (
    DataQuality,
    EngineState,
    MovementMode,
    MovementSegment,
    MovementState,
    ObservationFeature,
    ProcessingStatus,
    SegmentAccumulator,
    SegmentStatus,
)
from .repository import JsonStateRepository

SCHEMA_VERSION = 2


def _dt(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return result


def _optional_dt(value: object) -> datetime | None:
    return None if value is None else _dt(value)


def empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "engine": None}


def migrate_state(raw: dict[str, Any]) -> dict[str, Any]:
    version = raw.get("schema_version")
    if version in {0, 1}:
        engine = raw.get("engine")
        if isinstance(engine, dict):
            engine = dict(engine)
            engine["schema_version"] = SCHEMA_VERSION
            engine.setdefault("pending_origin_latitude", None)
            engine.setdefault("pending_origin_longitude", None)
            if engine.get("active_segment") and not engine.get("segment_accumulator"):
                recent = engine.get("recent", [])
                if not isinstance(recent, list) or not recent:
                    raise ValueError("legacy active segment lacks accumulator origin")
                origin = recent[0]
                headings = [item.get("heading_deg") for item in recent
                            if item.get("heading_deg") is not None]
                import math

                engine["segment_accumulator"] = {
                    "start_latitude": origin["latitude"],
                    "start_longitude": origin["longitude"],
                    "distance_m": engine["active_segment"].get("distance_m", 0),
                    "maximum_speed_mps": engine["active_segment"].get(
                        "maximum_speed_mps", 0
                    ),
                    "heading_x_sum": sum(math.cos(math.radians(value)) for value in headings),
                    "heading_y_sum": sum(math.sin(math.radians(value)) for value in headings),
                    "heading_count": len(headings),
                }
        return {"schema_version": SCHEMA_VERSION, "engine": engine}
    if version != SCHEMA_VERSION:
        raise ValueError("unsupported movement state schema")
    return raw


def validate_state(raw: dict[str, Any]) -> None:
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid movement state schema")
    if raw.get("engine") is not None and not isinstance(raw["engine"], dict):
        raise ValueError("engine state must be an object")


def _encode(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return getattr(value, "value")
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot encode {type(value).__name__}")


def encode_engine(state: EngineState) -> dict[str, Any]:
    """Serialize only bounded normalized features, never source payloads."""
    import json

    return json.loads(json.dumps(asdict(state), default=_encode))


def decode_engine(raw: dict[str, Any]) -> EngineState:
    movement_raw = raw.get("movement")
    movement = None
    if isinstance(movement_raw, dict):
        movement = MovementState(
            MovementMode(movement_raw["mode"]), float(movement_raw["confidence"]),
            _dt(movement_raw["state_started_at"]), _dt(movement_raw["last_observed_at"]),
            movement_raw.get("speed_mps"), movement_raw.get("heading_deg"),
            int(movement_raw["observation_count"]), tuple(movement_raw["evidence"]),
            DataQuality(movement_raw["data_quality"]),
        )
    segment_raw = raw.get("active_segment")
    segment = None
    if isinstance(segment_raw, dict):
        segment = MovementSegment(
            segment_raw["segment_id"], MovementMode(segment_raw["mode"]),
            _dt(segment_raw["started_at"]), _optional_dt(segment_raw.get("ended_at")),
            SegmentStatus(segment_raw["status"]), segment_raw["start_observation_id"],
            segment_raw["end_observation_id"], int(segment_raw["observation_count"]),
            float(segment_raw["duration_seconds"]), float(segment_raw["distance_m"]),
            float(segment_raw["displacement_m"]), float(segment_raw["average_speed_mps"]),
            float(segment_raw["maximum_speed_mps"]), segment_raw.get("dominant_heading_deg"),
            float(segment_raw["heading_stability"]), float(segment_raw["confidence"]),
            DataQuality(segment_raw["data_quality"]), int(segment_raw["gap_count"]),
        )
    accumulator_raw = raw.get("segment_accumulator")
    accumulator = None
    if isinstance(accumulator_raw, dict):
        accumulator = SegmentAccumulator(
            float(accumulator_raw["start_latitude"]),
            float(accumulator_raw["start_longitude"]),
            float(accumulator_raw.get("distance_m", 0)),
            float(accumulator_raw.get("maximum_speed_mps", 0)),
            float(accumulator_raw.get("heading_x_sum", 0)),
            float(accumulator_raw.get("heading_y_sum", 0)),
            int(accumulator_raw.get("heading_count", 0)),
        )
    recent = tuple(
        ObservationFeature(item["observation_id"], item["device_id"], _dt(item["observed_at"]),
                           float(item["latitude"]), float(item["longitude"]),
                           float(item["speed_mps"]), item.get("heading_deg"),
                           DataQuality(item["quality"]))
        for item in raw.get("recent", [])
    )
    return EngineState(
        int(raw.get("schema_version", SCHEMA_VERSION)), movement, segment, accumulator, recent,
        tuple(raw.get("seen_ids", [])),
        MovementMode(raw["pending_mode"]) if raw.get("pending_mode") else None,
        _optional_dt(raw.get("pending_since")), int(raw.get("pending_count", 0)),
        raw.get("pending_origin_latitude"), raw.get("pending_origin_longitude"),
        _optional_dt(raw.get("last_transition_at")),
        ProcessingStatus(raw["last_status"]) if raw.get("last_status") else None,
    )


class MovementStateRepository:
    def __init__(self, directory: Path):
        self.repository = JsonStateRepository(
            directory / "movement-state.json", empty_factory=empty_state,
            migrate=migrate_state, validate=validate_state,
        )

    def load(self) -> EngineState:
        raw = self.repository.load()
        engine = raw.get("engine")
        return EngineState() if engine is None else decode_engine(engine)

    def save(self, state: EngineState) -> None:
        self.repository.save({"schema_version": SCHEMA_VERSION, "engine": encode_engine(state)})

    def reset(self) -> None:
        self.repository.save(empty_state())
