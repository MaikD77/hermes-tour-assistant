#!/usr/bin/env python3
"""Sanitized operational CLI for deterministic private places."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from location_core.location_sources import LocationObservation
from location_core.place import PlaceConfig, PlaceEngine
from location_core.place_state import SCHEMA_VERSION, PlaceStateRepository
from location_core.repository import CorruptStateError


def _replay(path: Path) -> list[LocationObservation]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("synthetic") is not True:
        raise ValueError("replay must explicitly declare synthetic=true")
    observations = raw.get("observations")
    if not isinstance(observations, list):
        raise ValueError("observations must be a list")
    result = []
    for index, item in enumerate(observations):
        if not isinstance(item, dict):
            raise ValueError("observation must be an object")
        observed = datetime.fromisoformat(str(item["observed_at"]).replace("Z", "+00:00"))
        result.append(LocationObservation(
            source="synthetic-replay", device_id=str(item.get("device_id", "synthetic")),
            observed_at=observed, received_at=observed,
            latitude=float(item["latitude"]), longitude=float(item["longitude"]),
            accuracy_m=float(item.get("accuracy_m", 10)),
            speed_mps=float(item["speed_mps"]) if "speed_mps" in item else None,
            source_metadata={"replay_id": str(index)},
        ))
    return sorted(result, key=lambda item: (item.observed_at, item.observation_id))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="place")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "list", "visits", "diagnose", "reset"):
        sub.add_parser(command)
    forget = sub.add_parser("forget")
    forget.add_argument("place_id")
    replay = sub.add_parser("replay")
    replay.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    config = PlaceConfig.from_env()
    repository = PlaceStateRepository(config.state_dir, precision=config.centroid_precision)
    if args.command == "reset":
        repository.reset()
        print(json.dumps({"place_state": "reset"}))
        return 0
    if args.command == "forget":
        forgotten = repository.forget(args.place_id)
        print(json.dumps({"place_id": args.place_id, "forgotten": forgotten}))
        return 0 if forgotten else 1
    try:
        state = repository.load()
    except CorruptStateError as error:
        print(json.dumps({"valid": False, "quarantine": error.quarantine_path.name}))
        return 2
    if args.command == "diagnose":
        print(json.dumps({"valid": True, "schema_version": SCHEMA_VERSION,
                          "active_stay": state.active_stay is not None,
                          "candidate_stay": state.candidate is not None,
                          "known_places": len(state.places), "retained_visits": len(state.visits),
                          "deduplication_ids": len(state.seen_ids),
                          "spatial_data": "redacted"}))
        return 0
    if args.command == "status":
        stay = state.active_stay.stay if state.active_stay else None
        print(json.dumps({"active_stay": stay is not None,
                          "place_id": stay.place_id if stay else None,
                          "duration_seconds": stay.duration_seconds if stay else 0,
                          "confidence": stay.confidence if stay else 0,
                          "data_quality": stay.data_quality.value if stay else "invalid",
                          "arrival_mode": stay.arrival_mode.value if stay and stay.arrival_mode else None}))
        return 0
    if args.command == "list":
        print(json.dumps([{"place_id": p.place_id, "status": p.status.value,
                           "visit_count": p.visit_count,
                           "first_seen_at": p.first_seen_at.isoformat(),
                           "last_seen_at": p.last_seen_at.isoformat(),
                           "total_dwell_seconds": p.total_dwell_seconds,
                           "confidence": p.confidence} for p in state.places]))
        return 0
    if args.command == "visits":
        print(json.dumps([{"visit_id": v.visit_id, "place_id": v.place_id,
                           "stay_id": v.stay_id, "arrived_at": v.arrived_at.isoformat(),
                           "departed_at": v.departed_at.isoformat(),
                           "duration_seconds": v.duration_seconds,
                           "arrival_mode": v.arrival_mode.value if v.arrival_mode else None,
                           "departure_mode": v.departure_mode.value if v.departure_mode else None,
                           "confidence": v.confidence} for v in state.visits]))
        return 0
    engine = PlaceEngine(config, state)
    statuses: list[str] = []
    event_types: list[str] = []
    for observation in _replay(args.path):
        result = engine.process(observation)
        statuses.append(result.status.value)
        event_types.extend(event.event_type.value for event in result.events)
    repository.save(engine.state)
    print(json.dumps({"processed": len(statuses), "statuses": statuses,
                      "events": event_types}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
