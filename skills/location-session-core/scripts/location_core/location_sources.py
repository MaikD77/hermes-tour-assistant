"""Source-neutral location observations and deterministic source resolution."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, TypeAlias

MetadataValue: TypeAlias = str | int | float | bool
Metadata: TypeAlias = tuple[tuple[str, MetadataValue], ...]
_ALLOWED_METADATA = frozenset(
    {"event_id", "message_id", "connection_type", "replay_id", "legacy_session_id"}
)
_COORDINATE_PAIR = re.compile(r"(?<!\d)[+-]?\d{1,2}(?:\.\d+)?\s*[,;]\s*[+-]?\d{1,3}(?:\.\d+)?(?!\d)")


def _number(name: str, value: object, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _utc(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value.astimezone(UTC)


def _metadata(value: Mapping[str, MetadataValue] | Metadata) -> Metadata:
    items = value.items() if isinstance(value, Mapping) else value
    normalized: list[tuple[str, MetadataValue]] = []
    for key, item in items:
        if key not in _ALLOWED_METADATA:
            raise ValueError(f"source_metadata key is not allowed: {key}")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("source_metadata numbers must be finite")
        if not isinstance(item, (str, int, float, bool)):
            raise ValueError("source_metadata values must be scalar")
        lowered = item.lower() if isinstance(item, str) else ""
        if isinstance(item, str) and ("@" in item or "://" in lowered):
            raise ValueError("source_metadata must not contain credentials or URLs")
        if isinstance(item, str) and _COORDINATE_PAIR.search(item):
            raise ValueError("source_metadata must not contain coordinate copies")
        normalized.append((key, item))
    return tuple(sorted(normalized))


def _canonical_number(value: float | None) -> str | None:
    return None if value is None else format(value, ".15g")


@dataclass(frozen=True)
class LocationObservation:
    """Immutable canonical observation; identity covers stable normalized input."""

    source: str
    device_id: str
    observed_at: datetime
    received_at: datetime
    latitude: float
    longitude: float
    accuracy_m: float | None = None
    altitude_m: float | None = None
    speed_mps: float | None = None
    course_deg: float | None = None
    battery_percent: float | None = None
    trigger: str | None = None
    source_metadata: Mapping[str, MetadataValue] | Metadata = ()
    observation_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source is required")
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise ValueError("device_id is required")
        if self.trigger is not None and not isinstance(self.trigger, str):
            raise ValueError("trigger must be a string")
        observed = _utc("observed_at", self.observed_at)
        received = _utc("received_at", self.received_at)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "received_at", received)
        metadata = _metadata(self.source_metadata)
        object.__setattr__(self, "source_metadata", metadata)
        for name in ("latitude", "longitude"):
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
        if observed > received + timedelta(seconds=60):
            raise ValueError("observed_at is implausibly far in the future")
        identity = {
            "accuracy_m": _canonical_number(self.accuracy_m),
            "device_id": self.device_id,
            "event_id": dict(metadata).get("event_id")
            or dict(metadata).get("message_id")
            or dict(metadata).get("replay_id"),
            "latitude": _canonical_number(self.latitude),
            "longitude": _canonical_number(self.longitude),
            "observed_at": observed.isoformat(timespec="microseconds"),
            "source": self.source,
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        expected = f"loc_{digest}"
        if self.observation_id and self.observation_id != expected:
            raise ValueError("observation_id does not match canonical observation")
        object.__setattr__(self, "observation_id", expected)


def observation_session_id(observation: LocationObservation) -> str:
    """Stable opaque compatibility session key for current state schemas."""
    legacy = dict(observation.source_metadata).get("legacy_session_id")
    if isinstance(legacy, str):
        return legacy
    digest = hashlib.sha256(f"{observation.source}\0{observation.device_id}".encode()).hexdigest()[
        :20
    ]
    return f"{observation.source}-{digest}"


def adapt_legacy_sample(value: object, *, received_at: datetime) -> LocationObservation:
    """Explicitly adapt the pre-v1.5 float/alias sample at a public runtime boundary."""
    if isinstance(value, LocationObservation):
        return value
    observed_value = getattr(value, "observed_at")
    if isinstance(observed_value, bool) or not isinstance(observed_value, (int, float)):
        raise ValueError("legacy observed_at must be a Unix timestamp")
    source = getattr(value, "source", "telegram")
    message_id = getattr(value, "message_id")
    return LocationObservation(
        source=source,
        device_id=str(message_id),
        observed_at=datetime.fromtimestamp(observed_value, UTC),
        received_at=_utc("received_at", received_at),
        latitude=getattr(value, "lat"),
        longitude=getattr(value, "lon"),
        source_metadata={
            "message_id": str(message_id),
            "legacy_session_id": str(getattr(value, "session_id")),
        },
    )


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

    def latest(self, *, now: datetime, max_age_seconds: float) -> LocationSourceResult: ...


class OwnTracksReceiver(Protocol):
    def latest_payload(self) -> Mapping[str, Any] | None: ...


class HttpOwnTracksReceiver:
    """Narrow receiver API client; it neither retries nor knows SQLite."""

    def __init__(self, url: str, *, timeout_seconds: float = 5) -> None:
        self.url, self.timeout_seconds = url, timeout_seconds

    def latest_payload(self) -> Mapping[str, Any] | None:
        with urllib.request.urlopen(
            urllib.request.Request(self.url), timeout=self.timeout_seconds
        ) as response:
            value = json.loads(response.read().decode("utf-8"))
        return value if isinstance(value, dict) else None


def _iso_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc("timestamp", parsed)


def _unix_datetime(name: str, value: object) -> datetime:
    number = _number(name, value)
    assert number is not None
    return datetime.fromtimestamp(number, UTC)


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


class OwnTracksLocationSource:
    name = "owntracks"
    _METADATA_ALLOWLIST = frozenset({"event_id", "connection_type"})

    def __init__(self, receiver: OwnTracksReceiver) -> None:
        self.receiver = receiver

    def latest(self, *, now: datetime, max_age_seconds: float) -> LocationSourceResult:
        now = _utc("now", now)
        try:
            payload = self.receiver.latest_payload()
        except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError):
            return LocationSourceResult(LocationStatus.UNREACHABLE, detail="receiver_unreachable")
        if payload is None or payload.get("result") == "not_found":
            return LocationSourceResult(LocationStatus.NOT_AVAILABLE)
        if payload.get("result") != "ok":
            return LocationSourceResult(LocationStatus.INVALID, detail="invalid_receiver_response")
        try:
            metadata = {
                key: payload[key]
                for key in self._METADATA_ALLOWLIST
                if payload.get(key) is not None
            }
            observation = LocationObservation(
                source="owntracks",
                device_id=_required_string(payload, "device_id"),
                observed_at=_iso_datetime(payload.get("observed_at")),
                received_at=_iso_datetime(payload["received_at"])
                if payload.get("received_at") is not None
                else now,
                latitude=_required_number(payload, "latitude"),
                longitude=_required_number(payload, "longitude"),
                accuracy_m=_optional_number(payload, "accuracy_m"),
                altitude_m=_optional_number(payload, "altitude_m"),
                speed_mps=_optional_number(payload, "speed_mps"),
                course_deg=_optional_number(payload, "course_deg"),
                battery_percent=_optional_number(payload, "battery_percent"),
                trigger=_optional_string(payload, "trigger"),
                source_metadata=metadata,
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return LocationSourceResult(LocationStatus.INVALID, detail="invalid_observation")
        if (
            payload.get("stale") is True
            or (now - observation.observed_at).total_seconds() > max_age_seconds
        ):
            return LocationSourceResult(LocationStatus.STALE)
        return LocationSourceResult(LocationStatus.OK, observation)


class TelegramLocationSource:
    name = "telegram"
    _METADATA_ALLOWLIST = frozenset({"message_id"})

    def __init__(self, snapshot_path: Path, chat_id: str) -> None:
        self.snapshot_path, self.chat_id = snapshot_path, chat_id

    def latest(self, *, now: datetime, max_age_seconds: float) -> LocationSourceResult:
        now = _utc("now", now)
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
            if not isinstance(item, dict) or str(item.get("chat_id", "")) != self.chat_id:
                continue
            found = True
            try:
                message_id = _required_string(item, "message_id")
                observation = LocationObservation(
                    source="telegram",
                    device_id=message_id,
                    observed_at=_unix_datetime("updated_at", item.get("updated_at")),
                    received_at=now,
                    latitude=_required_number(item, "lat"),
                    longitude=_required_number(item, "lon"),
                    accuracy_m=_optional_number(item, "accuracy_m"),
                    altitude_m=_optional_number(item, "altitude_m"),
                    speed_mps=_optional_number(item, "speed_mps"),
                    course_deg=_optional_number(item, "course_deg"),
                    battery_percent=_optional_number(item, "battery_percent"),
                    trigger=_optional_string(item, "trigger"),
                    source_metadata={"message_id": message_id},
                )
                expires = _unix_datetime("expires_at", item.get("expires_at"))
                if (
                    expires <= now
                    or (now - observation.observed_at).total_seconds() > max_age_seconds
                ):
                    stale = True
                else:
                    valid.append(observation)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        if valid:
            return LocationSourceResult(
                LocationStatus.OK, max(valid, key=lambda item: item.observed_at)
            )
        status = (
            LocationStatus.STALE
            if stale
            else (LocationStatus.INVALID if found else LocationStatus.NOT_AVAILABLE)
        )
        return LocationSourceResult(status)


class ReplayLocationSource:
    """Deterministic immutable source for tests and offline replay."""

    name = "replay"
    _METADATA_ALLOWLIST = frozenset({"replay_id", "event_id"})

    def __init__(self, observations: Sequence[LocationObservation]) -> None:
        self.observations = tuple(observations)
        if any(item.source != self.name for item in self.observations):
            raise ValueError("replay observations must declare source=replay")
        if any(
            set(dict(item.source_metadata)) - self._METADATA_ALLOWLIST for item in self.observations
        ):
            raise ValueError("replay observation contains disallowed metadata")

    def latest(self, *, now: datetime, max_age_seconds: float) -> LocationSourceResult:
        now = _utc("now", now)
        eligible = [
            item for item in self.observations if item.observed_at <= now + timedelta(seconds=60)
        ]
        if not eligible:
            return LocationSourceResult(LocationStatus.NOT_AVAILABLE)
        observation = max(eligible, key=lambda item: item.observed_at)
        if (now - observation.observed_at).total_seconds() > max_age_seconds:
            return LocationSourceResult(LocationStatus.STALE)
        return LocationSourceResult(LocationStatus.OK, observation)


class LocationSourceResolver:
    """Resolve sequentially in configured order; never retries or writes state."""

    def __init__(
        self,
        sources: Mapping[str, LocationSource],
        order: Sequence[str],
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        if not order or any(name not in sources for name in order):
            raise ValueError("location source order contains an unknown source")
        self.sources, self.order = sources, tuple(order)
        self.logger = logger or logging.getLogger(__name__)

    def resolve(self, *, now: datetime, max_age_seconds: float) -> LocationSourceResult:
        timestamp = _utc("now", now)
        last = LocationSourceResult(LocationStatus.NOT_AVAILABLE)
        for name in self.order:
            result = self.sources[name].latest(now=timestamp, max_age_seconds=max_age_seconds)
            self.logger.info("location source=%s status=%s", name, result.status.value)
            if result.status is LocationStatus.OK:
                return result
            last = result
        return last


def parse_source_order(value: str | None) -> tuple[str, ...]:
    names = tuple(
        part.strip().lower() for part in (value or "owntracks,telegram").split(",") if part.strip()
    )
    if (
        not names
        or len(set(names)) != len(names)
        or any(name not in {"owntracks", "telegram", "replay"} for name in names)
    ):
        raise ValueError("invalid location source order")
    return names
