#!/usr/bin/env python3
"""Private, deterministic cron gate for active Telegram + OwnTracks location sessions."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from contracts import GateDecision, LocationSample  # noqa: E402
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


def fetch_owntracks_location(url: str, now: float, max_age_seconds: float) -> LocationSample | None:
    """Fetch the latest location from the OwnTracks receiver."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
    except (OSError, json.JSONDecodeError, urllib.error.URLError):
        return None
    if not isinstance(body, dict) or body.get("result") != "ok":
        return None
    try:
        lat = float(body["latitude"])
        lon = float(body["longitude"])
        observed_str = body.get("observed_at", "")
        stale = body.get("stale", True)
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(observed_str, str) or not observed_str:
        return None
    try:
        observed_at = (
            datetime.fromisoformat(observed_str.replace("Z", "+00:00"))
            .timestamp()
        )
    except (ValueError, TypeError):
        return None
    if stale or now - observed_at > max_age_seconds:
        return None
    digest = hashlib.sha256("owntracks\x00maik\x00iphone".encode()).hexdigest()[:20]
    return LocationSample(
        session_id=f"owntracks-{digest}",
        message_id="owntracks",
        observed_at=observed_at,
        expires_at=observed_at + max_age_seconds,
        lat=lat,
        lon=lon,
        source="owntracks",
    )


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

    # Try Telegram live-location first
    sample: LocationSample | None = None
    try:
        snapshot = read_snapshot(SNAPSHOT)
        sample = select_location(
            snapshot,
            chat_id=CHAT_ID,
            now=current_time,
            max_age_seconds=MAX_LOCATION_AGE_SECONDS,
        )
    except SnapshotError:
        pass

    # Fallback to OwnTracks if Telegram has no fresh location
    if sample is None:
        sample = fetch_owntracks_location(
            OWNTRACKS_URL,
            now=current_time,
            max_age_seconds=MAX_LOCATION_AGE_SECONDS,
        )

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