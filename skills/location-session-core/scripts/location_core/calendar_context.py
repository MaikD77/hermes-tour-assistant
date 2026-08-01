"""Deterministic calendar relevance, conflict, freshness and evidence engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from .calendar_contracts import (
    AttendanceMode,
    CalendarAvailable,
    CalendarEvent,
    CalendarPartial,
    CalendarProviderResult,
    CalendarUnauthorized,
    EventStatus,
    Transparency,
    UserResponse,
    Visibility,
    stable_id,
)


class CalendarFreshness(str, Enum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    EXPIRED = "expired"


class CalendarContextStatus(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    STALE = "stale"
    UNKNOWN = "unknown"
    INVALID = "invalid"


@dataclass(frozen=True)
class CalendarContextConfig:
    fresh_seconds: int = 300
    aging_seconds: int = 900
    stale_seconds: int = 3600
    recent_minutes: int = 120
    starting_soon_minutes: int = 15
    ending_soon_minutes: int = 10
    conflict_buffer_minutes: int = 0
    upcoming_limit: int = 10

    def __post_init__(self) -> None:
        if not 0 < self.fresh_seconds <= self.aging_seconds <= self.stale_seconds:
            raise ValueError("calendar freshness thresholds must be positive and ordered")
        if min(self.recent_minutes, self.starting_soon_minutes, self.ending_soon_minutes,
               self.conflict_buffer_minutes) < 0 or self.upcoming_limit < 1:
            raise ValueError("calendar context limits cannot be negative")


@dataclass(frozen=True)
class CalendarEvidence:
    evidence_id: str
    event_id: str | None
    evidence_type: str
    observed_at: datetime
    confidence: float
    freshness: CalendarFreshness
    reason: str
    status: str


@dataclass(frozen=True)
class CalendarUncertainty:
    code: str
    severity: str
    reason: str
    detected_at: datetime
    resolvable: bool
    event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalendarConflict:
    conflict_id: str
    event_ids: tuple[str, ...]
    conflict_type: str
    starts_at: datetime
    ends_at: datetime
    severity: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class CalendarContext:
    context_id: str
    computed_at: datetime
    window_start: datetime
    window_end: datetime
    current_events: tuple[CalendarEvent, ...]
    upcoming_events: tuple[CalendarEvent, ...]
    recent_events: tuple[CalendarEvent, ...]
    next_event: CalendarEvent | None
    current_busy_until: datetime | None
    conflicts: tuple[CalendarConflict, ...]
    uncertainties: tuple[CalendarUncertainty, ...]
    freshness: CalendarFreshness
    confidence: float
    status: CalendarContextStatus
    evidence: tuple[CalendarEvidence, ...]
    traits: tuple[str, ...]


def _sort(event: CalendarEvent) -> tuple[datetime, int, int, str]:
    return (event.start_at, event.transparency is Transparency.FREE,
            event.status is EventStatus.TENTATIVE, event.event_id)


def _active(event: CalendarEvent) -> bool:
    return (event.status is not EventStatus.CANCELLED and
            (event.user_response is not UserResponse.DECLINED or event.organizer))


class CalendarContextEngine:
    def __init__(self, config: CalendarContextConfig | None = None) -> None:
        self.config = config or CalendarContextConfig()

    def _freshness(self, age: float) -> CalendarFreshness:
        if age <= self.config.fresh_seconds:
            return CalendarFreshness.FRESH
        if age <= self.config.aging_seconds:
            return CalendarFreshness.AGING
        if age <= self.config.stale_seconds:
            return CalendarFreshness.STALE
        return CalendarFreshness.EXPIRED

    def compute(self, *, provider_result: CalendarProviderResult, computed_at: datetime,
                window_start: datetime, window_end: datetime) -> CalendarContext:
        if any(value.tzinfo is None or value.utcoffset() is None
               for value in (computed_at, window_start, window_end)) or window_end <= window_start:
            return self._empty(computed_at, window_start, window_end,
                CalendarContextStatus.INVALID, "event_time_invalid", "calendar window is invalid")
        fetched = provider_result.fetched_at
        freshness = self._freshness(max(0., (computed_at - fetched).total_seconds()))
        if not isinstance(provider_result, CalendarAvailable):
            code = "unauthorized" if isinstance(provider_result, CalendarUnauthorized) else "provider_unavailable"
            return self._empty(computed_at, window_start, window_end,
                CalendarContextStatus.UNKNOWN, code, provider_result.reason, freshness)
        events = tuple(sorted((event for event in provider_result.events if _active(event)), key=_sort))
        current = tuple(event for event in events if event.start_at <= computed_at < event.end_at)
        upcoming = tuple(event for event in events if event.start_at > computed_at)[:self.config.upcoming_limit]
        recent_floor = computed_at - timedelta(minutes=self.config.recent_minutes)
        recent = tuple(event for event in events if recent_floor <= event.end_at <= computed_at)
        conflicts = self._conflicts(events)
        uncertainties: list[CalendarUncertainty] = []
        if isinstance(provider_result, CalendarPartial):
            uncertainties.append(CalendarUncertainty("partial_result", "warning",
                "calendar result is incomplete", computed_at, True))
        if freshness in (CalendarFreshness.STALE, CalendarFreshness.EXPIRED):
            uncertainties.append(CalendarUncertainty("calendar_stale", "warning",
                "calendar fetch is stale", computed_at, True))
        for event in events:
            if event.visibility is Visibility.PRIVATE:
                uncertainties.append(CalendarUncertainty("event_visibility_private", "info",
                    "private event details were minimized", computed_at, False, (event.event_id,)))
            if event.user_response is UserResponse.UNKNOWN:
                uncertainties.append(CalendarUncertainty("user_response_unknown", "info",
                    "user response is unavailable", computed_at, True, (event.event_id,)))
        for conflict in conflicts:
            uncertainties.append(CalendarUncertainty("event_overlap", conflict.severity,
                "calendar timing conflict detected", computed_at, True, conflict.event_ids))
        evidence = self._evidence(current, upcoming, conflicts, fetched, freshness)
        confidence = self._confidence(provider_result, events, freshness, conflicts)
        status = (CalendarContextStatus.STALE if freshness in
            (CalendarFreshness.STALE, CalendarFreshness.EXPIRED) else
            CalendarContextStatus.PARTIAL if isinstance(provider_result, CalendarPartial) else
            CalendarContextStatus.AVAILABLE)
        busy_until = max((e.end_at for e in current if e.transparency is Transparency.BUSY), default=None)
        traits = self._traits(current, upcoming, conflicts, computed_at)
        identity = (computed_at.isoformat(), window_start.isoformat(), window_end.isoformat(),
                    tuple(e.event_id for e in events), tuple(c.conflict_id for c in conflicts), status.value)
        return CalendarContext(stable_id("cal_ctx", *identity), computed_at, window_start, window_end,
            current, upcoming, recent, upcoming[0] if upcoming else None, busy_until, conflicts,
            tuple(uncertainties), freshness, confidence, status, evidence, traits)

    def _conflicts(self, events: tuple[CalendarEvent, ...]) -> tuple[CalendarConflict, ...]:
        busy = tuple(event for event in events if event.transparency is Transparency.BUSY)
        found: list[CalendarConflict] = []
        for index, first in enumerate(busy):
            for second in busy[index + 1:]:
                if second.start_at > first.end_at + timedelta(minutes=self.config.conflict_buffer_minutes):
                    break
                if first.start_at == second.start_at:
                    kind, severity = "same_start", "warning"
                elif second.start_at < first.end_at:
                    kind, severity = "overlap", "critical"
                elif second.start_at == first.end_at:
                    kind, severity = "no_buffer", "warning"
                else:
                    continue
                start, end = second.start_at, min(first.end_at, second.end_at)
                if end < start:
                    end = start
                ids = tuple(sorted((first.event_id, second.event_id)))
                found.append(CalendarConflict(stable_id("cal_conf", kind, ids, start, end), ids,
                    kind, start, end, severity, .95, "busy calendar events have a timing conflict"))
        return tuple(sorted(found, key=lambda value: (value.starts_at, value.conflict_id)))

    def _evidence(self, current: tuple[CalendarEvent, ...], upcoming: tuple[CalendarEvent, ...],
                  conflicts: tuple[CalendarConflict, ...], fetched: datetime,
                  freshness: CalendarFreshness) -> tuple[CalendarEvidence, ...]:
        values: list[CalendarEvidence] = []
        for kind, events in (("current_event", current), ("upcoming_event", upcoming[:1])):
            for event in events:
                values.append(CalendarEvidence(stable_id("cal_ev", kind, event.event_id), event.event_id,
                    kind, fetched, .9 if event.status is EventStatus.CONFIRMED else .65,
                    freshness, f"sanitized {kind.replace('_', ' ')} timing", "active"))
                if event.attendance_mode is AttendanceMode.REMOTE:
                    values.append(CalendarEvidence(stable_id("cal_ev", "remote", event.event_id),
                        event.event_id, "event_remote", fetched, .9, freshness,
                        "technical conference indicator present", "active"))
        for conflict in conflicts:
            values.append(CalendarEvidence(stable_id("cal_ev", "conflict", conflict.conflict_id),
                None, "event_conflict", fetched, conflict.confidence, freshness,
                "sanitized timing conflict", "active"))
        return tuple(values)

    def _traits(self, current: tuple[CalendarEvent, ...], upcoming: tuple[CalendarEvent, ...],
                conflicts: tuple[CalendarConflict, ...], now: datetime) -> tuple[str, ...]:
        traits: set[str] = set()
        if current:
            traits.add("calendar_event_active")
        if upcoming:
            traits.add("calendar_event_upcoming")
        relevant = current + upcoming[:1]
        if any(e.transparency is Transparency.BUSY for e in relevant):
            traits.add("calendar_busy")
        if any(e.transparency is Transparency.FREE for e in relevant):
            traits.add("calendar_free")
        if conflicts:
            traits.add("calendar_conflict")
        if any(e.attendance_mode is AttendanceMode.REMOTE for e in relevant):
            traits.add("calendar_remote_event")
        if any(e.attendance_mode is AttendanceMode.ONSITE for e in relevant):
            traits.add("calendar_onsite_event")
        if upcoming and upcoming[0].start_at - now <= timedelta(minutes=self.config.starting_soon_minutes):
            traits.add("calendar_event_starting_soon")
        if any(e.end_at - now <= timedelta(minutes=self.config.ending_soon_minutes) for e in current):
            traits.add("calendar_event_ending_soon")
        return tuple(sorted(traits))

    @staticmethod
    def _confidence(result: CalendarAvailable, events: tuple[CalendarEvent, ...],
                    freshness: CalendarFreshness, conflicts: tuple[CalendarConflict, ...]) -> float:
        score = 1. if result.pagination_complete else .75
        score *= {CalendarFreshness.FRESH: 1., CalendarFreshness.AGING: .85,
                  CalendarFreshness.STALE: .5, CalendarFreshness.EXPIRED: .2}[freshness]
        if isinstance(result, CalendarPartial):
            score -= .15
        if any(e.status is EventStatus.TENTATIVE for e in events):
            score -= .05
        if any(e.user_response is UserResponse.UNKNOWN for e in events):
            score -= .05
        if any(e.visibility is Visibility.PRIVATE for e in events):
            score -= .03
        score -= min(.15, len(conflicts) * .03)
        return round(max(0., min(1., score)), 3)

    def _empty(self, computed: datetime, start: datetime, end: datetime,
               status: CalendarContextStatus, code: str, reason: str,
               freshness: CalendarFreshness = CalendarFreshness.EXPIRED) -> CalendarContext:
        uncertainty = CalendarUncertainty(code, "critical" if status is CalendarContextStatus.INVALID else "warning",
                                          reason, computed, True)
        return CalendarContext(stable_id("cal_ctx", computed, start, end, code), computed, start, end,
            (), (), (), None, None, (), (uncertainty,), freshness, 0., status, (), ())
