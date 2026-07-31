"""Source-neutral location observations and deterministic source resolution."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


def _number(name: str, value: object, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


@dataclass(frozen=True)
class LocationObservation:
    """Immutable canonical value passed to every location consumer."""

    source: str
    device_id: str
    observed_at: float
    received_at: float
    latitude: float
    longitude: float
    accuracy_m: float | None = None
    altitude_m: float | None = None
    speed_mps: float | None = None
    course_deg: float | None = None
    battery_percent: float | None = None
    trigger: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source is required")
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise ValueError("device_id is required")
        for name in ("trigger",):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
        for name in ("observed_at", "received_at", "latitude", "longitude"):
            _number(name, getattr(self, name))
        for name in ("accuracy_m", "altitude_m", "speed_mps", "course_deg", "battery_percent"):
            _number(name, getattr(self, name), optional=True)
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude out of range")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude out of range")
        if self.accuracy_m is not None and self.accuracy_m < 0:
            raise ValueError("accuracy_m must be non-negative")
        if self.speed_mps is not None and self.speed_mps < 0:
            raise ValueError("speed_mps must be non-negative")
        if self.course_deg is not None and not 0 <= self.course_deg < 360:
            raise ValueError("course_deg must be in [0, 360)")
        if self.battery_percent is not None and not 0 <= self.battery_percent <= 100:
            raise ValueError("battery_percent must be in [0, 100]")
        if self.observed_at <= 0 or self.received_at <= 0:
            raise ValueError("timestamps must be positive")
        if self.observed_at > self.received_at + 60:
            raise ValueError("observed_at is implausibly far in the future")

    @property
    def lat(self) -> float:
        return self.latitude

    @property
    def lon(self) -> float:
        return self.longitude

    @property
    def session_id(self) -> str:
        digest = hashlib.sha256(f"{self.source}\0{self.device_id}".encode()).hexdigest()[:20]
        return f"{self.source}-{digest}"

    @property
    def message_id(self) -> str:
        return self.device_id

    @property
    def expires_at(self) -> float:
        return self.observed_at


class LocationStatus(str, Enum):
    OK = "ok"
    NOT_AVAILABLE = "not_available"
    STALE = "stale"
    INVALID = "invalid"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class LocationSourceResult:
    status: LocationStatus
    observation: LocationObservation | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if (self.status is LocationStatus.OK) != (self.observation is not None):
            raise ValueError("only an ok result may contain an observation")


class LocationSource(Protocol):
    name: str

    def latest(self, *, now: float, max_age_seconds: float) -> LocationSourceResult: ...


class OwnTracksReceiver(Protocol):
    def latest_payload(self) -> Mapping[str, Any] | None: ...


class HttpOwnTracksReceiver:
    """Narrow receiver API client; it neither retries nor knows SQLite."""

    def __init__(self, url: str, *, timeout_seconds: float = 5) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def latest_payload(self) -> Mapping[str, Any] | None:
        request = urllib.request.Request(self.url)
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            value = json.loads(response.read().decode("utf-8"))
        return value if isinstance(value, dict) else None


def _iso_timestamp(value: object) -> float:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.timestamp()


class OwnTracksLocationSource:
    name = "owntracks"

    def __init__(self, receiver: OwnTracksReceiver) -> None:
        self.receiver = receiver

    def latest(self, *, now: float, max_age_seconds: float) -> LocationSourceResult:
        try:
            payload = self.receiver.latest_payload()
        except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError):
            return LocationSourceResult(LocationStatus.UNREACHABLE, detail="receiver_unreachable")
        if payload is None or payload.get("result") == "not_found":
            return LocationSourceResult(LocationStatus.NOT_AVAILABLE)
        if payload.get("result") != "ok":
            return LocationSourceResult(LocationStatus.INVALID, detail="invalid_receiver_response")
        try:
            observed = _iso_timestamp(payload.get("observed_at"))
            received_raw = payload.get("received_at")
            received = _iso_timestamp(received_raw) if received_raw is not None else now
            observation = LocationObservation(
                source="owntracks", device_id=_required_string(payload, "device_id"),
                observed_at=observed, received_at=received,
                latitude=_required_number(payload, "latitude"),
                longitude=_required_number(payload, "longitude"),
                accuracy_m=_optional_number(payload, "accuracy_m"),
                altitude_m=_optional_number(payload, "altitude_m"),
                speed_mps=_optional_number(payload, "speed_mps"),
                course_deg=_optional_number(payload, "course_deg"),
                battery_percent=_optional_number(payload, "battery_percent"),
                trigger=_optional_string(payload, "trigger"),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return LocationSourceResult(LocationStatus.INVALID, detail="invalid_observation")
        if payload.get("stale") is True or now - observation.observed_at > max_age_seconds:
            return LocationSourceResult(LocationStatus.STALE)
        return LocationSourceResult(LocationStatus.OK, observation)


def _required_string(value: Mapping[str, Any], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _required_number(value: Mapping[str, Any], key: str) -> float:
    result = _number(key, value[key])
    assert result is not None
    return result


def _optional_number(value: Mapping[str, Any], key: str) -> float | None:
    return _number(key, value.get(key), optional=True)


def _optional_string(value: Mapping[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is not None and not isinstance(item, str):
        raise ValueError(f"{key} must be a string")
    return item


class TelegramLocationSource:
    name = "telegram"

    def __init__(self, snapshot_path: Path, chat_id: str) -> None:
        self.snapshot_path = snapshot_path
        self.chat_id = chat_id

    def latest(self, *, now: float, max_age_seconds: float) -> LocationSourceResult:
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return LocationSourceResult(LocationStatus.NOT_AVAILABLE)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return LocationSourceResult(LocationStatus.INVALID, detail="invalid_snapshot")
        if not isinstance(payload, dict) or not isinstance(payload.get("locations"), list):
            return LocationSourceResult(LocationStatus.INVALID, detail="invalid_snapshot")
        found, stale, valid = False, False, []
        for item in payload["locations"]:
            if not isinstance(item, dict) or item.get("chat_id") != self.chat_id:
                continue
            found = True
            try:
                expires = _required_number(item, "expires_at")
                observation = LocationObservation(
                    source="telegram", device_id=_required_string(item, "message_id"),
                    observed_at=_required_number(item, "updated_at"), received_at=now,
                    latitude=_required_number(item, "lat"), longitude=_required_number(item, "lon"),
                    accuracy_m=_optional_number(item, "accuracy_m"),
                    altitude_m=_optional_number(item, "altitude_m"),
                    speed_mps=_optional_number(item, "speed_mps"),
                    course_deg=_optional_number(item, "course_deg"),
                    battery_percent=_optional_number(item, "battery_percent"),
                    trigger=_optional_string(item, "trigger"),
                )
                if expires <= now or now - observation.observed_at > max_age_seconds:
                    stale = True
                else:
                    valid.append(observation)
            except (KeyError, TypeError, ValueError):
                continue
        if valid:
            return LocationSourceResult(LocationStatus.OK, max(valid, key=lambda item: item.observed_at))
        return LocationSourceResult(LocationStatus.STALE if stale else (LocationStatus.INVALID if found else LocationStatus.NOT_AVAILABLE))


class ReplayLocationSource:
    """Deterministic in-memory source for tests, demos and offline replays."""

    name = "replay"

    def __init__(self, observations: Sequence[LocationObservation]) -> None:
        self.observations = tuple(observations)

    def latest(self, *, now: float, max_age_seconds: float) -> LocationSourceResult:
        eligible = [item for item in self.observations if item.observed_at <= now + 60]
        if not eligible:
            return LocationSourceResult(LocationStatus.NOT_AVAILABLE)
        observation = max(eligible, key=lambda item: item.observed_at)
        if now - observation.observed_at > max_age_seconds:
            return LocationSourceResult(LocationStatus.STALE)
        return LocationSourceResult(LocationStatus.OK, observation)


class LocationSourceResolver:
    """Resolve sequentially in configured order; never retries or writes state."""

    def __init__(self, sources: Mapping[str, LocationSource], order: Sequence[str], *, logger: logging.Logger | None = None) -> None:
        if not order or any(name not in sources for name in order):
            raise ValueError("location source order contains an unknown source")
        self.sources, self.order = sources, tuple(order)
        self.logger = logger or logging.getLogger(__name__)

    def resolve(self, *, now: float | None = None, max_age_seconds: float) -> LocationSourceResult:
        timestamp = time.time() if now is None else now
        last = LocationSourceResult(LocationStatus.NOT_AVAILABLE)
        for name in self.order:
            result = self.sources[name].latest(now=timestamp, max_age_seconds=max_age_seconds)
            self.logger.info("location source=%s status=%s", name, result.status.value)
            if result.status is LocationStatus.OK:
                return result
            last = result
        return last


def parse_source_order(value: str | None) -> tuple[str, ...]:
    names = tuple(part.strip().lower() for part in (value or "owntracks,telegram").split(",") if part.strip())
    if not names or len(set(names)) != len(names) or any(name not in {"owntracks", "telegram", "replay"} for name in names):
        raise ValueError("invalid location source order")
    return names
