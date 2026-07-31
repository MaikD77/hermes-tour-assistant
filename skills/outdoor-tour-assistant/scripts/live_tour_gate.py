#!/usr/bin/env python3
"""Private, deterministic cron gate for active Telegram + OwnTracks location sessions."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from contracts import GateDecision, LocationSample  # noqa: E402

CORE_SCRIPTS = Path(__file__).resolve().parents[2] / "location-session-core" / "scripts"
if str(CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CORE_SCRIPTS))
from location_core.location_sources import (  # noqa: E402
    HttpOwnTracksReceiver,
    LocationObservation,
    LocationSourceResolver,
    OwnTracksLocationSource,
    TelegramLocationSource,
    parse_source_order,
)
from tour_runtime import TourProfile, TourRuntime  # noqa: E402
from tour_state import CorruptStateError  # noqa: E402

HERMES_HOME = Path(
    os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
).expanduser()
STATE_DIR = Path(
    os.environ.get("HERMES_TOUR_STATE_DIR", str(HERMES_HOME / "state"))
).expanduser()
SNAPSHOT = STATE_DIR / "telegram_live_locations.json"
STATE = STATE_DIR / "live_tour_assistant.json"
CHAT_ID = os.environ.get("HERMES_TOUR_CHAT_ID", "").strip()
MAX_LOCATION_AGE_SECONDS = float(
    os.environ.get("HERMES_TOUR_LOCATION_MAX_AGE_SECONDS", "300")
)
OWNTRACKS_URL = os.environ.get(
    "HERMES_OWNTRACKS_URL", "http://127.0.0.1:9090/location"
)
LOCATION_SOURCE_ORDER = os.environ.get("HERMES_LOCATION_SOURCE_ORDER", "owntracks,telegram")


class SnapshotError(RuntimeError):
    pass


def read_snapshot(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"locations": []}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SnapshotError("invalid_location_snapshot") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("locations", []), list):
        raise SnapshotError("invalid_location_snapshot")
    return payload


def select_location(
    snapshot: dict[str, Any],
    *,
    chat_id: str,
    now: float,
    max_age_seconds: float,
) -> LocationSample | None:
    matching = [
        item
        for item in snapshot.get("locations", [])
        if isinstance(item, dict) and str(item.get("chat_id", "")) == chat_id
    ]
    if not matching:
        return None
    valid: list[LocationSample] = []
    for item in matching:
        try:
            valid.append(
                LocationSample.from_mapping(
                    item,
                    expected_chat_id=chat_id,
                    now=now,
                    max_age_seconds=max_age_seconds,
                )
            )
        except ValueError:
            continue
    if not valid:
        raise SnapshotError("invalid_or_stale_location")
    return max(valid, key=lambda sample: sample.observed_at)


def fetch_owntracks_location(
    url: str, now: float, max_age_seconds: float
) -> LocationObservation | None:
    """Compatibility wrapper around the source-neutral OwnTracks adapter."""
    return OwnTracksLocationSource(HttpOwnTracksReceiver(url)).latest(
        now=datetime.fromtimestamp(now, UTC), max_age_seconds=max_age_seconds
    ).observation


def emit(decision: GateDecision) -> None:
    print(json.dumps(decision.to_cron_payload(), separators=(",", ":")))


def _optional_env_float(name: str) -> float | None:
    value = os.environ.get(name)
    return None if value is None or not value.strip() else float(value)


def _runtime() -> TourRuntime:
    profile = TourProfile(
        os.environ.get("HERMES_TOUR_ACTIVITY", "cycling"),
        locale=os.environ.get("HERMES_TOUR_LOCALE", "de-DE"),
        move_threshold_m=_optional_env_float("HERMES_TOUR_MOVE_THRESHOLD_M"),
        off_route_enter_m=_optional_env_float("HERMES_TOUR_OFF_ROUTE_ENTER_M"),
        off_route_exit_m=_optional_env_float("HERMES_TOUR_OFF_ROUTE_EXIT_M"),
        settlement_approach_m=_optional_env_float(
            "HERMES_TOUR_SETTLEMENT_APPROACH_M"
        ),
        finish_approach_m=_optional_env_float("HERMES_TOUR_FINISH_APPROACH_M"),
    )
    return TourRuntime(STATE, profile=profile)


def _operational_error(runtime: TourRuntime, code: str, now: float) -> None:
    try:
        emit(runtime.operational_decision(code, now=now))
    except CorruptStateError:
        runtime.repository.recover_empty()
        emit(runtime.operational_decision(code, now=now))


def main(now: float | None = None) -> None:
    current_time = time.time() if now is None else now
    try:
        runtime = _runtime()
    except ValueError:
        runtime = TourRuntime(STATE)
        _operational_error(runtime, "invalid_tour_profile", current_time)
        return
    if not CHAT_ID:
        _operational_error(runtime, "missing_chat_id", current_time)
        return
    if not 30 <= MAX_LOCATION_AGE_SECONDS <= 3600:
        _operational_error(runtime, "invalid_location_max_age", current_time)
        return

    try:
        order = parse_source_order(LOCATION_SOURCE_ORDER)
        resolver = LocationSourceResolver(
            {
                "owntracks": OwnTracksLocationSource(HttpOwnTracksReceiver(OWNTRACKS_URL)),
                "telegram": TelegramLocationSource(SNAPSHOT, CHAT_ID),
            },
            order,
        )
        sample = resolver.resolve(
            now=datetime.fromtimestamp(current_time, UTC),
            max_age_seconds=MAX_LOCATION_AGE_SECONDS,
        ).observation
    except ValueError:
        _operational_error(runtime, "invalid_location_source_order", current_time)
        return

    if sample is None:
        try:
            runtime.end_active_session(ended_at=current_time)
        except CorruptStateError:
            runtime.repository.recover_empty()
            emit(
                runtime.operational_decision(
                    "corrupt_state_recovered",
                    now=current_time,
                )
            )
            return
        emit(GateDecision(wake_agent=False))
        return
    try:
        emit(runtime.evaluate_gate(sample, now=current_time))
    except CorruptStateError:
        runtime.repository.recover_empty()
        emit(
            runtime.operational_decision(
                "corrupt_state_recovered",
                now=current_time,
            )
        )


if __name__ == "__main__":
    main()
