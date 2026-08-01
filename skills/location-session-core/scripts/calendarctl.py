#!/usr/bin/env python3
"""Privacy-preserving read-only calendar context CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from location_core.calendar_context import CalendarContextConfig, CalendarContextEngine
from location_core.calendar_factory import (
    build_calendar_provider,
    context_config_from_env,
    provider_config_from_env,
)
from location_core.calendar_providers import CalendarProviderConfig
from location_core.calendar_state import CalendarStateRepository


def _integer(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, default))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def settings(env: Mapping[str, str] = os.environ) -> tuple[CalendarProviderConfig, CalendarContextConfig, Path]:
    provider = provider_config_from_env(env)
    context = context_config_from_env(env)
    state = Path(env.get("HERMES_CALENDAR_STATE_DIR", str(Path.home() / ".local/state/hermes/calendar")))
    return provider, context, state


def _summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    next_event = snapshot.get("next_event")
    return {"status": snapshot["status"], "freshness": snapshot["freshness"],
        "last_fetch": snapshot["computed_at"],
        "event_count": len(snapshot["current_events"]) + len(snapshot["upcoming_events"]),
        "current": bool(snapshot["current_events"]),
        "next_event": next_event["event_id"] if next_event else None,
        "conflicts": len(snapshot["conflicts"]), "confidence": snapshot["confidence"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="calendar")
    parser.add_argument("command", choices=("status", "fetch", "current", "upcoming",
        "conflicts", "explain", "export", "diagnose", "reset"))
    args = parser.parse_args(argv)
    try:
        provider_cfg, context_cfg, state_dir = settings()
    except ValueError:
        print(json.dumps({"status": "invalid", "reason": "calendar configuration is invalid"}))
        return 2
    repository = CalendarStateRepository(state_dir)
    if args.command == "reset":
        repository.reset()
        print(json.dumps({"calendar_state": "reset"}))
        return 0
    if args.command == "diagnose":
        credential = os.environ.get("HERMES_GOOGLE_CREDENTIALS_FILE", "")
        permission_safe = (not credential or (Path(credential).is_file() and
                           Path(credential).stat().st_mode & 0o077 == 0))
        print(json.dumps({"provider": os.environ.get("HERMES_CALENDAR_PROVIDER", "replay"),
            "configured": bool(provider_cfg.calendar_ids), "credentials_referenced": bool(credential),
            "credential_permissions_private": permission_safe,
            "scope": "calendar.readonly", "pagination_complete": True,
            "shadow_mode": True, "delivery": False}))
        return 0
    if args.command == "fetch":
        now = datetime.now(UTC)
        lookahead = _integer(os.environ, "HERMES_CALENDAR_LOOKAHEAD_HOURS", 24)
        start = now - timedelta(minutes=context_cfg.recent_minutes)
        end = now + timedelta(hours=lookahead)
        # Replay is intentionally empty unless a caller injects synthetic fixtures in Python.
        result = build_calendar_provider(env=os.environ, config=provider_cfg,
            clock=lambda: now).list_events(window_start=start, window_end=end)
        context = CalendarContextEngine(context_cfg).compute(provider_result=result,
            computed_at=now, window_start=start, window_end=end)
        repository.save(context)
        snapshot = repository.load()
        assert snapshot is not None
        print(json.dumps(_summary(snapshot)))
        return 0
    snapshot = repository.load()
    if snapshot is None:
        print(json.dumps({"status": "unknown", "last_calendar_context": None}))
        return 1
    if args.command == "status":
        output: Any = _summary(snapshot)
    elif args.command == "current":
        output = snapshot["current_events"]
    elif args.command == "upcoming":
        output = snapshot["upcoming_events"]
    elif args.command == "conflicts":
        output = snapshot["conflicts"]
    elif args.command == "explain":
        output = {"evidence": snapshot["evidence"], "uncertainties": snapshot["uncertainties"]}
    else:
        output = snapshot
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
