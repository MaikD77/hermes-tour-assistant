"""Provider-neutral, privacy-minimized calendar contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Mapping, Protocol, TypeAlias

SOURCE_METADATA_KEYS = frozenset({"etag", "updated", "sequence", "recurring_event_id"})
MAX_REASON_LENGTH = 160


def sanitize_reason(reason: object, fallback: str) -> str:
    """Return a bounded category, never provider-controlled diagnostic content."""
    value = " ".join(str(reason or fallback).split())[:MAX_REASON_LENGTH]
    forbidden = ("://", "@", "token", "secret", "payload")
    return fallback if any(item in value.lower() for item in forbidden) else value


class EventStatus(str, Enum):
    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"
    CANCELLED = "cancelled"


class Visibility(str, Enum):
    PRIVATE = "private"
    PUBLIC = "public"
    DEFAULT = "default"


class Transparency(str, Enum):
    BUSY = "busy"
    FREE = "free"


class UserResponse(str, Enum):
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TENTATIVE = "tentative"
    NEEDS_ACTION = "needs_action"
    ORGANIZER = "organizer"
    UNKNOWN = "unknown"


class AttendanceMode(str, Enum):
    REMOTE = "remote"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


def stable_id(prefix: str, *parts: object) -> str:
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(canonical.encode()).hexdigest()[:24]}"


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    provider: str
    calendar_id: str
    title: str
    description: str
    location_text: str
    start_at: datetime
    end_at: datetime
    all_day: bool
    status: EventStatus
    visibility: Visibility
    transparency: Transparency
    organizer: bool
    attendee_count: int
    user_response: UserResponse
    recurrence_id: str | None = None
    source_metadata: tuple[tuple[str, str], ...] = ()
    attendance_mode: AttendanceMode = AttendanceMode.UNKNOWN

    def __post_init__(self) -> None:
        if any(value.tzinfo is None or value.utcoffset() is None
               for value in (self.start_at, self.end_at)):
            raise ValueError("calendar event timestamps must be timezone-aware")
        if self.end_at <= self.start_at:
            raise ValueError("calendar event end must be after start")
        if self.attendee_count < 0:
            raise ValueError("attendee_count cannot be negative")
        raw_metadata = (self.source_metadata.items()
                        if isinstance(self.source_metadata, Mapping) else self.source_metadata)
        metadata = tuple(sorted((str(key), str(value)) for key, value in raw_metadata))
        if not {key for key, _ in metadata} <= SOURCE_METADATA_KEYS:
            raise ValueError("source_metadata contains a non-allowlisted key")
        if len({key for key, _ in metadata}) != len(metadata):
            raise ValueError("source_metadata contains duplicate keys")
        object.__setattr__(self, "source_metadata", metadata)


@dataclass(frozen=True)
class CalendarAvailable:
    events: tuple[CalendarEvent, ...]
    fetched_at: datetime
    pagination_complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))
        if not isinstance(self.pagination_complete, bool):
            raise ValueError("pagination_complete must be boolean")


@dataclass(frozen=True)
class CalendarPartial(CalendarAvailable):
    reason: str = "provider returned an incomplete result"

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "reason", sanitize_reason(
            self.reason, "provider returned an incomplete result"))


@dataclass(frozen=True)
class CalendarUnavailable:
    fetched_at: datetime
    reason: str = "calendar provider unavailable"

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", sanitize_reason(
            self.reason, "calendar provider unavailable"))


@dataclass(frozen=True)
class CalendarUnauthorized:
    fetched_at: datetime
    reason: str = "calendar provider authorization failed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", sanitize_reason(
            self.reason, "calendar provider authorization failed"))


@dataclass(frozen=True)
class CalendarRateLimited:
    fetched_at: datetime
    retry_after_seconds: int | None = None
    reason: str = "calendar provider rate limited"

    def __post_init__(self) -> None:
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds cannot be negative")
        object.__setattr__(self, "reason", sanitize_reason(
            self.reason, "calendar provider rate limited"))


@dataclass(frozen=True)
class CalendarInvalid:
    fetched_at: datetime
    reason: str = "calendar provider returned invalid data"

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", sanitize_reason(
            self.reason, "calendar provider returned invalid data"))


@dataclass(frozen=True)
class CalendarProviderError:
    fetched_at: datetime
    reason: str = "calendar provider error"

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", sanitize_reason(
            self.reason, "calendar provider error"))


CalendarProviderResult: TypeAlias = (CalendarAvailable | CalendarPartial |
    CalendarUnavailable | CalendarUnauthorized | CalendarRateLimited |
    CalendarInvalid | CalendarProviderError)


class CalendarProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def list_events(self, *, window_start: datetime,
                    window_end: datetime) -> CalendarProviderResult: ...
