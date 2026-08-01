"""Read-only calendar adapters. Google payloads stop at this module boundary."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Callable, Iterable, Mapping, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .calendar_contracts import (
    AttendanceMode,
    CalendarAvailable,
    CalendarEvent,
    CalendarInvalid,
    CalendarPartial,
    CalendarProviderError,
    CalendarProviderResult,
    CalendarRateLimited,
    CalendarUnauthorized,
    CalendarUnavailable,
    EventStatus,
    Transparency,
    UserResponse,
    Visibility,
    stable_id,
)

GOOGLE_READ_ONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)")
_CODE = re.compile(r"\b(?:meeting\s*(?:id|code)|passcode|access\s*code)\s*[:#-]?\s*[\w-]{4,}\b", re.I)
_HTML = re.compile(r"<[^>]+>")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_REMOTE_DOMAINS = ("meet.google.com", "zoom.us", "teams.microsoft.com", "webex.com")


@dataclass(frozen=True)
class CalendarProviderConfig:
    calendar_ids: tuple[str, ...]
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    max_events: int = 250
    title_max_length: int = 120
    description_max_length: int = 0
    location_max_length: int = 120
    sanitize_private_events: bool = True

    def __post_init__(self) -> None:
        if not self.calendar_ids:
            raise ValueError("at least one explicit calendar ID is required")
        if self.max_events < 1 or min(self.title_max_length, self.location_max_length) < 1:
            raise ValueError("calendar limits must be positive")
        if self.description_max_length < 0:
            raise ValueError("description maximum cannot be negative")


def sanitize_text(value: object, maximum: int) -> str:
    """Remove sensitive technical identifiers; never retain unlimited text."""
    if maximum <= 0:
        return ""
    text = html.unescape(_HTML.sub(" ", str(value or "")))
    text = _URL.sub("[link]", text)
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    text = _CODE.sub("[code]", text)
    text = _CONTROL.sub("", text)
    text = " ".join(text.split())
    return text[:maximum].rstrip()


def _enum(cls: type[Any], value: object, default: Any) -> Any:
    try:
        return cls(str(value).lower().replace("needsaction", "needs_action"))
    except ValueError:
        return default


def _datetime(value: Mapping[str, Any], fallback_timezone: str | None) -> tuple[datetime, bool]:
    if "dateTime" in value:
        parsed = datetime.fromisoformat(str(value["dateTime"]).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            zone_name = str(value.get("timeZone") or fallback_timezone or "")
            if not zone_name:
                raise ValueError("timezone missing")
            parsed = parsed.replace(tzinfo=ZoneInfo(zone_name))
        return parsed, False
    day = date.fromisoformat(str(value["date"]))
    zone_name = str(value.get("timeZone") or fallback_timezone or "UTC")
    return datetime.combine(day, time.min, ZoneInfo(zone_name)), True


def normalize_google_event(raw: Mapping[str, Any], *, calendar_id: str,
                           config: CalendarProviderConfig) -> CalendarEvent:
    start, all_day = _datetime(raw.get("start", {}), raw.get("timeZone"))
    end, end_all_day = _datetime(raw.get("end", {}), raw.get("timeZone"))
    all_day = all_day and end_all_day
    visibility = _enum(Visibility, raw.get("visibility", "default"), Visibility.DEFAULT)
    source = " ".join(str(raw.get(key, "")) for key in
        ("location", "hangoutLink", "conferenceData")).lower()
    remote = any(domain in source for domain in _REMOTE_DOMAINS) or bool(raw.get("conferenceData"))
    physical = bool(str(raw.get("location", "")).strip()) and not remote
    mode = AttendanceMode.REMOTE if remote else AttendanceMode.ONSITE if physical else AttendanceMode.UNKNOWN
    private = visibility is Visibility.PRIVATE and config.sanitize_private_events
    attendees = raw.get("attendees", ()) if isinstance(raw.get("attendees", ()), list) else ()
    self_attendee = next((a for a in attendees if isinstance(a, dict) and a.get("self")), None)
    organizer = bool(raw.get("organizer", {}).get("self"))
    response = UserResponse.ORGANIZER if organizer else _enum(UserResponse,
        self_attendee.get("responseStatus") if self_attendee else "unknown", UserResponse.UNKNOWN)
    provider_key = str(raw.get("id", ""))
    recurrence = raw.get("recurringEventId")
    event_id = stable_id("cal_evt", "google", calendar_id, provider_key,
                         raw.get("originalStartTime"), recurrence)
    metadata = {key: str(raw[key]) for key in
        ("etag", "updated", "sequence", "recurring_event_id") if key in raw}
    if recurrence:
        metadata["recurring_event_id"] = str(recurrence)
    return CalendarEvent(event_id, "google", calendar_id,
        "Private event" if private else sanitize_text(raw.get("summary", "Untitled event"), config.title_max_length),
        "" if private else sanitize_text(raw.get("description", ""), config.description_max_length),
        mode.value if private else sanitize_text(raw.get("location", ""), config.location_max_length),
        start, end, all_day, _enum(EventStatus, raw.get("status", "confirmed"), EventStatus.CONFIRMED),
        visibility, Transparency.FREE if raw.get("transparency") == "transparent" else Transparency.BUSY,
        organizer, len(attendees), response, str(recurrence) if recurrence else None, metadata, mode)


class GoogleTransport(Protocol):
    def list_events(self, *, calendar_id: str, time_min: str, time_max: str,
                    page_token: str | None, single_events: bool,
                    order_by: str) -> Mapping[str, Any]: ...


class GoogleCalendarProvider:
    """Google Calendar v3 adapter using an injected, read-only transport."""

    provider_id = "google"

    def __init__(self, transport: GoogleTransport, config: CalendarProviderConfig,
                 *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.transport, self.config, self.clock = transport, config, clock

    def list_events(self, *, window_start: datetime,
                    window_end: datetime) -> CalendarProviderResult:
        fetched = self.clock()
        if any(value.tzinfo is None for value in (window_start, window_end)) or window_end <= window_start:
            return CalendarInvalid(fetched, "calendar query window is invalid")
        events: list[CalendarEvent] = []
        invalid = 0
        try:
            for calendar_id in self.config.calendar_ids:
                if self.config.include and calendar_id not in self.config.include:
                    continue
                if calendar_id in self.config.exclude:
                    continue
                token: str | None = None
                while True:
                    page = self.transport.list_events(calendar_id=calendar_id,
                        time_min=window_start.isoformat(), time_max=window_end.isoformat(),
                        page_token=token, single_events=True, order_by="startTime")
                    for raw in page.get("items", ()):
                        try:
                            events.append(normalize_google_event(raw, calendar_id=calendar_id,
                                                                config=self.config))
                        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
                            invalid += 1
                        if len(events) >= self.config.max_events:
                            return CalendarPartial(tuple(sorted(events, key=_event_key)), fetched,
                                False, "configured event limit reached")
                    token = str(page.get("nextPageToken")) if page.get("nextPageToken") else None
                    if not token:
                        break
        except Exception as error:
            status = getattr(error, "status_code", getattr(error, "status", None))
            if status in (401, 403):
                return CalendarUnauthorized(fetched)
            if status == 429:
                return CalendarRateLimited(fetched)
            if isinstance(error, (TimeoutError, ConnectionError)):
                return CalendarUnavailable(fetched)
            return CalendarProviderError(fetched)
        ordered = tuple(sorted(events, key=_event_key))
        return CalendarPartial(ordered, fetched, True, "one or more events were invalid") if invalid else CalendarAvailable(ordered, fetched)


def _event_key(event: CalendarEvent) -> tuple[datetime, int, int, str]:
    return (event.start_at, event.transparency is Transparency.FREE,
            event.status is EventStatus.TENTATIVE, event.event_id)


class ReplayCalendarProvider:
    """Deterministic offline provider over already-sanitized synthetic events."""

    provider_id = "replay"

    def __init__(self, events: Iterable[CalendarEvent], *, fetched_at: datetime) -> None:
        self.events, self.fetched_at = tuple(events), fetched_at

    def list_events(self, *, window_start: datetime,
                    window_end: datetime) -> CalendarProviderResult:
        if any(value.tzinfo is None for value in (window_start, window_end)) or window_end <= window_start:
            return CalendarInvalid(self.fetched_at, "calendar query window is invalid")
        selected = (event for event in self.events
                    if event.end_at > window_start and event.start_at < window_end)
        return CalendarAvailable(tuple(sorted(selected, key=_event_key)), self.fetched_at)
