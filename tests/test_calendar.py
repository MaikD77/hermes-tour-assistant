from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from location_core.calendar_context import (
    CalendarContextConfig,
    CalendarContextEngine,
)
from location_core.calendar_contracts import (
    AttendanceMode,
    CalendarAvailable,
    CalendarEvent,
    CalendarUnauthorized,
    EventStatus,
    Transparency,
    UserResponse,
    Visibility,
    stable_id,
)
from location_core.calendar_providers import (
    CalendarProviderConfig,
    GoogleCalendarProvider,
    ReplayCalendarProvider,
    normalize_google_event,
    sanitize_text,
)

NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)


def event(name: str, start: datetime, end: datetime, **changes: object) -> CalendarEvent:
    values = {"event_id": stable_id("cal_evt", name), "provider": "replay",
        "calendar_id": "synthetic", "title": name, "description": "", "location_text": "",
        "start_at": start, "end_at": end, "all_day": False,
        "status": EventStatus.CONFIRMED, "visibility": Visibility.DEFAULT,
        "transparency": Transparency.BUSY, "organizer": False, "attendee_count": 0,
        "user_response": UserResponse.ACCEPTED, "attendance_mode": AttendanceMode.UNKNOWN}
    values.update(changes)
    return CalendarEvent(**values)  # type: ignore[arg-type]


def compute(events: tuple[CalendarEvent, ...], *, fetched: datetime = NOW):
    result = CalendarAvailable(events, fetched)
    return CalendarContextEngine().compute(provider_result=result, computed_at=NOW,
        window_start=NOW - timedelta(hours=2), window_end=NOW + timedelta(days=1))


def test_event_contract_is_aware_ordered_deterministic_and_frozen() -> None:
    value = event("one", NOW, NOW + timedelta(hours=1))
    assert value.event_id == stable_id("cal_evt", "one")
    with pytest.raises(FrozenInstanceError):
        value.title = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="timezone-aware"):
        event("naive", NOW.replace(tzinfo=None), NOW + timedelta(hours=1))
    with pytest.raises(ValueError, match="after start"):
        event("backwards", NOW, NOW)
    with pytest.raises(ValueError, match="allowlisted"):
        event("raw", NOW, NOW + timedelta(hours=1), source_metadata={"raw_payload": "no"})


@pytest.mark.parametrize("text,secret", [
    ("Join https://meet.example/a", "https://"), ("a@example.com", "@example.com"),
    ("Call +49 123 456789", "123"), ("<b>Hello</b>", "<b>"),
    ("Meeting ID: 1234-5678", "1234"), ("safe\x00text", "\x00")])
def test_sanitization_removes_sensitive_content(text: str, secret: str) -> None:
    assert secret not in sanitize_text(text, 30)


def test_sanitization_truncates_and_description_defaults_to_empty() -> None:
    assert sanitize_text("x" * 20, 5) == "xxxxx"
    config = CalendarProviderConfig(("primary",))
    raw = {"id": "1", "summary": "Title", "description": "private notes",
           "start": {"dateTime": NOW.isoformat()},
           "end": {"dateTime": (NOW + timedelta(hours=1)).isoformat()}}
    assert normalize_google_event(raw, calendar_id="primary", config=config).description == ""


def test_google_private_all_day_and_remote_are_minimized() -> None:
    raw = {"id": "instance", "summary": "Secret", "description": "Notes",
        "visibility": "private", "location": "https://meet.google.com/secret",
        "conferenceData": {"entryPoints": []}, "recurringEventId": "series",
        "start": {"date": "2026-08-01"}, "end": {"date": "2026-08-02"}}
    value = normalize_google_event(raw, calendar_id="primary",
        config=CalendarProviderConfig(("primary",), description_max_length=100))
    assert (value.title, value.description, value.location_text) == ("Private event", "", "remote")
    assert value.all_day and value.recurrence_id == "series"


class Transport:
    def __init__(self, pages: list[dict[str, object]] | None = None, error: Exception | None = None):
        self.pages, self.error, self.calls = pages or [], error, 0

    def list_events(self, **kwargs: object) -> dict[str, object]:
        if self.error:
            raise self.error
        page = self.pages[self.calls]
        self.calls += 1
        return page


def test_google_adapter_paginates_and_expands_instances() -> None:
    def raw(key: str, hour: int) -> dict[str, object]:
        return {"id": key,
            "start": {"dateTime": (NOW + timedelta(hours=hour)).isoformat()},
            "end": {"dateTime": (NOW + timedelta(hours=hour + 1)).isoformat()}}
    transport = Transport([{"items": [raw("b", 2)], "nextPageToken": "next"},
                           {"items": [raw("a", 1)]}])
    result = GoogleCalendarProvider(transport, CalendarProviderConfig(("primary",)),
                                    clock=lambda: NOW).list_events(
        window_start=NOW, window_end=NOW + timedelta(days=1))
    assert isinstance(result, CalendarAvailable)
    assert [item.start_at.hour for item in result.events] == [13, 14]
    assert transport.calls == 2


@pytest.mark.parametrize("error,kind", [(TimeoutError("token=secret"), "CalendarUnavailable"),
    (ConnectionError("https://secret"), "CalendarUnavailable"), (RuntimeError("payload"), "CalendarProviderError")])
def test_google_adapter_sanitizes_expected_errors(error: Exception, kind: str) -> None:
    result = GoogleCalendarProvider(Transport(error=error), CalendarProviderConfig(("primary",)),
                                    clock=lambda: NOW).list_events(window_start=NOW,
                                    window_end=NOW + timedelta(hours=1))
    assert type(result).__name__ == kind
    assert "secret" not in result.reason


def test_replay_is_filtered_and_deterministic() -> None:
    events = (event("later", NOW + timedelta(hours=2), NOW + timedelta(hours=3)),
              event("current", NOW - timedelta(minutes=5), NOW + timedelta(minutes=5)))
    provider = ReplayCalendarProvider(events, fetched_at=NOW)
    first = provider.list_events(window_start=NOW - timedelta(hours=1), window_end=NOW + timedelta(days=1))
    second = provider.list_events(window_start=NOW - timedelta(hours=1), window_end=NOW + timedelta(days=1))
    assert first == second
    assert [item.title for item in first.events] == ["current", "later"]


def test_current_boundaries_parallel_upcoming_and_declined() -> None:
    values = (event("ends-now", NOW - timedelta(hours=1), NOW),
        event("current", NOW, NOW + timedelta(hours=1)),
        event("free", NOW + timedelta(hours=2), NOW + timedelta(hours=3), transparency=Transparency.FREE),
        event("busy", NOW + timedelta(hours=2), NOW + timedelta(hours=3)),
        event("declined", NOW, NOW + timedelta(hours=1), user_response=UserResponse.DECLINED))
    context = compute(values)
    assert [item.title for item in context.current_events] == ["current"]
    assert [item.title for item in context.upcoming_events[:2]] == ["busy", "free"]
    assert context.next_event.title == "busy"


def test_conflicts_overlap_same_start_no_buffer_and_free_ignored() -> None:
    values = (event("a", NOW, NOW + timedelta(hours=1)),
        event("same", NOW, NOW + timedelta(minutes=30)),
        event("overlap", NOW + timedelta(minutes=45), NOW + timedelta(hours=2)),
        event("adjacent", NOW + timedelta(hours=2), NOW + timedelta(hours=3)),
        event("free", NOW, NOW + timedelta(hours=4), transparency=Transparency.FREE))
    context = compute(values)
    kinds = {conflict.conflict_type for conflict in context.conflicts}
    assert {"same_start", "overlap", "no_buffer"} <= kinds
    assert all(event("free", NOW, NOW + timedelta(hours=1)).event_id not in c.event_ids
               for c in context.conflicts)


@pytest.mark.parametrize("seconds,expected", [(300, "fresh"), (301, "aging"),
    (900, "aging"), (901, "stale"), (3601, "expired")])
def test_freshness_uses_fetch_time(seconds: int, expected: str) -> None:
    assert compute((), fetched=NOW - timedelta(seconds=seconds)).freshness.value == expected


def test_traits_are_technical_only() -> None:
    current = event("remote", NOW, NOW + timedelta(minutes=5),
                    attendance_mode=AttendanceMode.REMOTE)
    upcoming = event("onsite", NOW + timedelta(minutes=10), NOW + timedelta(hours=1),
                     attendance_mode=AttendanceMode.ONSITE)
    traits = compute((current, upcoming)).traits
    assert {"calendar_event_active", "calendar_busy", "calendar_remote_event",
            "calendar_event_starting_soon", "calendar_event_ending_soon"} <= set(traits)
    assert not any(word in trait for trait in traits for word in ("late", "notify", "should"))


def test_unavailable_is_unknown_without_crash() -> None:
    context = CalendarContextEngine().compute(provider_result=CalendarUnauthorized(NOW),
        computed_at=NOW, window_start=NOW, window_end=NOW + timedelta(hours=1))
    assert context.status.value == "unknown"
    assert context.uncertainties[0].code == "unauthorized"


def test_freshness_config_rejects_bad_thresholds() -> None:
    with pytest.raises(ValueError):
        CalendarContextConfig(10, 5, 20)
