#!/usr/bin/env python3
"""Coordinate-free cron gate for an active personal city walk."""

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

CORE_SCRIPTS = Path(
    os.environ.get(
        "HERMES_LOCATION_CORE_DIR",
        str(Path(__file__).resolve().parents[2] / "location-session-core" / "scripts"),
    )
).expanduser()
if str(CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CORE_SCRIPTS))

from city_runtime import CityRuntime  # noqa: E402
from city_state import CorruptStateError  # noqa: E402
from location_core.contracts import GateDecision, LocationSample  # noqa: E402
from location_core.location_sources import (  # noqa: E402
    HttpOwnTracksReceiver,
    LocationSourceResolver,
    OwnTracksLocationSource,
    TelegramLocationSource,
    canonical_device_id_from_env,
    parse_source_order,
)

HERMES_HOME = Path(
    os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
).expanduser()
STATE_DIR = Path(
    os.environ.get("HERMES_CITY_GUIDE_STATE_DIR", str(HERMES_HOME / "state"))
).expanduser()
SNAPSHOT = STATE_DIR / "telegram_live_locations.json"
STATE = STATE_DIR / "city_guide_state.json"
CHAT_ID = os.environ.get(
    "HERMES_CITY_GUIDE_CHAT_ID",
    os.environ.get("HERMES_TOUR_CHAT_ID", ""),
).strip()
MAX_LOCATION_AGE_SECONDS = float(
    os.environ.get("HERMES_CITY_GUIDE_LOCATION_MAX_AGE_SECONDS", "300")
)
OWNTRACKS_URL = os.environ.get("HERMES_OWNTRACKS_URL", "http://127.0.0.1:9090/location")
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


def emit(decision: GateDecision) -> None:
    print(json.dumps(decision.to_cron_payload(), separators=(",", ":")))


def _operational_error(runtime: CityRuntime, code: str, now: float) -> None:
    try:
        emit(runtime.operational_decision(code, now=now))
    except CorruptStateError:
        runtime.repository.recover_empty()
        emit(runtime.operational_decision(code, now=now))


def build_location_resolver() -> LocationSourceResolver:
    """Build both productive adapters with one explicitly configured stream ID."""
    device_id = canonical_device_id_from_env()
    return LocationSourceResolver(
        {
            "owntracks": OwnTracksLocationSource(
                HttpOwnTracksReceiver(OWNTRACKS_URL), canonical_device_id=device_id
            ),
            "telegram": TelegramLocationSource(
                SNAPSHOT, CHAT_ID, canonical_device_id=device_id
            ),
        },
        parse_source_order(LOCATION_SOURCE_ORDER),
    )
def main(now: float | None = None) -> None:
    current_time = time.time() if now is None else now
    runtime = CityRuntime(STATE)
    if not CHAT_ID:
        _operational_error(runtime, "missing_chat_id", current_time)
        return
    if not 30 <= MAX_LOCATION_AGE_SECONDS <= 3600:
        _operational_error(runtime, "invalid_location_max_age", current_time)
        return
    try:
        resolver = build_location_resolver()
        sample = resolver.resolve(
            now=datetime.fromtimestamp(current_time, UTC),
            max_age_seconds=MAX_LOCATION_AGE_SECONDS,
        ).observation
    except ValueError as error:
        code = (
            "invalid_canonical_device_id"
            if "HERMES_LOCATION_CANONICAL_DEVICE_ID" in str(error)
            else "invalid_location_source_order"
        )
        _operational_error(runtime, code, current_time)
        return
    if sample is None:
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
