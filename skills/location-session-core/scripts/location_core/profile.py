"""Evidence-backed mobility profile contracts and deterministic aggregation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .movement import DataQuality, MovementMode, MovementSegment
from .place import Place, PlaceVisit


class FactStatus(str, Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    STALE = "stale"
    REVOKED = "revoked"


class FactType(str, Enum):
    FREQUENT_PLACE = "frequent_place"
    FREQUENT_OVERNIGHT_PLACE = "frequent_overnight_place"
    FREQUENT_DAYTIME_PLACE = "frequent_daytime_place"
    TYPICAL_VISIT_WINDOW = "typical_visit_window"
    TYPICAL_DWELL_DURATION = "typical_dwell_duration"
    TYPICAL_DEPARTURE_WINDOW = "typical_departure_window"
    TYPICAL_ARRIVAL_WINDOW = "typical_arrival_window"
    FREQUENT_TRANSITION = "frequent_transition"
    TYPICAL_TRANSITION_DURATION = "typical_transition_duration"
    TYPICAL_TRANSITION_MODE = "typical_transition_mode"


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class FactEvidence:
    visit_count: int = 0
    distinct_days: int = 0
    observation_window_days: int = 0
    median_dwell_seconds: float = 0
    overnight_count: int = 0
    weekday_count: int = 0
    transition_count: int = 0
    usable_quality_fraction: float = 0
    outlier_fraction: float = 0


@dataclass(frozen=True)
class PersonalContextFact:
    fact_id: str
    fact_type: FactType
    subject_id: str
    value: Mapping[str, Any]
    confidence: float
    evidence: FactEvidence
    first_observed_at: datetime
    last_observed_at: datetime
    computed_at: datetime
    sample_count: int
    status: FactStatus

    def __post_init__(self) -> None:
        for name in ("first_observed_at", "last_observed_at", "computed_at"):
            _aware(getattr(self, name), name)
        if not 0 <= self.confidence <= 1 or self.sample_count < 1:
            raise ValueError("invalid fact confidence or sample count")


@dataclass(frozen=True)
class PlaceStatistics:
    place_id: str
    visit_count: int
    distinct_days: int
    observed_days: int
    visits_per_week: float
    active_day_fraction: float
    total_dwell_seconds: float
    mean_dwell_seconds: float
    median_dwell_seconds: float
    arrival_minutes: tuple[int, ...]
    departure_minutes: tuple[int, ...]
    dwell_samples: tuple[float, ...]
    active_dates: tuple[str, ...]
    weekday_distribution: tuple[int, ...]
    weekday_count: int
    weekend_count: int
    overnight_count: int
    quality_sum: float
    first_observed_at: datetime
    last_observed_at: datetime


@dataclass(frozen=True)
class TransitionSample:
    sample_id: str
    from_place_id: str
    to_place_id: str
    departed_at: datetime
    arrived_at: datetime
    duration_seconds: float
    mode: MovementMode
    quality: DataQuality


@dataclass(frozen=True)
class PlaceTransitionPattern:
    transition_id: str
    from_place_id: str
    to_place_id: str
    sample_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    typical_duration_seconds: float
    typical_mode: MovementMode
    confidence: float
    status: FactStatus
    duration_range_seconds: tuple[float, float]
    weekday_distribution: tuple[int, ...]


@dataclass(frozen=True)
class ProfileState:
    schema_version: int = 1
    facts: tuple[PersonalContextFact, ...] = ()
    transitions: tuple[PlaceTransitionPattern, ...] = ()
    place_statistics: tuple[PlaceStatistics, ...] = ()
    transition_samples: tuple[TransitionSample, ...] = ()
    seen_visit_ids: tuple[str, ...] = ()
    seen_transition_ids: tuple[str, ...] = ()
    last_computed_at: datetime | None = None


@dataclass(frozen=True)
class ProfileConfig:
    timezone: str
    candidate_visits: int = 3
    confirmed_visits: int = 8
    confirmed_distinct_days: int = 5
    night_start: time = time(22)
    night_end: time = time(6)
    minimum_overnight_seconds: int = 10800
    stale_days: int = 60
    revoke_days: int = 180
    candidate_confidence: float = .35
    confirmed_confidence: float = .65
    transition_minimum_samples: int = 3
    maximum_transition_seconds: int = 21600
    retention_days: int = 730
    deduplication_limit: int = 2048
    state_dir: Path = Path.home() / ".local/state/hermes/profile"

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("HERMES_PROFILE_TIMEZONE must be an IANA timezone") from error
        counts = (self.candidate_visits, self.confirmed_visits,
                  self.confirmed_distinct_days, self.minimum_overnight_seconds,
                  self.stale_days, self.revoke_days, self.transition_minimum_samples,
                  self.maximum_transition_seconds, self.retention_days,
                  self.deduplication_limit)
        if min(counts) < 1 or self.confirmed_visits < self.candidate_visits:
            raise ValueError("invalid profile thresholds")
        if self.revoke_days <= self.stale_days:
            raise ValueError("revoke days must exceed stale days")
        if not 0 <= self.candidate_confidence <= self.confirmed_confidence <= 1:
            raise ValueError("invalid confidence thresholds")
        if self.night_start == self.night_end:
            raise ValueError("night window must not be empty")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> ProfileConfig:
        timezone = env.get("HERMES_PROFILE_TIMEZONE", "").strip()
        if not timezone:
            raise ValueError("HERMES_PROFILE_TIMEZONE is required")
        defaults = cls(timezone=timezone)
        def integer(name: str, default: int) -> int:
            try:
                return int(env.get(name, default))
            except ValueError as error:
                raise ValueError(f"{name} must be an integer") from error
        def number(name: str, default: float) -> float:
            try:
                return float(env.get(name, default))
            except ValueError as error:
                raise ValueError(f"{name} must be numeric") from error
        def clock(name: str, default: time) -> time:
            try:
                return time.fromisoformat(env.get(name, default.isoformat()))
            except ValueError as error:
                raise ValueError(f"{name} must be HH:MM[:SS]") from error
        return cls(timezone, integer("HERMES_PROFILE_CANDIDATE_VISITS", defaults.candidate_visits),
            integer("HERMES_PROFILE_CONFIRMED_VISITS", defaults.confirmed_visits),
            integer("HERMES_PROFILE_CONFIRMED_DISTINCT_DAYS", defaults.confirmed_distinct_days),
            clock("HERMES_PROFILE_NIGHT_START", defaults.night_start),
            clock("HERMES_PROFILE_NIGHT_END", defaults.night_end),
            integer("HERMES_PROFILE_MIN_OVERNIGHT_SECONDS", defaults.minimum_overnight_seconds),
            integer("HERMES_PROFILE_STALE_DAYS", defaults.stale_days),
            integer("HERMES_PROFILE_REVOKE_DAYS", defaults.revoke_days),
            number("HERMES_PROFILE_CANDIDATE_CONFIDENCE", defaults.candidate_confidence),
            number("HERMES_PROFILE_CONFIRMED_CONFIDENCE", defaults.confirmed_confidence),
            integer("HERMES_PROFILE_TRANSITION_MIN_SAMPLES", defaults.transition_minimum_samples),
            integer("HERMES_PROFILE_MAX_TRANSITION_SECONDS", defaults.maximum_transition_seconds),
            integer("HERMES_PROFILE_RETENTION_DAYS", defaults.retention_days),
            integer("HERMES_PROFILE_DEDUPLICATION_LIMIT", defaults.deduplication_limit),
            Path(env.get("HERMES_PROFILE_STATE_DIR", str(defaults.state_dir))))


def _id(prefix: str, *parts: object) -> str:
    raw = json.dumps(parts, default=str, separators=(",", ":")).encode()
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:24]}"


def _quality_score(confidence: float) -> float:
    return 1.0 if confidence >= .75 else .6 if confidence >= .5 else .2


def _quantile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def circular_window(minutes: Iterable[int]) -> tuple[int, int]:
    """Return robust Q1-Q3 clock window, unwrapping at the largest circular gap."""
    ordered = sorted(value % 1440 for value in minutes)
    if not ordered:
        return 0, 0
    if len(ordered) == 1:
        return ordered[0], ordered[0]
    gaps = [((ordered[(i + 1) % len(ordered)] - ordered[i]) % 1440, i)
            for i in range(len(ordered))]
    _, cut = max(gaps)
    start = ordered[(cut + 1) % len(ordered)]
    unwrapped = sorted((value - start) % 1440 for value in ordered)
    return (round(start + _quantile(unwrapped, .25)) % 1440,
            round(start + _quantile(unwrapped, .75)) % 1440)


def overnight_seconds(visit: PlaceVisit, config: ProfileConfig) -> float:
    """Duration overlapping local night windows, calculated on the UTC timeline."""
    _aware(visit.arrived_at, "arrived_at")
    _aware(visit.departed_at, "departed_at")
    zone = ZoneInfo(config.timezone)
    arrival = visit.arrived_at.astimezone(UTC)
    departure = visit.departed_at.astimezone(UTC)
    local_start = arrival.astimezone(zone).date() - timedelta(days=1)
    local_end = departure.astimezone(zone).date() + timedelta(days=1)
    total = 0.0
    current = local_start
    while current <= local_end:
        start = datetime.combine(current, config.night_start, zone)
        end_date = current + timedelta(days=1) if config.night_end <= config.night_start else current
        end = datetime.combine(end_date, config.night_end, zone)
        total += max(0.0, (min(departure, end.astimezone(UTC)) -
                          max(arrival, start.astimezone(UTC))).total_seconds())
        current += timedelta(days=1)
    return total


def _status(samples: int, days: int, confidence: float, last: datetime,
            now: datetime, config: ProfileConfig) -> FactStatus:
    age = (now - last).total_seconds() / 86400
    if age >= config.revoke_days:
        return FactStatus.REVOKED
    if age >= config.stale_days:
        return FactStatus.STALE
    if (samples >= config.confirmed_visits and days >= config.confirmed_distinct_days
            and confidence >= config.confirmed_confidence):
        return FactStatus.CONFIRMED
    return FactStatus.CANDIDATE


def _confidence(samples: int, days: int, quality: float, consistency: float,
                first: datetime, last: datetime, now: datetime, outliers: float,
                config: ProfileConfig) -> float:
    sample_score = min(1.0, samples / config.confirmed_visits)
    day_score = min(1.0, days / config.confirmed_distinct_days)
    span_score = min(1.0, max(1, (last.date() - first.date()).days + 1) / 28)
    recency = max(0.0, 1 - max(0, (now - last).days) / config.revoke_days)
    value = (.25 * sample_score + .2 * day_score + .2 * quality + .15 * consistency
             + .1 * span_score + .1 * recency - .15 * outliers)
    return round(max(0.0, min(1.0, value)), 3)


def _updated_statistics(old: PlaceStatistics | None, visit: PlaceVisit,
                        config: ProfileConfig) -> PlaceStatistics:
    zone = ZoneInfo(config.timezone)
    local_arrival = visit.arrived_at.astimezone(zone)
    local_departure = visit.departed_at.astimezone(zone)
    arrivals = (old.arrival_minutes if old else ()) + (local_arrival.hour * 60 + local_arrival.minute,)
    departures = (old.departure_minutes if old else ()) + (local_departure.hour * 60 + local_departure.minute,)
    # Bounded duration samples are represented through aggregates plus arrival bucket count.
    count = (old.visit_count if old else 0) + 1
    total = (old.total_dwell_seconds if old else 0) + visit.duration_seconds
    # Incremental median needs bounded samples: durations are encoded alongside departure buckets
    # as a derived rolling estimate; rebuild applies visits in the same deterministic order.
    dwell_samples = ((old.dwell_samples if old else ()) + (visit.duration_seconds,))[-512:]
    robust_median = median(dwell_samples)
    weekdays = list(old.weekday_distribution if old else (0,) * 7)
    weekdays[local_arrival.weekday()] += 1
    first = min(old.first_observed_at, visit.arrived_at) if old else visit.arrived_at
    last = max(old.last_observed_at, visit.departed_at) if old else visit.departed_at
    # Active dates are conservatively reconstructed from distribution; repeated-day protection
    # uses retained local date keys embedded in seen IDs at engine level.
    active_dates = tuple(sorted(set((old.active_dates if old else ()) +
                                    (local_arrival.date().isoformat(),))))[-512:]
    distinct = len(active_dates)
    observed = max(1, (last.astimezone(zone).date() - first.astimezone(zone).date()).days + 1)
    overnight = int(overnight_seconds(visit, config) >= config.minimum_overnight_seconds)
    return PlaceStatistics(visit.place_id, count, distinct, observed,
        round(count / max(1, observed) * 7, 3), round(distinct / observed, 3), total,
        total / count, robust_median, arrivals[-512:], departures[-512:], dwell_samples,
        active_dates, tuple(weekdays),
        sum(weekdays[:5]), sum(weekdays[5:]), (old.overnight_count if old else 0) + overnight,
        (old.quality_sum if old else 0) + _quality_score(visit.confidence), first, last)


class MobilityProfileEngine:
    """Pure shadow-mode engine: consumes abstract visits and segments, emits no actions."""

    def __init__(self, config: ProfileConfig, state: ProfileState | None = None):
        self.config = config
        self.state = state or ProfileState()

    def process_visit(self, visit: PlaceVisit, place: Place | None = None,
                      movement_context: object | None = None,
                      *, computed_at: datetime | None = None) -> ProfileState:
        del movement_context
        if place is not None and place.place_id != visit.place_id:
            raise ValueError("visit and place identifiers differ")
        _aware(visit.arrived_at, "arrived_at")
        _aware(visit.departed_at, "departed_at")
        if visit.departed_at <= visit.arrived_at or visit.duration_seconds <= 0:
            raise ValueError("visit duration must be positive")
        if visit.visit_id in self.state.seen_visit_ids:
            return self.state
        stats = {item.place_id: item for item in self.state.place_statistics}
        stats[visit.place_id] = _updated_statistics(stats.get(visit.place_id), visit, self.config)
        now = computed_at or visit.departed_at
        _aware(now, "computed_at")
        seen = (self.state.seen_visit_ids + (visit.visit_id,))[-self.config.deduplication_limit:]
        self.state = replace(self.state, place_statistics=tuple(sorted(stats.values(), key=lambda x: x.place_id)),
                             seen_visit_ids=seen, last_computed_at=now)
        self._compute_place_facts(now)
        return self.state

    def _compute_place_facts(self, now: datetime) -> None:
        transition_facts = [f for f in self.state.facts if f.fact_type in {
            FactType.FREQUENT_TRANSITION, FactType.TYPICAL_TRANSITION_DURATION,
            FactType.TYPICAL_TRANSITION_MODE}]
        facts = transition_facts
        for stat in self.state.place_statistics:
            if stat.visit_count < self.config.candidate_visits:
                continue
            quality = stat.quality_sum / stat.visit_count
            q1, q3 = circular_window(stat.arrival_minutes)
            widths = (q3 - q1) % 1440
            consistency = max(0.0, 1 - widths / 720)
            confidence = _confidence(stat.visit_count, stat.distinct_days, quality, consistency,
                stat.first_observed_at, stat.last_observed_at, now, 0, self.config)
            status = _status(stat.visit_count, stat.distinct_days, confidence,
                             stat.last_observed_at, now, self.config)
            evidence = FactEvidence(stat.visit_count, stat.distinct_days, stat.observed_days,
                stat.median_dwell_seconds, stat.overnight_count, stat.weekday_count,
                usable_quality_fraction=round(quality, 3))
            types = [FactType.FREQUENT_PLACE, FactType.TYPICAL_VISIT_WINDOW,
                     FactType.TYPICAL_DWELL_DURATION, FactType.TYPICAL_ARRIVAL_WINDOW,
                     FactType.TYPICAL_DEPARTURE_WINDOW]
            if stat.overnight_count / stat.visit_count >= .5:
                types.append(FactType.FREQUENT_OVERNIGHT_PLACE)
            elif stat.overnight_count == 0:
                types.append(FactType.FREQUENT_DAYTIME_PLACE)
            for kind in types:
                value: Mapping[str, Any] = {"place_id": stat.place_id}
                if kind in {FactType.TYPICAL_VISIT_WINDOW, FactType.TYPICAL_ARRIVAL_WINDOW}:
                    value = {**value, "start_minute": q1, "end_minute": q3}
                elif kind is FactType.TYPICAL_DEPARTURE_WINDOW:
                    start, end = circular_window(stat.departure_minutes)
                    value = {**value, "start_minute": start, "end_minute": end}
                elif kind is FactType.TYPICAL_DWELL_DURATION:
                    value = {**value, "seconds": round(stat.median_dwell_seconds)}
                facts.append(PersonalContextFact(_id("fact", kind.value, stat.place_id), kind,
                    stat.place_id, value, confidence, evidence, stat.first_observed_at,
                    stat.last_observed_at, now, stat.visit_count, status))
        self.state = replace(self.state, facts=tuple(sorted(facts, key=lambda f: f.fact_id)))

    def rebuild(self, visits: Iterable[PlaceVisit], *, computed_at: datetime | None = None) -> ProfileState:
        ordered = sorted(visits, key=lambda v: (v.arrived_at, v.visit_id))
        self.state = ProfileState()
        for visit in ordered:
            self.process_visit(visit, computed_at=computed_at or visit.departed_at)
        if computed_at and ordered:
            self._compute_place_facts(computed_at)
            self.state = replace(self.state, last_computed_at=computed_at)
        return self.state

    def process_transition(self, departure: PlaceVisit, arrival: PlaceVisit,
                           segments: Iterable[MovementSegment], *, data_gap: bool = False,
                           unknown_intermediate_stay: bool = False,
                           computed_at: datetime | None = None) -> ProfileState:
        """Aggregate a plausible departure→movement→arrival chain without route geometry."""
        if departure.place_id == arrival.place_id or data_gap or unknown_intermediate_stay:
            return self.state
        started = departure.departed_at
        ended = arrival.arrived_at
        duration = (ended - started).total_seconds()
        if duration <= 0 or duration > self.config.maximum_transition_seconds:
            return self.state
        relevant = [segment for segment in segments
                    if segment.started_at <= ended and (segment.ended_at or ended) >= started]
        if not relevant or any(segment.gap_count for segment in relevant):
            return self.state
        coverage_start = min(segment.started_at for segment in relevant)
        coverage_end = max(segment.ended_at or ended for segment in relevant)
        if (coverage_start - started).total_seconds() > 300 or (ended - coverage_end).total_seconds() > 300:
            return self.state
        sample_id = _id("transition-sample", departure.visit_id, arrival.visit_id)
        if sample_id in self.state.seen_transition_ids:
            return self.state
        weighted: dict[MovementMode, float] = {}
        for segment in relevant:
            weighted[segment.mode] = weighted.get(segment.mode, 0) + max(1, segment.duration_seconds)
        mode = max(weighted, key=lambda item: (weighted[item], item.value))
        quality = min((segment.data_quality for segment in relevant),
                      key=lambda q: {DataQuality.INVALID: 0, DataQuality.POOR: 1,
                                     DataQuality.LIMITED: 2, DataQuality.GOOD: 3}[q])
        sample = TransitionSample(sample_id, departure.place_id, arrival.place_id,
                                  started, ended, duration, mode, quality)
        samples = (self.state.transition_samples + (sample,))[-self.config.retention_days * 20:]
        seen = (self.state.seen_transition_ids + (sample_id,))[-self.config.deduplication_limit:]
        now = computed_at or ended
        self.state = replace(self.state, transition_samples=samples,
                             seen_transition_ids=seen, last_computed_at=now)
        self._compute_transitions(now)
        return self.state

    def _compute_transitions(self, now: datetime) -> None:
        groups: dict[tuple[str, str], list[TransitionSample]] = {}
        for sample in self.state.transition_samples:
            groups.setdefault((sample.from_place_id, sample.to_place_id), []).append(sample)
        patterns: list[PlaceTransitionPattern] = []
        facts = [fact for fact in self.state.facts if fact.fact_type not in {
            FactType.FREQUENT_TRANSITION, FactType.TYPICAL_TRANSITION_DURATION,
            FactType.TYPICAL_TRANSITION_MODE}]
        zone = ZoneInfo(self.config.timezone)
        for (source, target), samples in sorted(groups.items()):
            if len(samples) < self.config.transition_minimum_samples:
                continue
            durations = [sample.duration_seconds for sample in samples]
            q1, q3 = _quantile(durations, .25), _quantile(durations, .75)
            iqr = max(1, q3 - q1)
            outliers = sum(d < q1 - 1.5 * iqr or d > q3 + 1.5 * iqr for d in durations) / len(samples)
            days = len({sample.departed_at.astimezone(zone).date() for sample in samples})
            quality = sum({DataQuality.GOOD: 1, DataQuality.LIMITED: .65,
                           DataQuality.POOR: .25, DataQuality.INVALID: 0}[s.quality]
                          for s in samples) / len(samples)
            mode_counts: dict[MovementMode, int] = {}
            weekdays = [0] * 7
            for sample in samples:
                mode_counts[sample.mode] = mode_counts.get(sample.mode, 0) + 1
                weekdays[sample.departed_at.astimezone(zone).weekday()] += 1
            mode = max(mode_counts, key=lambda item: (mode_counts[item], item.value))
            consistency = max(mode_counts.values()) / len(samples)
            first = min(sample.departed_at for sample in samples)
            last = max(sample.arrived_at for sample in samples)
            confidence = _confidence(len(samples), days, quality, consistency, first, last,
                                     now, outliers, self.config)
            status = _status(len(samples), days, confidence, last, now, self.config)
            transition_id = _id("transition", source, target)
            pattern = PlaceTransitionPattern(transition_id, source, target, len(samples), first,
                last, median(durations), mode, confidence, status, (q1, q3), tuple(weekdays))
            patterns.append(pattern)
            evidence = FactEvidence(distinct_days=days,
                observation_window_days=max(1, (last.date() - first.date()).days + 1),
                transition_count=len(samples), usable_quality_fraction=round(quality, 3),
                outlier_fraction=round(outliers, 3))
            values: dict[FactType, Mapping[str, Any]] = {
                FactType.FREQUENT_TRANSITION: {"from_place_id": source, "to_place_id": target},
                FactType.TYPICAL_TRANSITION_DURATION: {"seconds": round(pattern.typical_duration_seconds)},
                FactType.TYPICAL_TRANSITION_MODE: {"mode": mode.value},
            }
            for kind, value in values.items():
                facts.append(PersonalContextFact(_id("fact", kind.value, transition_id), kind,
                    transition_id, value, confidence, evidence, first, last, now,
                    len(samples), status))
        self.state = replace(self.state, transitions=tuple(patterns),
                             facts=tuple(sorted(facts, key=lambda f: f.fact_id)))

    def explain(self, fact_id: str) -> str:
        fact = next((item for item in self.state.facts if item.fact_id == fact_id), None)
        if fact is None:
            raise KeyError(fact_id)
        e = fact.evidence
        return (f"Fact:\n{fact.fact_type.value}\n\nConfidence:\n{fact.confidence:.3f}\n\nEvidence:\n"
                f"- {e.visit_count} visits\n- {e.distinct_days} distinct days\n"
                f"- observed across {e.observation_window_days} days\n"
                f"- median dwell {round(e.median_dwell_seconds)}s\n"
                f"- {e.overnight_count} overnight stays\n"
                f"- {e.usable_quality_fraction:.0%} usable data quality")
