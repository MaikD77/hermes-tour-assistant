"""Deterministic, coordinate-free current-context snapshots."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .location_sources import LocationObservation
from .movement import EngineState, MovementMode
from .place import PlaceEngineState, StayStatus
from .profile import FactStatus, FactType, ProfileState


class Freshness(str, Enum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    EXPIRED = "expired"


class ContextStatus(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    STALE = "stale"
    UNKNOWN = "unknown"
    INVALID = "invalid"


class ComponentStatus(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    STALE = "stale"
    UNKNOWN = "unknown"
    INVALID = "invalid"


class ContextProcessingStatus(str, Enum):
    COMPUTED = "computed"
    PARTIAL = "partial"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    STALE_INPUTS = "stale_inputs"
    CONFLICT_DETECTED = "conflict_detected"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True)
class FreshnessThresholds:
    fresh_seconds: int
    aging_seconds: int
    stale_seconds: int

    def __post_init__(self) -> None:
        if not 0 < self.fresh_seconds <= self.aging_seconds <= self.stale_seconds:
            raise ValueError("freshness thresholds must be positive and ordered")

    def classify(self, age_seconds: float) -> Freshness:
        if age_seconds <= self.fresh_seconds:
            return Freshness.FRESH
        if age_seconds <= self.aging_seconds:
            return Freshness.AGING
        if age_seconds <= self.stale_seconds:
            return Freshness.STALE
        return Freshness.EXPIRED


@dataclass(frozen=True)
class ContextConfig:
    timezone: str = "UTC"
    location_freshness: FreshnessThresholds = FreshnessThresholds(120, 300, 900)
    movement_freshness: FreshnessThresholds = FreshnessThresholds(180, 600, 900)
    place_freshness: FreshnessThresholds = FreshnessThresholds(300, 900, 1800)
    profile_freshness: FreshnessThresholds = FreshnessThresholds(86400, 2592000, 5184000)
    future_skew_seconds: int = 60
    minimum_overall_confidence: float = .6
    trait_confidence_threshold: float = .5
    time_window_tolerance_minutes: int = 30
    context_validity_seconds: int = 120
    profile_fact_minimum_confidence: float = .6
    night_start: time = time(22)
    night_end: time = time(6)
    state_dir: Path = Path.home() / ".local/state/hermes/context"

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("context timezone must be an IANA timezone") from error
        if self.future_skew_seconds < 0 or self.context_validity_seconds < 1:
            raise ValueError("invalid context time configuration")
        for value in (self.minimum_overall_confidence, self.trait_confidence_threshold,
                      self.profile_fact_minimum_confidence):
            if not 0 <= value <= 1:
                raise ValueError("context confidence thresholds must be in [0, 1]")
        if self.time_window_tolerance_minutes < 0 or self.night_start == self.night_end:
            raise ValueError("invalid context window configuration")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "ContextConfig":
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

        def thresholds(prefix: str, default: FreshnessThresholds) -> FreshnessThresholds:
            return FreshnessThresholds(integer(f"{prefix}_FRESH_SECONDS", default.fresh_seconds),
                integer(f"{prefix}_AGING_SECONDS", default.aging_seconds),
                integer(f"{prefix}_STALE_SECONDS", default.stale_seconds))

        defaults = cls()
        timezone = env.get("HERMES_CONTEXT_TIMEZONE",
                           env.get("HERMES_PROFILE_TIMEZONE", defaults.timezone)).strip()
        return cls(timezone=timezone,
            location_freshness=thresholds("HERMES_CONTEXT_LOCATION", defaults.location_freshness),
            movement_freshness=thresholds("HERMES_CONTEXT_MOVEMENT", defaults.movement_freshness),
            place_freshness=thresholds("HERMES_CONTEXT_PLACE", defaults.place_freshness),
            profile_freshness=thresholds("HERMES_CONTEXT_PROFILE", defaults.profile_freshness),
            future_skew_seconds=integer("HERMES_CONTEXT_FUTURE_SKEW_SECONDS", 60),
            minimum_overall_confidence=number("HERMES_CONTEXT_MINIMUM_CONFIDENCE", .6),
            trait_confidence_threshold=number("HERMES_CONTEXT_TRAIT_CONFIDENCE", .5),
            time_window_tolerance_minutes=integer("HERMES_CONTEXT_WINDOW_TOLERANCE_MINUTES", 30),
            context_validity_seconds=integer("HERMES_CONTEXT_VALIDITY_SECONDS", 120),
            profile_fact_minimum_confidence=number("HERMES_CONTEXT_FACT_CONFIDENCE", .6),
            state_dir=Path(env.get("HERMES_CONTEXT_STATE_DIR", str(defaults.state_dir))))


@dataclass(frozen=True)
class ContextEvidence:
    evidence_id: str
    evidence_type: str
    source_layer: str
    source_id: str
    observed_at: datetime
    freshness: Freshness
    confidence: float
    quality: str
    reason: str
    status: str


@dataclass(frozen=True)
class ContextUncertainty:
    code: str
    severity: str
    affected_component: str
    reason: str
    detected_at: datetime
    resolvable: bool
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextTrait:
    trait_id: str
    trait_type: str
    value: Any
    confidence: float
    evidence_ids: tuple[str, ...]
    status: str
    valid_until: datetime


@dataclass(frozen=True)
class LocationContext:
    observation_id: str | None
    source: str | None
    observed_at: datetime | None
    received_at: datetime | None
    age_seconds: float | None
    accuracy_class: str | None
    data_quality: str | None
    canonical_device_id: str | None
    movement_fields: tuple[str, ...]
    freshness: Freshness
    status: ComponentStatus


@dataclass(frozen=True)
class MovementContext:
    mode: str | None
    confidence: float
    since: datetime | None
    smoothed_speed_mps: float | None
    coarse_direction: str | None
    active_segment_id: str | None
    segment_duration_seconds: float | None
    segment_distance_m: float | None
    data_quality: str | None
    last_data_gap_at: datetime | None
    freshness: Freshness
    status: ComponentStatus


@dataclass(frozen=True)
class PlaceContext:
    active_stay_id: str | None
    place_id: str | None
    stay_status: str | None
    arrived_at: datetime | None
    duration_seconds: float | None
    arrival_mode: str | None
    departure_pending: bool
    place_status: str | None
    visit_count: int
    place_confidence: float
    matching_status: str
    data_quality: str | None
    freshness: Freshness
    status: ComponentStatus


@dataclass(frozen=True)
class ProfileContext:
    relevant_fact_ids: tuple[str, ...]
    transition_ids: tuple[str, ...]
    typical_windows: tuple[str, ...]
    overnight_pattern: bool | None
    daytime_pattern: bool | None
    fact_confidence: float
    fact_status: str
    freshness: Freshness
    status: ComponentStatus


@dataclass(frozen=True)
class TemporalContext:
    local_time: datetime
    weekday: str
    weekend: bool
    day_period: str
    night_window: bool
    timezone: str
    dst_active: bool
    status: ComponentStatus = ComponentStatus.AVAILABLE


@dataclass(frozen=True)
class CurrentContext:
    context_id: str
    subject_id: str
    computed_at: datetime
    valid_from: datetime
    valid_until: datetime
    overall_confidence: float
    freshness: Freshness
    location_context: LocationContext
    movement_context: MovementContext
    place_context: PlaceContext
    profile_context: ProfileContext
    temporal_context: TemporalContext
    uncertainties: tuple[ContextUncertainty, ...]
    evidence: tuple[ContextEvidence, ...]
    traits: tuple[ContextTrait, ...]
    status: ContextStatus


@dataclass(frozen=True)
class ContextResult:
    context: CurrentContext
    status: ContextProcessingStatus
    diagnostic: Mapping[str, Any]


def _id(prefix: str, *parts: Any) -> str:
    body = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(body.encode()).hexdigest()[:24]}"


def _quality_score(value: str | None) -> float:
    return {"good": 1., "limited": .7, "poor": .3, "invalid": 0}.get(value or "", .5)


def _freshness_score(value: Freshness) -> float:
    return {Freshness.FRESH: 1., Freshness.AGING: .75,
            Freshness.STALE: .35, Freshness.EXPIRED: 0}[value]


def _direction(heading: float | None) -> str | None:
    if heading is None:
        return None
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int((heading + 22.5) // 45) % 8]


def cyclic_window_matches(minute: int, start: int, end: int, tolerance: int = 0) -> bool:
    """Match a local minute against a same-day or midnight-crossing cyclic interval."""
    if not all(0 <= value < 1440 for value in (minute, start, end)) or tolerance < 0:
        return False
    near_boundary = any(min((minute - edge) % 1440, (edge - minute) % 1440) <= tolerance
                        for edge in (start, end))
    inside = start <= minute <= end if start <= end else minute >= start or minute <= end
    return inside or near_boundary


class CurrentContextEngine:
    """Read-only composition of lower-layer states; never calls providers or delivers actions."""

    def __init__(self, config: ContextConfig | None = None) -> None:
        self.config = config or ContextConfig()

    def compute(self, *, observation: LocationObservation | None = None,
                movement_state: EngineState | None = None,
                place_state: PlaceEngineState | None = None,
                profile_state: ProfileState | None = None,
                computed_at: datetime) -> ContextResult:
        if computed_at.tzinfo is None or computed_at.utcoffset() is None:
            raise ValueError("computed_at must be timezone-aware")
        cfg, evidence, uncertain = self.config, [], []
        def add_uncertainty(code: str, component: str, reason: str,
                            severity: str = "warning", ids: tuple[str, ...] = ()) -> None:
            uncertain.append(ContextUncertainty(code, severity, component, reason,
                                                computed_at, True, ids))
        def fresh(at: datetime | None, threshold: FreshnessThresholds) -> Freshness:
            return Freshness.EXPIRED if at is None else threshold.classify(
                max(0., (computed_at - at).total_seconds()))
        invalid = False
        timestamps = [x for x in (observation.observed_at if observation else None,
            movement_state.movement.last_observed_at if movement_state and movement_state.movement else None,
            place_state.last_observed_at if place_state else None,
            profile_state.last_computed_at if profile_state else None) if x is not None]
        for stamp in timestamps:
            if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > computed_at + timedelta(seconds=cfg.future_skew_seconds):
                add_uncertainty("clock_skew", "context", "input timestamp is naive or in the future", "critical")
                invalid = True
        # Location: intentionally omit coordinates and raw metadata.
        lf = fresh(observation.observed_at if observation else None, cfg.location_freshness)
        if observation:
            age = max(0., (computed_at - observation.observed_at).total_seconds())
            accuracy = ("precise" if observation.accuracy_m is not None and observation.accuracy_m <= 20
                        else "usable" if observation.accuracy_m is not None and observation.accuracy_m <= 100
                        else "coarse" if observation.accuracy_m is not None else "unknown")
            quality = "good" if accuracy == "precise" else "limited" if accuracy in ("usable", "unknown") else "poor"
            location = LocationContext(observation.observation_id, observation.source,
                observation.observed_at, observation.received_at, age, accuracy, quality,
                observation.device_id, tuple(x for x, v in (("speed", observation.speed_mps),
                ("course", observation.course_deg), ("altitude", observation.altitude_m)) if v is not None),
                lf, ComponentStatus.STALE if lf in (Freshness.STALE, Freshness.EXPIRED) else ComponentStatus.AVAILABLE)
            evidence.append(ContextEvidence(_id("ev", "location", observation.observation_id),
                "location_observation", "location", observation.observation_id,
                observation.observed_at, lf, _quality_score(quality), quality,
                "latest canonical observation", "active"))
            if lf in (Freshness.STALE, Freshness.EXPIRED):
                add_uncertainty("location_stale", "location", f"location is {lf.value}")
        else:
            location = LocationContext(None, None, None, None, None, None, None, None, (), lf, ComponentStatus.UNKNOWN)
            add_uncertainty("location_missing", "location", "no current location observation")
        # Movement and active segment.
        movement = movement_state.movement if movement_state else None
        mf = fresh(movement.last_observed_at if movement else None, cfg.movement_freshness)
        segment = movement_state.active_segment if movement_state else None
        if movement:
            movement_context = MovementContext(movement.mode.value, movement.confidence,
                movement.state_started_at, movement.speed_mps, _direction(movement.heading_deg),
                segment.segment_id if segment else None, segment.duration_seconds if segment else None,
                segment.distance_m if segment else None, movement.data_quality.value,
                movement.last_observed_at if movement_state and movement_state.last_status and
                movement_state.last_status.value == "gap_detected" else None, mf,
                ComponentStatus.STALE if mf in (Freshness.STALE, Freshness.EXPIRED) else
                ComponentStatus.PARTIAL if movement.mode is MovementMode.UNKNOWN else ComponentStatus.AVAILABLE)
            mid = _id("ev", "movement", movement.mode.value, movement.last_observed_at)
            evidence.append(ContextEvidence(mid, "movement_state", "movement", mid,
                movement.last_observed_at, mf, movement.confidence, movement.data_quality.value,
                "current inferred movement state", "active"))
            if segment:
                evidence.append(ContextEvidence(_id("ev", "segment", segment.segment_id),
                    "active_segment", "movement", segment.segment_id, segment.started_at, mf,
                    segment.confidence, segment.data_quality.value, "active bounded segment", "active"))
            if movement.mode is MovementMode.UNKNOWN:
                add_uncertainty("movement_unknown", "movement", "movement mode is unknown")
            if movement.confidence < cfg.trait_confidence_threshold:
                add_uncertainty("movement_low_confidence", "movement", "movement confidence is below threshold")
            if movement_state and movement_state.last_status and movement_state.last_status.value == "gap_detected":
                add_uncertainty("data_gap", "movement", "movement engine reports a data gap")
        else:
            movement_context = MovementContext(None, 0, None, None, None, None, None, None,
                                               None, None, mf, ComponentStatus.UNKNOWN)
            add_uncertainty("movement_unknown", "movement", "movement state is unavailable")
        # Place/stay.
        accumulator = place_state.active_stay if place_state and place_state.active_stay else (
            place_state.candidate if place_state else None)
        stay = accumulator.stay if accumulator else None
        pf = fresh(place_state.last_observed_at if place_state else None, cfg.place_freshness)
        place = next((p for p in place_state.places if stay and p.place_id == stay.place_id), None) if place_state else None
        if stay:
            assert accumulator is not None
            matching = "matched" if place else "candidate" if stay.place_id is None else "unknown_place"
            place_context = PlaceContext(stay.stay_id, stay.place_id, stay.status.value,
                stay.started_at, stay.duration_seconds, stay.arrival_mode.value if stay.arrival_mode else None,
                accumulator.departure_observed_at is not None, place.status.value if place else None,
                place.visit_count if place else 0, place.confidence if place else stay.confidence,
                matching, stay.data_quality.value, pf,
                ComponentStatus.STALE if pf in (Freshness.STALE, Freshness.EXPIRED) else
                ComponentStatus.PARTIAL if stay.status is StayStatus.CANDIDATE else ComponentStatus.AVAILABLE)
            sid = _id("ev", "stay", stay.stay_id)
            evidence.append(ContextEvidence(sid, "active_stay", "place", stay.stay_id,
                stay.started_at, pf, stay.confidence, stay.data_quality.value,
                "active or candidate stay", stay.status.value))
            if place:
                evidence.append(ContextEvidence(_id("ev", "place", place.place_id), "matched_place",
                    "place", place.place_id, place.last_seen_at, pf, place.confidence,
                    place.data_quality.value, "stay matched to coordinate-free place identifier", place.status.value))
            if stay.status is StayStatus.CANDIDATE:
                add_uncertainty("stay_candidate_only", "place", "stay is not confirmed", "info", (sid,))
            if stay.place_id and not place:
                add_uncertainty("conflicting_evidence", "place", "stay references an unknown place", "critical", (sid,))
                invalid = True
        else:
            place_context = PlaceContext(None, None, None, None, None, None, False, None, 0, 0,
                                         "none", None, pf, ComponentStatus.UNKNOWN)
            add_uncertainty("place_unknown", "place", "no active stay")
        # Profile facts: confirmed, confidence-qualified, current-place-only. Revoked never enters evidence.
        prof_fresh = fresh(profile_state.last_computed_at if profile_state else None, cfg.profile_freshness)
        facts = tuple(f for f in (profile_state.facts if profile_state else ())
            if f.status is not FactStatus.REVOKED and f.confidence >= cfg.profile_fact_minimum_confidence
            and (not place_context.place_id or f.subject_id == place_context.place_id))
        active_facts = tuple(f for f in facts if f.status is FactStatus.CONFIRMED)
        transitions = tuple(p for p in (profile_state.transitions if profile_state else ())
            if p.status is FactStatus.CONFIRMED and p.confidence >= cfg.profile_fact_minimum_confidence)
        windows = tuple(f.fact_type.value for f in facts if f.fact_type in
            (FactType.TYPICAL_ARRIVAL_WINDOW, FactType.TYPICAL_DEPARTURE_WINDOW, FactType.TYPICAL_VISIT_WINDOW))
        overnight = any(f.fact_type is FactType.FREQUENT_OVERNIGHT_PLACE for f in active_facts)
        daytime = any(f.fact_type is FactType.FREQUENT_DAYTIME_PLACE for f in active_facts)
        fact_conf = round(sum(f.confidence * (.35 if f.status is FactStatus.STALE else 1)
                              for f in facts) / len(facts), 3) if facts else 0
        profile_context = ProfileContext(tuple(f.fact_id for f in facts),
            tuple(p.transition_id for p in transitions), windows,
            overnight if facts else None, daytime if facts else None, fact_conf,
            "confirmed" if active_facts else "stale" if facts else "missing", prof_fresh,
            ComponentStatus.UNKNOWN if not facts else ComponentStatus.STALE if
            prof_fresh in (Freshness.STALE, Freshness.EXPIRED) or not active_facts else
            ComponentStatus.AVAILABLE)
        if not profile_state:
            add_uncertainty("profile_missing", "profile", "profile state is unavailable", "info")
        elif prof_fresh in (Freshness.STALE, Freshness.EXPIRED) or (facts and not active_facts):
            add_uncertainty("profile_stale", "profile", "profile evidence is stale", "info")
        known_places = {p.place_id for p in place_state.places} if place_state else set()
        for fact in facts:
            eid = _id("ev", "fact", fact.fact_id)
            evidence.append(ContextEvidence(eid, "profile_fact", "profile", fact.fact_id,
                fact.last_observed_at, prof_fresh, fact.confidence,
                "limited" if fact.status is FactStatus.STALE else "good",
                f"qualified {fact.fact_type.value} fact", fact.status.value))
            if place_state and fact.subject_id.startswith("place_") and fact.subject_id not in known_places:
                add_uncertainty("conflicting_evidence", "profile", "fact references an unknown place", "warning", (eid,))
        # Timezone-aware temporal model.
        local = computed_at.astimezone(ZoneInfo(cfg.timezone))
        clock = local.timetz().replace(tzinfo=None)
        night = clock >= cfg.night_start or clock < cfg.night_end
        period = "night" if night else "morning" if clock < time(12) else "afternoon" if clock < time(18) else "evening"
        temporal = TemporalContext(local, local.strftime("%A"), local.weekday() >= 5,
            period, night, cfg.timezone, bool(local.dst() and local.dst() != timedelta(0)))
        tev = _id("ev", "temporal", cfg.timezone, local.isoformat())
        evidence.append(ContextEvidence(tev, "temporal_rule", "context", tev,
            computed_at, Freshness.FRESH, 1, "good", "timezone-aware local clock", "active"))
        # Cross-layer conflicts; never repair lower state.
        if observation and movement and movement.last_observed_at > observation.observed_at:
            add_uncertainty("conflicting_evidence", "movement", "movement state is newer than location", "warning")
        if observation and movement_state and movement_state.recent and \
                movement_state.recent[-1].device_id != observation.device_id:
            add_uncertainty("device_mismatch", "context",
                "location and movement state use different canonical devices", "critical")
            invalid = True
        if movement and stay and movement.mode in (MovementMode.CYCLING, MovementMode.AUTOMOTIVE) and movement.speed_mps is not None and movement.speed_mps > 2:
            add_uncertainty("conflicting_evidence", "place", "active stay conflicts with sustained movement")
        if movement and movement.mode in (MovementMode.CYCLING, MovementMode.AUTOMOTIVE) and (movement.speed_mps or 0) < .5:
            add_uncertainty("conflicting_evidence", "movement", "moving mode conflicts with near-zero speed")
        # Traits are bounded claims backed by evidence IDs.
        valid_until = computed_at + timedelta(seconds=cfg.context_validity_seconds)
        traits: list[ContextTrait] = []
        def trait(kind: str, value: Any, confidence: float, ids: tuple[str, ...] = ()) -> None:
            if confidence >= cfg.trait_confidence_threshold:
                traits.append(ContextTrait(_id("trait", kind, value, ids), kind, value,
                    round(confidence, 3), ids, "active", valid_until))
        movement_ids = tuple(e.evidence_id for e in evidence if e.evidence_type == "movement_state")
        if movement and movement.mode is not MovementMode.UNKNOWN:
            trait("movement_presence", "currently_stationary" if movement.mode is MovementMode.STATIONARY else "currently_moving", movement.confidence, movement_ids)
        if stay:
            trait("known_place_presence", "currently_at_known_place" if place else "currently_at_candidate_place", stay.confidence, (sid,))
        frequent = next((f for f in active_facts if f.fact_type is FactType.FREQUENT_PLACE), None)
        if frequent:
            trait("place_frequency", "current_place_is_frequent", frequent.confidence,
                  (_id("ev", "fact", frequent.fact_id),))
        if overnight and night:
            trait("overnight_pattern_match", True, fact_conf,
                  tuple(e.evidence_id for e in evidence if e.evidence_type == "profile_fact"))
        # Generic cyclic window matcher accepts profile values start_minute/end_minute or minute_of_day.
        def window_match(fact: Any) -> bool:
            minute = local.hour * 60 + local.minute
            start = int(fact.value.get("start_minute", fact.value.get("minute_of_day", -9999)))
            end = int(fact.value.get("end_minute", start))
            tol = cfg.time_window_tolerance_minutes
            return cyclic_window_matches(minute, start, end, tol)
        for fact_type, trait_type in ((FactType.TYPICAL_ARRIVAL_WINDOW, "arrival_window_match"),
            (FactType.TYPICAL_DEPARTURE_WINDOW, "departure_window_match")):
            matched = next((f for f in facts if f.fact_type is fact_type and window_match(f)), None)
            if matched:
                confidence = matched.confidence * (.35 if matched.status is FactStatus.STALE else 1)
                trait(trait_type, True, confidence, (_id("ev", "fact", matched.fact_id),))
        if segment and not stay and place_state and place_state.visits:
            previous = max(place_state.visits, key=lambda v: v.departed_at)
            trait("transition_pattern_match", {"currently_in_transition": True,
                "from_place_id": previous.place_id, "possible_to_place_ids": []}, segment.confidence,
                tuple(e.evidence_id for e in evidence if e.evidence_type == "active_segment"))
        completeness = sum(x.status is ComponentStatus.AVAILABLE for x in
            (location, movement_context, place_context, profile_context)) / 4
        trait("context_completeness", round(completeness, 2), max(completeness, cfg.trait_confidence_threshold))
        # Weighted confidence: operational evidence 35/25/25/15, adjusted by freshness,
        # quality, diversity bonus, explicit uncertainty and conflict penalties.
        components = ((.35, _quality_score(location.data_quality) * _freshness_score(lf)),
            (.25, movement_context.confidence * _quality_score(movement_context.data_quality) * _freshness_score(mf)),
            (.25, place_context.place_confidence * _quality_score(place_context.data_quality) * _freshness_score(pf)),
            (.15, profile_context.fact_confidence * _freshness_score(prof_fresh)))
        confidence = sum(weight * score for weight, score in components)
        confidence += min(len({e.source_layer for e in evidence}) - 1, 3) * .025
        confidence -= sum(.18 if u.severity == "critical" else .08 if u.code == "conflicting_evidence" else .035 for u in uncertain)
        confidence = round(max(0., min(1., confidence)), 3)
        freshness = max((lf, mf, pf), key=lambda x: list(Freshness).index(x))
        usable = sum(x.status not in (ComponentStatus.UNKNOWN, ComponentStatus.INVALID) for x in
                     (location, movement_context, place_context, profile_context))
        if invalid:
            status = ContextStatus.INVALID
        elif usable == 0:
            status = ContextStatus.UNKNOWN
        elif sum(component.status is not ComponentStatus.UNKNOWN and value in
                 (Freshness.STALE, Freshness.EXPIRED) for component, value in
                 ((location, lf), (movement_context, mf), (place_context, pf))) >= 2:
            status = ContextStatus.STALE
        elif usable == 4 and confidence >= cfg.minimum_overall_confidence and not uncertain:
            status = ContextStatus.AVAILABLE
        else:
            status = ContextStatus.PARTIAL
        processing = (ContextProcessingStatus.INVALID_INPUT if invalid else
            ContextProcessingStatus.INSUFFICIENT_EVIDENCE if status is ContextStatus.UNKNOWN else
            ContextProcessingStatus.STALE_INPUTS if status is ContextStatus.STALE else
            ContextProcessingStatus.CONFLICT_DETECTED if any(u.code == "conflicting_evidence" for u in uncertain) else
            ContextProcessingStatus.COMPUTED if status is ContextStatus.AVAILABLE else ContextProcessingStatus.PARTIAL)
        subject = observation.device_id if observation else "unknown"
        identity = (subject, computed_at.isoformat(), observation.observation_id if observation else None,
            movement.last_observed_at.isoformat() if movement else None,
            segment.segment_id if segment else None, place_state.last_observation_id if place_state else None,
            profile_state.last_computed_at.isoformat() if profile_state and profile_state.last_computed_at else None,
            tuple(f.fact_id for f in facts), tuple(t.trait_id for t in traits), tuple(u.code for u in uncertain))
        context = CurrentContext(_id("ctx", *identity), subject, computed_at, computed_at,
            valid_until, confidence, freshness, location, movement_context, place_context,
            profile_context, temporal, tuple(uncertain), tuple(evidence), tuple(traits), status)
        diagnostic = {"status": processing.value, "schema_version": 1,
            "input_available": {"location": observation is not None, "movement": movement is not None,
                "place": place_state is not None, "profile": profile_state is not None},
            "input_freshness": {"location": lf.value, "movement": mf.value,
                "place": pf.value, "profile": prof_fresh.value},
            "conflicts": [u.reason for u in uncertain if u.code == "conflicting_evidence"],
            "shadow_mode": True, "provider_calls": False, "delivery": False}
        return ContextResult(context, processing, diagnostic)

    def export(self, context: CurrentContext) -> dict[str, Any]:
        """Return a fully sanitized representation (contracts contain no coordinates)."""
        def default(value: object) -> object:
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, Enum):
                return value.value
            raise TypeError(type(value).__name__)
        return json.loads(json.dumps(asdict(context), default=default))

    def explain(self, context: CurrentContext) -> str:
        lines = [f"Status: {context.status.value}", f"Confidence: {context.overall_confidence:.3f}",
            f"Freshness: {context.freshness.value}",
            f"Valid: {context.valid_from.isoformat()} – {context.valid_until.isoformat()}", "", "Known:"]
        if context.movement_context.mode:
            lines.append(f"- movement: {context.movement_context.mode}, confidence {context.movement_context.confidence:.3f}")
        lines.append(f"- place: {context.place_context.place_id or 'none'}")
        lines.extend(f"- trait: {t.trait_type}={t.value}" for t in context.traits)
        lines.append("\nUncertain:")
        lines.extend(f"- {u.code}: {u.reason}" for u in context.uncertainties)
        return "\n".join(lines)
