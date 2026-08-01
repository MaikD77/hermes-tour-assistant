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
class VisitEvidence:
    """Coordinate-free retained input sufficient to reproduce place aggregates."""

    visit_id: str
    place_id: str
    arrived_at: datetime
    departed_at: datetime
    duration_seconds: float
    confidence: float


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
    schema_version: int = 2
    facts: tuple[PersonalContextFact, ...] = ()
    transitions: tuple[PlaceTransitionPattern, ...] = ()
    place_statistics: tuple[PlaceStatistics, ...] = ()
    transition_samples: tuple[TransitionSample, ...] = ()
    visit_evidence: tuple[VisitEvidence, ...] = ()
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
    transition_confirmed_samples: int = 8
    transition_confirmed_distinct_days: int = 5
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
                  self.transition_confirmed_samples, self.transition_confirmed_distinct_days,
                  self.maximum_transition_seconds, self.retention_days,
                  self.deduplication_limit)
        if min(counts) < 1 or self.confirmed_visits < self.candidate_visits:
            raise ValueError("invalid profile thresholds")
        if self.transition_confirmed_samples < self.transition_minimum_samples:
            raise ValueError("transition confirmation must not precede candidacy")
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
            integer("HERMES_PROFILE_TRANSITION_CONFIRMED_SAMPLES", defaults.transition_confirmed_samples),
            integer("HERMES_PROFILE_TRANSITION_CONFIRMED_DISTINCT_DAYS", defaults.transition_confirmed_distinct_days),
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
            now: datetime, config: ProfileConfig, *, candidate_samples: int,
            confirmed_samples: int, confirmed_days: int,
            qualifying_confidence: float | None = None) -> FactStatus | None:
    if samples < candidate_samples:
        return None
    age = (now - last).total_seconds() / 86400
    qualified = confidence if qualifying_confidence is None else qualifying_confidence
    if age >= config.revoke_days:
        return FactStatus.REVOKED if qualified >= config.candidate_confidence else None
    if age >= config.stale_days:
        return FactStatus.STALE if qualified >= config.candidate_confidence else None
    if confidence < config.candidate_confidence:
        return None
    if (samples >= confirmed_samples and days >= confirmed_days
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
        if any(item.visit_id == visit.visit_id for item in self.state.visit_evidence):
            return self.state
        now = computed_at or visit.departed_at
        _aware(now, "computed_at")
        evidence = self.state.visit_evidence + (VisitEvidence(visit.visit_id, visit.place_id,
            visit.arrived_at, visit.departed_at, visit.duration_seconds, visit.confidence),)
        self.state = replace(self.state, visit_evidence=evidence, last_computed_at=now)
        self._apply_retention(now)
        self._recompute_place_statistics()
        self._compute_place_facts(now)
        return self.state

    def _apply_retention(self, now: datetime) -> None:
        cutoff = now - timedelta(days=self.config.retention_days)
        visits = tuple(item for item in self.state.visit_evidence if item.departed_at >= cutoff)
        transitions = tuple(item for item in self.state.transition_samples
                            if item.arrived_at >= cutoff)
        self.state = replace(self.state, visit_evidence=visits, transition_samples=transitions,
            seen_visit_ids=tuple(item.visit_id for item in visits)[-self.config.deduplication_limit:],
            seen_transition_ids=tuple(item.sample_id for item in transitions)[-self.config.deduplication_limit:])

    def _recompute_place_statistics(self) -> None:
        groups: dict[str, list[VisitEvidence]] = {}
        for item in self.state.visit_evidence:
            groups.setdefault(item.place_id, []).append(item)
        zone = ZoneInfo(self.config.timezone)
        statistics: list[PlaceStatistics] = []
        for place_id, items in sorted(groups.items()):
            items.sort(key=lambda item: (item.arrived_at, item.visit_id))
            arrivals = tuple(i.arrived_at.astimezone(zone).hour * 60 + i.arrived_at.astimezone(zone).minute for i in items)
            departures = tuple(i.departed_at.astimezone(zone).hour * 60 + i.departed_at.astimezone(zone).minute for i in items)
            durations = tuple(i.duration_seconds for i in items)
            dates = tuple(sorted({i.arrived_at.astimezone(zone).date().isoformat() for i in items}))
            weekdays = [0] * 7
            overnight = 0
            for item in items:
                weekdays[item.arrived_at.astimezone(zone).weekday()] += 1
                pseudo = PlaceVisit(item.visit_id, item.place_id, "retained", item.arrived_at,
                    item.departed_at, item.duration_seconds, None, None, item.confidence)
                overnight += overnight_seconds(pseudo, self.config) >= self.config.minimum_overnight_seconds
            first, last = items[0].arrived_at, max(item.departed_at for item in items)
            observed = max(1, (last.astimezone(zone).date() - first.astimezone(zone).date()).days + 1)
            total = sum(durations)
            statistics.append(PlaceStatistics(place_id, len(items), len(dates), observed,
                round(len(items) / observed * 7, 3), round(len(dates) / observed, 3), total,
                total / len(items), median(durations), arrivals, departures, durations, dates,
                tuple(weekdays), sum(weekdays[:5]), sum(weekdays[5:]), overnight,
                sum(_quality_score(item.confidence) for item in items), first, last))
        self.state = replace(self.state, place_statistics=tuple(statistics))

    def maintain(self, computed_at: datetime) -> ProfileState:
        """Apply time-based retention and lifecycle maintenance without new evidence."""
        _aware(computed_at, "computed_at")
        self._apply_retention(computed_at)
        self._recompute_place_statistics()
        self._compute_place_facts(computed_at)
        self._compute_transitions(computed_at)
        self.state = replace(self.state, last_computed_at=computed_at)
        return self.state

    def _compute_place_facts(self, now: datetime) -> None:
        transition_facts = [f for f in self.state.facts if f.fact_type in {
            FactType.FREQUENT_TRANSITION, FactType.TYPICAL_TRANSITION_DURATION,
            FactType.TYPICAL_TRANSITION_MODE}]
        facts = transition_facts
        for stat in self.state.place_statistics:
            quality = stat.quality_sum / stat.visit_count
            q1, q3 = circular_window(stat.arrival_minutes)
            widths = (q3 - q1) % 1440
            consistency = max(0.0, 1 - widths / 720)
            confidence = _confidence(stat.visit_count, stat.distinct_days, quality, consistency,
                stat.first_observed_at, stat.last_observed_at, now, 0, self.config)
            status = _status(stat.visit_count, stat.distinct_days, confidence,
                stat.last_observed_at, now, self.config,
                candidate_samples=self.config.candidate_visits,
                confirmed_samples=self.config.confirmed_visits,
                confirmed_days=self.config.confirmed_distinct_days,
                qualifying_confidence=_confidence(stat.visit_count, stat.distinct_days,
                    quality, consistency, stat.first_observed_at, stat.last_observed_at,
                    stat.last_observed_at, 0, self.config))
            if status is None:
                continue
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
        final_now = computed_at or (ordered[-1].departed_at if ordered else None)
        for visit in ordered:
            self.process_visit(visit, computed_at=final_now or visit.departed_at)
        if final_now:
            self.maintain(final_now)
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
        if any(item.sample_id == sample_id for item in self.state.transition_samples):
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
        samples = self.state.transition_samples + (sample,)
        now = computed_at or ended
        self.state = replace(self.state, transition_samples=samples, last_computed_at=now)
        self._apply_retention(now)
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
            status = _status(len(samples), days, confidence, last, now, self.config,
                candidate_samples=self.config.transition_minimum_samples,
                confirmed_samples=self.config.transition_confirmed_samples,
                confirmed_days=self.config.transition_confirmed_distinct_days,
                qualifying_confidence=_confidence(len(samples), days, quality, consistency,
                    first, last, last, outliers, self.config))
            if status is None:
                continue
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
