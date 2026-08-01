#!/usr/bin/env python3
"""Sanitized operational CLI for the movement engine."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from location_core.location_sources import LocationObservation
from location_core.movement import MovementConfig, MovementEngine
from location_core.movement_state import SCHEMA_VERSION, MovementStateRepository
from location_core.repository import CorruptStateError


def _direction(heading: float | None) -> str:
    if heading is None:
        return "unknown"
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int((heading + 22.5) // 45) % 8]


def _load_replay(path: Path) -> list[LocationObservation]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("synthetic") is not True:
        raise ValueError("replay must explicitly declare synthetic=true")
    values = raw.get("observations")
    if not isinstance(values, list):
        raise ValueError("observations must be a list")
    result = []
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            raise ValueError("observation must be an object")
        observed = datetime.fromisoformat(str(item["observed_at"]).replace("Z", "+00:00"))
        result.append(LocationObservation(
            source="synthetic-replay", device_id=str(item.get("device_id", "synthetic")),
            observed_at=observed, received_at=observed,
            latitude=float(item["latitude"]), longitude=float(item["longitude"]),
            accuracy_m=float(item.get("accuracy_m", 10)),
            speed_mps=float(item["speed_mps"]) if "speed_mps" in item else None,
            course_deg=float(item["course_deg"]) if "course_deg" in item else None,
            source_metadata={"replay_id": str(index)},
        ))
    return sorted(result, key=lambda value: (value.observed_at, value.observation_id))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="movement")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("diagnose")
    sub.add_parser("reset")
    replay = sub.add_parser("replay")
    replay.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    config = MovementConfig.from_env()
    repository = MovementStateRepository(config.state_dir)
    if args.command == "reset":
        repository.reset()
        print(json.dumps({"movement_state": "reset"}))
        return 0
    try:
        state = repository.load()
    except CorruptStateError as error:
        print(json.dumps({"valid": False, "quarantine": error.quarantine_path.name}))
        return 2
    if args.command == "diagnose":
        print(json.dumps({"valid": True, "schema_version": SCHEMA_VERSION,
                          "last_status": state.last_status.value if state.last_status else None,
                          "deduplication_ids": len(state.seen_ids),
                          "active_gap_count": state.active_segment.gap_count if state.active_segment else 0}))
        return 0
    if args.command == "status":
        movement = state.movement
        age = ((datetime.now(UTC) - movement.last_observed_at).total_seconds()
               if movement else None)
        print(json.dumps({"mode": movement.mode.value if movement else "unknown",
                          "confidence": movement.confidence if movement else 0,
                          "state_duration_seconds": ((movement.last_observed_at - movement.state_started_at).total_seconds() if movement else 0),
                          "speed_mps": movement.speed_mps if movement else None,
                          "direction": _direction(movement.heading_deg if movement else None),
                          "active_segment": state.active_segment.segment_id if state.active_segment else None,
                          "data_quality": movement.data_quality.value if movement else "invalid",
                          "last_observation_age_seconds": age}))
        return 0
    engine = MovementEngine(config, state)
    statuses: list[str] = []
    for observation in _load_replay(args.path):
        result = engine.process(observation)
        statuses.append(result.status.value)
    repository.save(engine.state)
    print(json.dumps({"processed": len(statuses), "statuses": statuses,
                      "events": "sanitized; inspect tests for deterministic contracts"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
