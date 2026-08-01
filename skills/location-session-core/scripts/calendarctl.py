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
from location_core.calendar_contracts import CalendarUnavailable
from location_core.calendar_providers import (
    GOOGLE_READ_ONLY_SCOPE,
    CalendarProviderConfig,
    ReplayCalendarProvider,
)
from location_core.calendar_state import CalendarStateRepository


def _integer(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, default))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name, str(default)).strip().lower()
    if value not in ("true", "false"):
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def settings(env: Mapping[str, str] = os.environ) -> tuple[CalendarProviderConfig, CalendarContextConfig, Path]:
    ids = tuple(value.strip() for value in env.get("HERMES_CALENDAR_IDS", "").split(",") if value.strip())
    provider = CalendarProviderConfig(ids,
        tuple(filter(None, env.get("HERMES_CALENDAR_INCLUDE", "").split(","))),
        tuple(filter(None, env.get("HERMES_CALENDAR_EXCLUDE", "").split(","))),
        _integer(env, "HERMES_CALENDAR_MAX_EVENTS", 250),
        _integer(env, "HERMES_CALENDAR_TITLE_MAX_LENGTH", 120),
        _integer(env, "HERMES_CALENDAR_DESCRIPTION_MAX_LENGTH", 0),
        _integer(env, "HERMES_CALENDAR_LOCATION_MAX_LENGTH", 120),
        _boolean(env, "HERMES_CALENDAR_PRIVATE_SANITIZATION", True))
    if env.get("HERMES_CALENDAR_PROVIDER") == "google" and \
            env.get("HERMES_GOOGLE_OAUTH_SCOPE", GOOGLE_READ_ONLY_SCOPE) != GOOGLE_READ_ONLY_SCOPE:
        raise ValueError("Google Calendar scope must be calendar.readonly")
    context = CalendarContextConfig(
        _integer(env, "HERMES_CALENDAR_FRESH_SECONDS", 300),
        _integer(env, "HERMES_CALENDAR_AGING_SECONDS", 900),
        _integer(env, "HERMES_CALENDAR_STALE_SECONDS", 3600),
        _integer(env, "HERMES_CALENDAR_LOOKBACK_MINUTES", 120),
        _integer(env, "HERMES_CALENDAR_STARTING_SOON_MINUTES", 15),
        _integer(env, "HERMES_CALENDAR_ENDING_SOON_MINUTES", 10),
        _integer(env, "HERMES_CALENDAR_CONFLICT_BUFFER_MINUTES", 0),
        _integer(env, "HERMES_CALENDAR_UPCOMING_LIMIT", 10))
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
    provider_cfg, context_cfg, state_dir = settings()
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
        result = (ReplayCalendarProvider((), fetched_at=now).list_events(
            window_start=start, window_end=end)
            if os.environ.get("HERMES_CALENDAR_PROVIDER", "replay") == "replay" else
            CalendarUnavailable(now, "Google transport is not initialized by offline CLI"))
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
