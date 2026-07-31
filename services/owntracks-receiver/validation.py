#!/usr/bin/env python3
"""OwnTracks payload validation and normalization.

Transport (HTTP) is separated from validation (this module) and from the
context store (models.py). This module is pure — no I/O, no logging.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

from models import Location

# OwnTracks trigger codes, see https://owntracks.org/booklet/features/triggers/
VALID_TRIGGERS = frozenset({"b", "c", "i", "p", "r", "u", "t", "s"})
# OwnTracks connection types: w=wifi, c=cellular, m=offline, o=other
VALID_CONN = frozenset({"w", "c", "m", "o"})

# Payloads older than this (or in the future beyond this skew) are rejected.
MAX_AGE_SECONDS = 24 * 60 * 60
MAX_FUTURE_SKEW_SECONDS = 60

# tst must be after 2015-01-01 to catch broken epochs (0, negative, ancient).
MIN_PLAUSIBLE_TST = 1420070400


class PayloadError(ValueError):
    """Raised when an OwnTracks payload fails validation."""


def _require_number(value: Any, field: str, *, allow_int_only: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadError(f"{field}: must be a number")
    if allow_int_only and not isinstance(value, int):
        raise PayloadError(f"{field}: must be an integer")
    number = float(value)
    if not math.isfinite(number):
        raise PayloadError(f"{field}: must be finite")
    return number


def validate_tst(tst: Any) -> float:
    """Reject implausible OwnTracks timestamps (epoch seconds)."""
    number = _require_number(tst, "tst")
    now = time.time()
    if number < MIN_PLAUSIBLE_TST:
        raise PayloadError("tst: timestamp is implausibly old (pre-2015)")
    if number > now + MAX_FUTURE_SKEW_SECONDS:
        raise PayloadError("tst: timestamp is in the future")
    if now - number > MAX_AGE_SECONDS:
        raise PayloadError("tst: timestamp is too old")
    return number


def normalize_location(payload: dict[str, Any]) -> Location:
    """Validate an OwnTracks location payload and return an immutable Location.

    Raises PayloadError on any invalid field. Unknown extra fields are
    ignored (forward compatibility).
    """
    _type = payload.get("_type")
    if _type != "location":
        raise PayloadError(f"_type: expected 'location', got {_type!r}")

    lat = _require_number(payload.get("lat"), "lat")
    lon = _require_number(payload.get("lon"), "lon")
    if not -90 <= lat <= 90:
        raise PayloadError("lat: must be between -90 and 90")
    if not -180 <= lon <= 180:
        raise PayloadError("lon: must be between -180 and 180")

    tst = validate_tst(payload.get("tst"))

    acc = payload.get("acc")
    accuracy_m = _require_number(acc, "acc") if acc is not None else None
    if accuracy_m is not None and accuracy_m < 0:
        raise PayloadError("acc: must be non-negative")

    alt = payload.get("alt")
    altitude_m = _require_number(alt, "alt") if alt is not None else None

    batt = payload.get("batt")
    battery_percent: int | None = None
    if batt is not None:
        battery_percent = int(_require_number(batt, "batt", allow_int_only=True))
        if not 0 <= battery_percent <= 100:
            raise PayloadError("batt: must be between 0 and 100")

    conn = payload.get("conn")
    if conn is not None:
        if not isinstance(conn, str) or conn not in VALID_CONN:
            raise PayloadError(f"conn: invalid value {conn!r}")
        connection_type: str | None = conn
    else:
        connection_type = None

    trigger = payload.get("t")
    if trigger is not None:
        if not isinstance(trigger, str) or trigger not in VALID_TRIGGERS:
            raise PayloadError(f"t: invalid trigger {trigger!r}")
        trigger_code: str | None = trigger
    else:
        trigger_code = None

    username = payload.get("username")
    device = payload.get("device")
    device_id = payload.get("device_id")
    if isinstance(device_id, str) and device_id.strip():
        resolved_device_id = device_id.strip()
    elif isinstance(username, str) and isinstance(device, str) and username.strip() and device.strip():
        resolved_device_id = f"{username.strip()}/{device.strip()}"
    else:
        resolved_device_id = "unknown"

    return Location(
        device_id=resolved_device_id,
        latitude=lat,
        longitude=lon,
        accuracy_m=accuracy_m,
        altitude_m=altitude_m,
        battery_percent=battery_percent,
        connection_type=connection_type,
        trigger=trigger_code,
        observed_at=datetime.fromtimestamp(tst, tz=timezone.utc),
        received_at=datetime.now(timezone.utc),
    )


def normalize_transition(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an OwnTracks transition payload (acknowledged, not stored)."""
    _type = payload.get("_type")
    if _type != "transition":
        raise PayloadError(f"_type: expected 'transition', got {_type!r}")
    validate_tst(payload.get("tst"))
    event = payload.get("event")
    if event is not None and not isinstance(event, str):
        raise PayloadError("event: must be a string")
    return {"event": event}