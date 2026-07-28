#!/usr/bin/env python3
"""Typed boundary contracts shared by location runtimes and provider adapters."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class LocationSample:
    session_id: str
    message_id: str
    observed_at: float
    expires_at: float
    lat: float
    lon: float
    source: str = "telegram"

    @classmethod
    def from_mapping(
        cls,
        value: dict[str, Any],
        *,
        expected_chat_id: str,
        now: float,
        max_age_seconds: float,
    ) -> "LocationSample":
        if str(value.get("chat_id", "")) != expected_chat_id:
            raise ValueError("location belongs to a different chat")
        message_id = str(value.get("message_id", "")).strip()
        if not message_id:
            raise ValueError("live-location message_id is required")
        try:
            lat = float(value["lat"])
            lon = float(value["lon"])
            observed_at = float(value["updated_at"])
            expires_at = float(value["expires_at"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("location contains invalid fields") from error
        if not math.isfinite(lat) or not -90 <= lat <= 90:
            raise ValueError("location latitude is invalid")
        if not math.isfinite(lon) or not -180 <= lon <= 180:
            raise ValueError("location longitude is invalid")
        if not math.isfinite(observed_at) or observed_at <= 0:
            raise ValueError("location observation time is invalid")
        if not math.isfinite(expires_at) or expires_at <= now:
            raise ValueError("live location has expired")
        if observed_at > now + 60:
            raise ValueError("location observation is in the future")
        if now - observed_at > max_age_seconds:
            raise ValueError("live location is stale")
        digest = hashlib.sha256(
            f"telegram\0{expected_chat_id}\0{message_id}".encode()
        ).hexdigest()[:20]
        return cls(
            session_id=f"telegram-{digest}",
            message_id=message_id,
            observed_at=observed_at,
            expires_at=expires_at,
            lat=lat,
            lon=lon,
        )


@dataclass(frozen=True)
class GateDecision:
    wake_agent: bool
    session_id: str | None = None
    reason: str | None = None
    cadence_minutes: int | None = None
    flags: tuple[str, ...] = ()
    error_code: str | None = None

    def to_cron_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"wakeAgent": self.wake_agent}
        if not self.wake_agent:
            return payload
        context: dict[str, Any] = {
            "session_id": self.session_id,
            "reason": self.reason,
            "cadence_minutes": self.cadence_minutes,
            "flags": list(self.flags),
        }
        if self.error_code:
            context["error_code"] = self.error_code
        payload["context"] = {key: value for key, value in context.items() if value is not None}
        return payload


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    provider: str
    capability: str
    observed_at: float
    data: T | None = None
    valid_until: float | None = None
    confidence: float = 0.0
    error_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_code is None and self.data is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RouteGeometry:
    points: tuple[tuple[float, float], ...]
    distance_m: float
    duration_seconds: float | None = None
    provider: str | None = None

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError("route geometry needs at least two points")
        if not math.isfinite(self.distance_m) or self.distance_m <= 0:
            raise ValueError("route distance must be finite and positive")
        if self.duration_seconds is not None and (
            not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0
        ):
            raise ValueError("route duration must be finite and positive")
        for lat, lon in self.points:
            if not math.isfinite(lat) or not -90 <= lat <= 90:
                raise ValueError("route latitude is invalid")
            if not math.isfinite(lon) or not -180 <= lon <= 180:
                raise ValueError("route longitude is invalid")


@dataclass(frozen=True)
class AlertDecision:
    silent: bool
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.silent and self.events:
            raise ValueError("silent alert decision cannot contain events")
        if len(self.events) > 3:
            raise ValueError("at most three safety events may be delivered")
