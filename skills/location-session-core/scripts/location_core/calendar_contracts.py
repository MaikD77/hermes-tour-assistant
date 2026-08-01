"""Provider-neutral, privacy-minimized calendar contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Protocol, TypeAlias


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
    source_metadata: Mapping[str, str] = field(default_factory=dict)
    attendance_mode: AttendanceMode = AttendanceMode.UNKNOWN

    def __post_init__(self) -> None:
        if any(value.tzinfo is None or value.utcoffset() is None
               for value in (self.start_at, self.end_at)):
            raise ValueError("calendar event timestamps must be timezone-aware")
        if self.end_at <= self.start_at:
            raise ValueError("calendar event end must be after start")
        if self.attendee_count < 0:
            raise ValueError("attendee_count cannot be negative")
        allowed = {"etag", "updated", "sequence", "recurring_event_id"}
        if not set(self.source_metadata) <= allowed:
            raise ValueError("source_metadata contains a non-allowlisted key")
        object.__setattr__(self, "source_metadata", dict(sorted(self.source_metadata.items())))


@dataclass(frozen=True)
class CalendarAvailable:
    events: tuple[CalendarEvent, ...]
    fetched_at: datetime
    pagination_complete: bool = True


@dataclass(frozen=True)
class CalendarPartial(CalendarAvailable):
    reason: str = "provider returned an incomplete result"


@dataclass(frozen=True)
class CalendarUnavailable:
    fetched_at: datetime
    reason: str = "calendar provider unavailable"


@dataclass(frozen=True)
class CalendarUnauthorized:
    fetched_at: datetime
    reason: str = "calendar provider authorization failed"


@dataclass(frozen=True)
class CalendarRateLimited:
    fetched_at: datetime
    retry_after_seconds: int | None = None
    reason: str = "calendar provider rate limited"


@dataclass(frozen=True)
class CalendarInvalid:
    fetched_at: datetime
    reason: str = "calendar provider returned invalid data"


@dataclass(frozen=True)
class CalendarProviderError:
    fetched_at: datetime
    reason: str = "calendar provider error"


CalendarProviderResult: TypeAlias = (CalendarAvailable | CalendarPartial |
    CalendarUnavailable | CalendarUnauthorized | CalendarRateLimited |
    CalendarInvalid | CalendarProviderError)


class CalendarProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def list_events(self, *, window_start: datetime,
                    window_end: datetime) -> CalendarProviderResult: ...
