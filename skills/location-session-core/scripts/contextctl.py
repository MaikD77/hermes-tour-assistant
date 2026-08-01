#!/usr/bin/env python3
"""Coordinate-free CLI for deterministic current-context shadow mode."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any

from location_core.context import ContextConfig, CurrentContextEngine
from location_core.context_inputs import ContextInputLoader
from location_core.context_state import SCHEMA_VERSION, ContextStateRepository
from location_core.repository import CorruptStateError


def _summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {"status": snapshot["status"], "confidence": snapshot["overall_confidence"],
        "freshness": snapshot["freshness"],
        "movement": snapshot["movement_context"]["mode"],
        "place_id": snapshot["place_context"]["place_id"],
        "traits": [item["trait_type"] for item in snapshot["traits"]],
        "uncertainties": [item["code"] for item in snapshot["uncertainties"]],
        "computed_at": snapshot["computed_at"], "valid_until": snapshot["valid_until"]}


def _explain(snapshot: dict[str, Any]) -> str:
    lines = [f"Status: {snapshot['status']}",
        f"Confidence: {snapshot['overall_confidence']:.3f}",
        f"Freshness: {snapshot['freshness']}", "", "Known:",
        f"- movement: {snapshot['movement_context']['mode'] or 'unknown'}",
        f"- place: {snapshot['place_context']['place_id'] or 'none'}"]
    lines.extend(f"- trait: {item['trait_type']}={item['value']}" for item in snapshot["traits"])
    lines.append("\nUncertain:")
    lines.extend(f"- {item['code']}: {item['reason']}" for item in snapshot["uncertainties"])
    return "\n".join(lines)


def compute_context(config: ContextConfig, *, now: datetime,
                    loader: ContextInputLoader | None = None,
                    repository: ContextStateRepository | None = None) -> dict[str, Any]:
    """Execute the productive abstraction-only input path; injectable for integration tests."""
    input_loader = loader or ContextInputLoader(config)
    bundle = input_loader.load(now=now)
    engine = CurrentContextEngine(config)
    result = engine.compute(observation=bundle.observation,
        movement_state=bundle.movement_state, place_state=bundle.place_state,
        profile_state=bundle.profile_state, computed_at=now, input_issues=bundle.issues)
    (repository or ContextStateRepository(config.state_dir)).save(result.context)
    return engine.export(result.context)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="context")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "compute", "explain", "export", "diagnose", "reset"):
        sub.add_parser(command)
    args = parser.parse_args(argv)
    config = ContextConfig.from_env()
    repository = ContextStateRepository(config.state_dir)
    if args.command == "reset":
        repository.reset()
        print(json.dumps({"context_state": "reset"}))
        return 0
    if args.command == "compute":
        computed_snapshot = compute_context(
            config, now=datetime.now(UTC), repository=repository
        )
        print(json.dumps(_summary(computed_snapshot)))
        return 0
    try:
        snapshot = repository.load()
    except CorruptStateError as error:
        print(json.dumps({"valid": False, "quarantine": error.quarantine_path.name}))
        return 2
    if args.command == "diagnose":
        print(json.dumps({"valid": snapshot is not None, "schema_version": SCHEMA_VERSION,
            "input_availability": snapshot is not None,
            "input_freshness": snapshot["freshness"] if snapshot else "expired",
            "conflicts": [u["reason"] for u in snapshot["uncertainties"]
                if u["code"] == "conflicting_evidence"] if snapshot else [],
            "shadow_mode": True, "provider_calls": False,
            "context_engine_provider_calls": False,
            "location_source_resolution_on_compute": True, "delivery": False}))
        return 0
    if snapshot is None:
        print(json.dumps({"status": "unknown", "last_context": None}))
        return 1
    if args.command == "status":
        print(json.dumps(_summary(snapshot)))
    elif args.command == "export":
        print(json.dumps(snapshot, indent=2))
    elif args.command == "explain":
        print(_explain(snapshot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
