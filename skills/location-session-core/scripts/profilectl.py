#!/usr/bin/env python3
"""Sanitized CLI for the shadow-mode mobility profile."""

from __future__ import annotations

import argparse
import json
import sys

from location_core.place import PlaceConfig
from location_core.place_state import PlaceStateRepository
from location_core.profile import FactStatus, MobilityProfileEngine, ProfileConfig
from location_core.profile_state import (
    SCHEMA_VERSION,
    ProfileRebuildRequired,
    ProfileStateRepository,
)
from location_core.repository import CorruptStateError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="profile")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "facts", "transitions", "export", "rebuild", "reset", "diagnose"):
        sub.add_parser(command)
    explain = sub.add_parser("explain")
    explain.add_argument("fact_id")
    forget = sub.add_parser("forget-place")
    forget.add_argument("place_id")
    args = parser.parse_args(argv)
    config = ProfileConfig.from_env()
    repository = ProfileStateRepository(config.state_dir)
    if args.command == "reset":
        repository.reset()
        print(json.dumps({"profile_state": "reset"}))
        return 0
    if args.command == "forget-place":
        forgotten = repository.forget_place(args.place_id)
        print(json.dumps({"place_id": args.place_id, "forgotten": forgotten}))
        return 0 if forgotten else 1
    if args.command == "rebuild":
        places = PlaceStateRepository(PlaceConfig.from_env().state_dir).load()
        engine = MobilityProfileEngine(config)
        state = engine.rebuild(places.visits)
        repository.save(state)
        print(json.dumps({"processed_visits": len(places.visits), "facts": len(state.facts)}))
        return 0
    try:
        state = repository.load()
    except ProfileRebuildRequired:
        print(json.dumps({"valid": False, "rebuild_required": True}))
        return 3
    except CorruptStateError as error:
        print(json.dumps({"valid": False, "quarantine": error.quarantine_path.name}))
        return 2
    if args.command == "diagnose":
        print(json.dumps({"valid": True, "schema_version": SCHEMA_VERSION,
            "shadow_mode": True, "raw_locations": False,
            "deduplication_ids": len(state.seen_visit_ids)}))
    elif args.command == "status":
        counts = {status.value: sum(f.status is status for f in state.facts) for status in FactStatus}
        print(json.dumps({"facts": len(state.facts), **counts,
            "transition_patterns": len(state.transitions),
            "last_updated_at": state.last_computed_at.isoformat() if state.last_computed_at else None}))
    elif args.command == "facts":
        print(json.dumps([{"fact_id": f.fact_id, "type": f.fact_type.value,
            "subject_id": f.subject_id, "status": f.status.value,
            "confidence": f.confidence, "sample_count": f.sample_count} for f in state.facts]))
    elif args.command == "transitions":
        print(json.dumps([{"from_place_id": p.from_place_id, "to_place_id": p.to_place_id,
            "sample_count": p.sample_count, "typical_duration_seconds": p.typical_duration_seconds,
            "typical_mode": p.typical_mode.value, "confidence": p.confidence}
            for p in state.transitions]))
    elif args.command == "explain":
        print(MobilityProfileEngine(config, state).explain(args.fact_id))
    elif args.command == "export":
        print(json.dumps(repository.export(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
