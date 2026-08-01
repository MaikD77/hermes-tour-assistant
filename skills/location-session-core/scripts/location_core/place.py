"""Deterministic private Place, Stay and Visit inference.

Places are spatial clusters learned from stays.  They are deliberately neither
POIs nor addresses and carry no semantic label.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from statistics import median
from typing import Mapping

from .location_sources import LocationObservation
from .movement import DataQuality, MovementMode, MovementState
from .route_engine import haversine_m

Point = tuple[float, float]


class PlaceStatus(str, Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    ARCHIVED = "archived"


class StayStatus(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    COMPLETED = "completed"
    DISCARDED = "discarded"


class MatchStatus(str, Enum):
    MATCHED = "matched"
    NEW_CANDIDATE = "new_candidate"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENT_QUALITY = "insufficient_quality"


class PlaceProcessingStatus(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CANDIDATE = "candidate"
    STAY_ACTIVE = "stay_active"
    ARRIVAL_CONFIRMED = "arrival_confirmed"
    DEPARTURE_CONFIRMED = "departure_confirmed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    AMBIGUOUS = "ambiguous"
    GAP_DETECTED = "gap_detected"


class PlaceEventType(str, Enum):
    STAY_CANDIDATE_STARTED = "stay.candidate_started"
    STAY_CONFIRMED = "stay.confirmed"
    STAY_COMPLETED = "stay.completed"
    STAY_DISCARDED = "stay.discarded"
    PLACE_CANDIDATE_CREATED = "place.candidate_created"
    PLACE_CONFIRMED = "place.confirmed"
    PLACE_REVISITED = "place.revisited"
    PLACE_DEPARTED = "place.departed"
    PLACE_MATCH_AMBIGUOUS = "place.match_ambiguous"


@dataclass(frozen=True)
class Place:
    place_id: str
    centroid: Point
    radius_m: float
    confidence: float
    first_seen_at: datetime
    last_seen_at: datetime
    visit_count: int
    total_dwell_seconds: float
    typical_dwell_seconds: float
    data_quality: DataQuality
    status: PlaceStatus


@dataclass(frozen=True)
class Stay:
    stay_id: str
    place_id: str | None
    started_at: datetime
    ended_at: datetime | None
    status: StayStatus
    duration_seconds: float
    observation_count: int
    centroid: Point
    radius_m: float
    confidence: float
    data_quality: DataQuality
    arrival_mode: MovementMode | None = None
    departure_mode: MovementMode | None = None


@dataclass(frozen=True)
class PlaceVisit:
    visit_id: str
    place_id: str
    stay_id: str
    arrived_at: datetime
    departed_at: datetime
    duration_seconds: float
    arrival_mode: MovementMode | None
    departure_mode: MovementMode | None
    confidence: float


@dataclass(frozen=True)
class PlaceEvent:
    event_id: str
    event_type: PlaceEventType
    observed_at: datetime
    confirmed_at: datetime
    place_id: str | None
    stay_id: str
    confidence: float
    evidence: tuple[str, ...]
    data_quality: DataQuality


@dataclass(frozen=True)
class PlaceMatch:
    status: MatchStatus
    place_id: str | None = None
    candidate_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlaceConfig:
    stay_radius_m: float = 50.0
    candidate_seconds: float = 120.0
    confirmed_seconds: float = 300.0
    minimum_observations: int = 3
    departure_radius_m: float = 80.0
    departure_confirmation_seconds: float = 120.0
    match_radius_m: float = 75.0
    maximum_accuracy_m: float = 100.0
    short_gap_seconds: float = 300.0
    long_gap_seconds: float = 1800.0
    promotion_visits: int = 3
    promotion_dwell_seconds: float = 1800.0
    visit_retention: int = 200
    deduplication_retention: int = 256
    centroid_precision: int = 4
    state_dir: Path = Path.home() / ".local/state/hermes/places"

    def __post_init__(self) -> None:
        values = (self.stay_radius_m, self.candidate_seconds, self.confirmed_seconds,
                  self.departure_radius_m, self.departure_confirmation_seconds,
                  self.match_radius_m, self.maximum_accuracy_m, self.short_gap_seconds,
                  self.long_gap_seconds, self.promotion_dwell_seconds)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("place thresholds must be finite and positive")
        if self.departure_radius_m <= self.stay_radius_m:
            raise ValueError("departure radius must exceed arrival radius")
        if self.confirmed_seconds < self.candidate_seconds:
            raise ValueError("confirmation must not precede candidacy")
        if self.long_gap_seconds <= self.short_gap_seconds:
            raise ValueError("long gap must exceed short gap")
        if min(self.minimum_observations, self.promotion_visits,
               self.visit_retention, self.deduplication_retention) < 1:
            raise ValueError("place counts must be positive")
        if not 3 <= self.centroid_precision <= 6:
            raise ValueError("centroid precision must be between 3 and 6 decimals")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> PlaceConfig:
        defaults = cls()

        def number(name: str, default: float) -> float:
            try:
                return float(env.get(name, default))
            except ValueError as error:
                raise ValueError(f"{name} must be numeric") from error

        def integer(name: str, default: int) -> int:
            raw = env.get(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except ValueError as error:
                raise ValueError(f"{name} must be an integer") from error

        state_dir = env.get("HERMES_PLACE_STATE_DIR", str(defaults.state_dir)).strip()
        if not state_dir:
            raise ValueError("HERMES_PLACE_STATE_DIR must not be empty")
        return cls(
            stay_radius_m=number("HERMES_PLACE_STAY_RADIUS_M", defaults.stay_radius_m),
            candidate_seconds=number("HERMES_PLACE_CANDIDATE_SECONDS", defaults.candidate_seconds),
            confirmed_seconds=number("HERMES_PLACE_CONFIRMED_SECONDS", defaults.confirmed_seconds),
            minimum_observations=integer(
                "HERMES_PLACE_MINIMUM_OBSERVATIONS", defaults.minimum_observations
            ),
            departure_radius_m=number("HERMES_PLACE_DEPARTURE_RADIUS_M", defaults.departure_radius_m),
            departure_confirmation_seconds=number("HERMES_PLACE_DEPARTURE_SECONDS", defaults.departure_confirmation_seconds),
            match_radius_m=number("HERMES_PLACE_MATCH_RADIUS_M", defaults.match_radius_m),
            maximum_accuracy_m=number(
                "HERMES_PLACE_MAXIMUM_ACCURACY_M", defaults.maximum_accuracy_m
            ),
            short_gap_seconds=number(
                "HERMES_PLACE_SHORT_GAP_SECONDS", defaults.short_gap_seconds
            ),
            long_gap_seconds=number(
                "HERMES_PLACE_LONG_GAP_SECONDS", defaults.long_gap_seconds
            ),
            promotion_visits=integer(
                "HERMES_PLACE_PROMOTION_VISITS", defaults.promotion_visits
            ),
            promotion_dwell_seconds=number(
                "HERMES_PLACE_PROMOTION_DWELL_SECONDS", defaults.promotion_dwell_seconds
            ),
            visit_retention=integer(
                "HERMES_PLACE_VISIT_RETENTION", defaults.visit_retention
            ),
            deduplication_retention=integer(
                "HERMES_PLACE_DEDUPLICATION_RETENTION", defaults.deduplication_retention
            ),
            centroid_precision=integer(
                "HERMES_PLACE_CENTROID_PRECISION", defaults.centroid_precision
            ),
            state_dir=Path(state_dir),
        )


@dataclass(frozen=True)
class CandidateAccumulator:
    stay: Stay
    latitude_sum: float
    longitude_sum: float
    good_quality_count: int = 0
    limited_quality_count: int = 0
    poor_quality_count: int = 0
    departure_observed_at: datetime | None = None
    departure_mode: MovementMode | None = None


@dataclass(frozen=True)
class PlaceEngineState:
    schema_version: int = 2
    candidate: CandidateAccumulator | None = None
    active_stay: CandidateAccumulator | None = None
    places: tuple[Place, ...] = ()
    visits: tuple[PlaceVisit, ...] = ()
    seen_ids: tuple[str, ...] = ()
    emitted_event_ids: tuple[str, ...] = ()
    last_observation_id: str | None = None
    last_observed_at: datetime | None = None


@dataclass(frozen=True)
class PlaceResult:
    state: PlaceEngineState
    active_stay: Stay | None
    events: tuple[PlaceEvent, ...]
    match: PlaceMatch | None
    status: PlaceProcessingStatus
    data_quality: DataQuality
    diagnostic: tuple[str, ...]


def _id(prefix: str, *values: object) -> str:
    encoded = json.dumps(values, separators=(",", ":"), default=str).encode()
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _quality(observation: LocationObservation, maximum_accuracy_m: float = 100.0) -> DataQuality:
    if observation.accuracy_m is None:
        return DataQuality.LIMITED
    if observation.accuracy_m <= 30:
        return DataQuality.GOOD
    if observation.accuracy_m <= maximum_accuracy_m:
        return DataQuality.LIMITED
    return DataQuality.POOR


def _confidence(quality: DataQuality, count: int, duration: float,
                poor_fraction: float = 0.0) -> float:
    base = {DataQuality.GOOD: .6, DataQuality.LIMITED: .4,
            DataQuality.POOR: .15, DataQuality.INVALID: 0}[quality]
    confidence = base + min(count, 5) * .05 + min(duration / 6000, .15)
    return round(max(0.0, min(1.0, confidence - poor_fraction * .35)), 3)


def _quality_counts(quality: DataQuality) -> tuple[int, int, int]:
    """Return bounded aggregate evidence; INVALID is deliberately not evidence."""
    if quality is DataQuality.GOOD:
        return 1, 0, 0
    if quality is DataQuality.LIMITED:
        return 0, 1, 0
    if quality is DataQuality.POOR:
        return 0, 0, 1
    return 0, 0, 0


def _add_quality_evidence(good: int, limited: int, poor: int,
                          quality: DataQuality) -> tuple[int, int, int]:
    """Add evidence while keeping counters bounded and their distribution stable."""
    if good + limited + poor >= 1024:
        good, limited, poor = good // 2, limited // 2, poor // 2
    added_good, added_limited, added_poor = _quality_counts(quality)
    return good + added_good, limited + added_limited, poor + added_poor


def aggregate_quality(good: int, limited: int, poor: int) -> DataQuality:
    """Classify the distribution without letting one outlier dominate.

    GOOD requires at least 60% good evidence, POOR at least 50% poor evidence;
    all other valid mixtures are LIMITED. Empty/invalid-only evidence is INVALID.
    """
    if min(good, limited, poor) < 0:
        raise ValueError("quality counts cannot be negative")
    total = good + limited + poor
    if total == 0:
        return DataQuality.INVALID
    if poor * 2 >= total:
        return DataQuality.POOR
    if good * 5 >= total * 3:
        return DataQuality.GOOD
    return DataQuality.LIMITED


class PlaceEngine:
    """Incremental bounded-state engine with deterministic spatial hysteresis."""

    def __init__(self, config: PlaceConfig | None = None,
                 state: PlaceEngineState | None = None) -> None:
        self.config = config or PlaceConfig()
        self.state = state or PlaceEngineState()

    def _event(self, kind: PlaceEventType, stay: Stay, confirmed_at: datetime,
               evidence: tuple[str, ...], place_id: str | None = None,
               observed_at: datetime | None = None) -> PlaceEvent:
        return PlaceEvent(
            _id("event", kind.value, stay.stay_id, confirmed_at.isoformat()), kind,
            observed_at or (stay.started_at if kind in {
                PlaceEventType.STAY_CANDIDATE_STARTED,
                PlaceEventType.STAY_CONFIRMED} else confirmed_at),
            confirmed_at, place_id if place_id is not None else stay.place_id,
            stay.stay_id, stay.confidence, evidence, stay.data_quality,
        )

    def _append_events(self, events: list[PlaceEvent]) -> tuple[PlaceEvent, ...]:
        known = set(self.state.emitted_event_ids)
        unique = tuple(event for event in events if event.event_id not in known)
        retained = (self.state.emitted_event_ids + tuple(e.event_id for e in unique))[
            -self.config.deduplication_retention:]
        self.state = replace(self.state, emitted_event_ids=retained)
        return unique

    def _new_candidate(self, observation: LocationObservation, quality: DataQuality,
                       mode: MovementMode | None) -> CandidateAccumulator:
        stay = Stay(
            _id("stay", observation.device_id, observation.observed_at.isoformat(),
                observation.observation_id), None, observation.observed_at, None,
            StayStatus.CANDIDATE, 0, 1, (observation.latitude, observation.longitude),
            observation.accuracy_m or self.config.stay_radius_m,
            _confidence(quality, 1, 0), quality, mode, None,
        )
        good, limited, poor = _quality_counts(quality)
        return CandidateAccumulator(stay, observation.latitude, observation.longitude,
                                    good, limited, poor)

    def _update(self, accumulator: CandidateAccumulator,
                observation: LocationObservation, quality: DataQuality) -> CandidateAccumulator:
        count = accumulator.stay.observation_count + 1
        lat_sum = accumulator.latitude_sum + observation.latitude
        lon_sum = accumulator.longitude_sum + observation.longitude
        centroid = (lat_sum / count, lon_sum / count)
        duration = max(0.0, (observation.observed_at - accumulator.stay.started_at).total_seconds())
        radius = max(accumulator.stay.radius_m,
                     haversine_m(centroid[0], centroid[1], observation.latitude,
                                 observation.longitude))
        good, limited, poor = _add_quality_evidence(
            accumulator.good_quality_count, accumulator.limited_quality_count,
            accumulator.poor_quality_count, quality
        )
        combined_quality = aggregate_quality(good, limited, poor)
        valid_count = good + limited + poor
        poor_fraction = poor / valid_count if valid_count else 0.0
        stay = replace(accumulator.stay, duration_seconds=duration,
                       observation_count=count, centroid=centroid, radius_m=round(radius, 2),
                       confidence=_confidence(combined_quality, valid_count, duration,
                                              poor_fraction),
                       data_quality=combined_quality)
        return CandidateAccumulator(stay, lat_sum, lon_sum,
                                    good, limited, poor,
                                    accumulator.departure_observed_at,
                                    accumulator.departure_mode)

    def _update_quality_only(self, accumulator: CandidateAccumulator,
                             observation: LocationObservation,
                             quality: DataQuality) -> CandidateAccumulator:
        """Record poor evidence without letting an unreliable point move the cluster."""
        good, limited, poor = _add_quality_evidence(
            accumulator.good_quality_count, accumulator.limited_quality_count,
            accumulator.poor_quality_count, quality
        )
        valid_count = good + limited + poor
        aggregate = aggregate_quality(good, limited, poor)
        duration = max(
            0.0, (observation.observed_at - accumulator.stay.started_at).total_seconds()
        )
        stay = replace(
            accumulator.stay,
            duration_seconds=duration,
            observation_count=accumulator.stay.observation_count + 1,
            confidence=_confidence(
                aggregate, valid_count, duration, poor / valid_count if valid_count else 0.0
            ),
            data_quality=aggregate,
        )
        return replace(accumulator, stay=stay, good_quality_count=good,
                       limited_quality_count=limited, poor_quality_count=poor)

    def match(self, stay: Stay) -> PlaceMatch:
        if stay.data_quality in {DataQuality.POOR, DataQuality.INVALID}:
            return PlaceMatch(MatchStatus.INSUFFICIENT_QUALITY)
        matches: list[tuple[float, Place]] = []
        for place in self.state.places:
            if place.status is PlaceStatus.ARCHIVED:
                continue
            distance = haversine_m(stay.centroid[0], stay.centroid[1],
                                   place.centroid[0], place.centroid[1])
            threshold = max(self.config.match_radius_m,
                            min(150.0, stay.radius_m + place.radius_m))
            if distance <= threshold:
                matches.append((distance, place))
        matches.sort(key=lambda item: (item[0], item[1].place_id))
        if not matches:
            return PlaceMatch(MatchStatus.NEW_CANDIDATE)
        if len(matches) > 1 and matches[1][0] - matches[0][0] < 20:
            return PlaceMatch(MatchStatus.AMBIGUOUS, candidate_ids=tuple(
                item[1].place_id for item in matches))
        return PlaceMatch(MatchStatus.MATCHED, matches[0][1].place_id)

    def _associate(self, accumulator: CandidateAccumulator, now: datetime,
                   events: list[PlaceEvent]) -> tuple[CandidateAccumulator, PlaceMatch]:
        stay, match = accumulator.stay, self.match(accumulator.stay)
        if match.status is MatchStatus.AMBIGUOUS:
            events.append(self._event(PlaceEventType.PLACE_MATCH_AMBIGUOUS, stay, now,
                                      ("multiple_spatial_matches",)))
            return accumulator, match
        if match.status is MatchStatus.INSUFFICIENT_QUALITY:
            return accumulator, match
        if match.status is MatchStatus.NEW_CANDIDATE:
            # Identity is seeded once from the immutable first stay, never recomputed
            # from a moving centroid.
            place_id = _id("place", stay.stay_id)
            place = Place(place_id, stay.centroid, stay.radius_m, stay.confidence,
                          stay.started_at, now, 0, 0, 0, stay.data_quality,
                          PlaceStatus.CANDIDATE)
            self.state = replace(self.state, places=self.state.places + (place,))
            events.append(self._event(PlaceEventType.PLACE_CANDIDATE_CREATED, stay, now,
                                      ("first_qualified_stay",), place_id))
        else:
            assert match.place_id is not None
            place_id = match.place_id
            events.append(self._event(PlaceEventType.PLACE_REVISITED, stay, now,
                                      ("spatial_overlap", "stable_identity"), place_id))
        return replace(accumulator, stay=replace(stay, place_id=place_id)), match

    def _complete(self, accumulator: CandidateAccumulator, departed_at: datetime,
                  departure_confirmed_at: datetime,
                  departure_mode: MovementMode | None,
                  events: list[PlaceEvent]) -> tuple[PlaceMatch | None, Stay]:
        stay = replace(accumulator.stay, ended_at=departed_at, status=StayStatus.COMPLETED,
                       departure_mode=departure_mode,
                       duration_seconds=max(0, (departed_at - accumulator.stay.started_at).total_seconds()))
        match: PlaceMatch | None = None
        if stay.place_id is None:
            accumulator, match = self._associate(
                replace(accumulator, stay=stay), departure_confirmed_at, events
            )
            stay = accumulator.stay
        events.append(self._event(
            PlaceEventType.STAY_COMPLETED, stay, departure_confirmed_at,
            ("departure_hysteresis_elapsed",), observed_at=departed_at
        ))
        if stay.place_id:
            visit = PlaceVisit(_id("visit", stay.place_id, stay.stay_id), stay.place_id,
                               stay.stay_id, stay.started_at, departed_at,
                               stay.duration_seconds, stay.arrival_mode, departure_mode,
                               stay.confidence)
            places: list[Place] = []
            for place in self.state.places:
                if place.place_id != stay.place_id:
                    places.append(place)
                    continue
                count = place.visit_count + 1
                total = place.total_dwell_seconds + stay.duration_seconds
                durations = [v.duration_seconds for v in self.state.visits
                             if v.place_id == place.place_id] + [stay.duration_seconds]
                weight = max(1, place.visit_count)
                centroid = ((place.centroid[0] * weight + stay.centroid[0]) / (weight + 1),
                            (place.centroid[1] * weight + stay.centroid[1]) / (weight + 1))
                status = place.status
                if (status is PlaceStatus.CANDIDATE and count >= self.config.promotion_visits
                        and total >= self.config.promotion_dwell_seconds
                        and stay.data_quality is not DataQuality.POOR):
                    status = PlaceStatus.CONFIRMED
                    events.append(self._event(PlaceEventType.PLACE_CONFIRMED, stay,
                                              departure_confirmed_at,
                                              ("visit_threshold", "dwell_threshold")))
                places.append(replace(place, centroid=centroid,
                                      radius_m=max(place.radius_m, stay.radius_m),
                                      confidence=round(max(place.confidence, stay.confidence), 3),
                                      last_seen_at=departed_at, visit_count=count,
                                      total_dwell_seconds=total,
                                      typical_dwell_seconds=float(median(durations)), status=status))
            self.state = replace(self.state, places=tuple(places),
                                 visits=(self.state.visits + (visit,))[-self.config.visit_retention:])
            events.append(self._event(
                PlaceEventType.PLACE_DEPARTED, stay, departure_confirmed_at,
                ("visit_recorded",), observed_at=departed_at
            ))
        return match, stay

    def process(self, observation: LocationObservation,
                movement_state: MovementState | None = None) -> PlaceResult:
        quality = _quality(observation, self.config.maximum_accuracy_m)
        if observation.observation_id in self.state.seen_ids:
            return PlaceResult(self.state,
                               self.state.active_stay.stay if self.state.active_stay else None,
                               (), None, PlaceProcessingStatus.DUPLICATE, quality,
                               ("duplicate_observation",))
        gap = ((observation.observed_at - self.state.last_observed_at).total_seconds()
               if self.state.last_observed_at else 0)
        seen = (self.state.seen_ids + (observation.observation_id,))[
            -self.config.deduplication_retention:]
        self.state = replace(self.state, seen_ids=seen,
                             last_observation_id=observation.observation_id,
                             last_observed_at=observation.observed_at)
        mode = movement_state.mode if movement_state else None
        speed = observation.speed_mps if observation.speed_mps is not None else (
            movement_state.speed_mps if movement_state else None)
        events: list[PlaceEvent] = []
        match: PlaceMatch | None = None
        status = PlaceProcessingStatus.ACCEPTED
        diagnostic = ["coordinates_redacted", f"quality:{quality.value}"]
        if gap > self.config.long_gap_seconds:
            # A long absence is uncertainty, never evidence of departure.
            active = self.state.active_stay
            if active:
                active = replace(active, departure_observed_at=None, departure_mode=None)
            self.state = replace(self.state, candidate=None, active_stay=active)
            return PlaceResult(self.state,
                               self.state.active_stay.stay if self.state.active_stay else None,
                               (), None, PlaceProcessingStatus.GAP_DETECTED, quality,
                               tuple(diagnostic + ["long_gap_state_uncertain"]))
        acceptable = quality is not DataQuality.POOR and (speed is None or speed <= 2.6) \
            and mode not in {MovementMode.CYCLING, MovementMode.AUTOMOTIVE}
        active = self.state.active_stay
        if active and gap > self.config.short_gap_seconds:
            # A gap breaks continuous departure evidence. The current outside
            # point may start a new pending window, but cannot confirm the old one.
            active = replace(active, departure_observed_at=None, departure_mode=None)
            self.state = replace(self.state, active_stay=active)
            diagnostic.append("medium_gap_departure_evidence_reset")
        if active:
            distance = haversine_m(active.stay.centroid[0], active.stay.centroid[1],
                                   observation.latitude, observation.longitude)
            if quality is DataQuality.POOR:
                active = self._update_quality_only(active, observation, quality)
                self.state = replace(self.state, active_stay=active)
                status = PlaceProcessingStatus.STAY_ACTIVE
                diagnostic.append("poor_observation_excluded_from_spatial_cluster")
            elif distance <= self.config.departure_radius_m:
                active = self._update(replace(active, departure_observed_at=None,
                                              departure_mode=None),
                                      observation, quality)
                self.state = replace(self.state, active_stay=active)
                status = PlaceProcessingStatus.STAY_ACTIVE
            elif active.departure_observed_at is None:
                active = replace(active, departure_observed_at=observation.observed_at,
                                 departure_mode=mode)
                self.state = replace(self.state, active_stay=active)
                status = PlaceProcessingStatus.STAY_ACTIVE
                diagnostic.append("departure_pending")
            elif (observation.observed_at - active.departure_observed_at).total_seconds() \
                    >= self.config.departure_confirmation_seconds:
                match, _ = self._complete(active, active.departure_observed_at,
                                          observation.observed_at,
                                          active.departure_mode or mode, events)
                self.state = replace(self.state, active_stay=None)
                status = PlaceProcessingStatus.DEPARTURE_CONFIRMED
            else:
                status = PlaceProcessingStatus.STAY_ACTIVE
        elif not acceptable:
            if self.state.candidate and quality is DataQuality.POOR:
                candidate = self._update_quality_only(
                    self.state.candidate, observation, quality
                )
                self.state = replace(self.state, candidate=candidate)
                diagnostic.append("poor_observation_excluded_from_spatial_cluster")
            elif self.state.candidate:
                discarded = replace(self.state.candidate.stay, status=StayStatus.DISCARDED)
                events.append(self._event(PlaceEventType.STAY_DISCARDED, discarded,
                                          observation.observed_at, ("movement_or_quality",)))
                self.state = replace(self.state, candidate=None)
            status = PlaceProcessingStatus.INSUFFICIENT_EVIDENCE
        elif self.state.candidate is None:
            candidate = self._new_candidate(observation, quality, mode)
            self.state = replace(self.state, candidate=candidate)
            events.append(self._event(PlaceEventType.STAY_CANDIDATE_STARTED, candidate.stay,
                                      observation.observed_at, ("low_speed", "first_sample")))
            status = PlaceProcessingStatus.CANDIDATE
        else:
            candidate = self.state.candidate
            distance = haversine_m(candidate.stay.centroid[0], candidate.stay.centroid[1],
                                   observation.latitude, observation.longitude)
            if distance > self.config.stay_radius_m:
                discarded = replace(candidate.stay, status=StayStatus.DISCARDED)
                events.append(self._event(PlaceEventType.STAY_DISCARDED, discarded,
                                          observation.observed_at, ("arrival_radius_exceeded",)))
                candidate = self._new_candidate(observation, quality, mode)
                self.state = replace(self.state, candidate=candidate)
                events.append(self._event(PlaceEventType.STAY_CANDIDATE_STARTED, candidate.stay,
                                          observation.observed_at, ("low_speed", "new_cluster")))
                status = PlaceProcessingStatus.CANDIDATE
            else:
                candidate = self._update(candidate, observation, quality)
                if (candidate.stay.duration_seconds >= self.config.confirmed_seconds
                        and candidate.stay.observation_count >= self.config.minimum_observations):
                    candidate = replace(candidate, stay=replace(candidate.stay,
                                        status=StayStatus.ACTIVE))
                    candidate, match = self._associate(candidate, observation.observed_at, events)
                    events.append(self._event(PlaceEventType.STAY_CONFIRMED, candidate.stay,
                                              observation.observed_at,
                                              ("duration_threshold", "observation_threshold")))
                    self.state = replace(self.state, candidate=None, active_stay=candidate)
                    status = PlaceProcessingStatus.ARRIVAL_CONFIRMED
                else:
                    self.state = replace(self.state, candidate=candidate)
                    status = PlaceProcessingStatus.CANDIDATE
        emitted = self._append_events(events)
        return PlaceResult(self.state,
                           self.state.active_stay.stay if self.state.active_stay else None,
                           emitted, match, status, quality, tuple(diagnostic))
