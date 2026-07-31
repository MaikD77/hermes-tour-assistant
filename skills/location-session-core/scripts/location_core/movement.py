"""Deterministic, source-neutral movement and segment inference."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Mapping

from .location_sources import LocationObservation
from .route_engine import haversine_m


class MovementMode(str, Enum):
    UNKNOWN = "unknown"
    STATIONARY = "stationary"
    WALKING = "walking"
    CYCLING = "cycling"
    AUTOMOTIVE = "automotive"


class DataQuality(str, Enum):
    GOOD = "good"
    LIMITED = "limited"
    POOR = "poor"
    INVALID = "invalid"


class SegmentStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"


class ProcessingStatus(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    INVALID = "invalid"
    GAP_DETECTED = "gap_detected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class MovementEventType(str, Enum):
    STARTED = "movement.started"
    STOPPED = "movement.stopped"
    MODE_CHANGED = "movement.mode_changed"
    HEADING_CHANGED = "movement.heading_changed"
    SEGMENT_STARTED = "movement.segment_started"
    SEGMENT_UPDATED = "movement.segment_updated"
    SEGMENT_COMPLETED = "movement.segment_completed"
    DATA_GAP = "movement.data_gap"


def _bounded(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


def _aware(value: datetime | None, name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class MovementConfig:
    stationary_radius_m: float = 15.0
    stationary_max_mps: float = 0.7
    stationary_min_seconds: float = 90.0
    start_distance_m: float = 20.0
    start_min_seconds: float = 20.0
    walk_max_mps: float = 2.6
    cycling_max_mps: float = 12.0
    automotive_min_mps: float = 10.0
    heading_change_deg: float = 45.0
    short_gap_seconds: float = 180.0
    long_gap_seconds: float = 900.0
    max_plausible_mps: float = 70.0
    poor_accuracy_m: float = 100.0
    transition_observations: int = 3
    transition_min_seconds: float = 20.0
    cooldown_seconds: float = 45.0
    buffer_size: int = 64
    state_dir: Path = Path.home() / ".local/state/hermes/movement"

    def __post_init__(self) -> None:
        positive = [
            self.stationary_radius_m, self.stationary_min_seconds, self.start_distance_m,
            self.walk_max_mps, self.cycling_max_mps, self.automotive_min_mps,
            self.heading_change_deg, self.short_gap_seconds, self.long_gap_seconds,
            self.max_plausible_mps, self.poor_accuracy_m, self.transition_min_seconds,
        ]
        if any(not math.isfinite(v) or v <= 0 for v in positive):
            raise ValueError("movement thresholds must be finite and positive")
        if not (self.stationary_max_mps < self.walk_max_mps < self.cycling_max_mps):
            raise ValueError("speed thresholds must increase from stationary to cycling")
        if not self.walk_max_mps < self.automotive_min_mps <= self.cycling_max_mps:
            raise ValueError("automotive threshold must overlap the upper cycling band")
        if not self.long_gap_seconds > self.short_gap_seconds:
            raise ValueError("long gap must exceed short gap")
        if self.max_plausible_mps <= self.automotive_min_mps:
            raise ValueError("maximum plausible speed must exceed automotive threshold")
        if self.transition_observations < 2 or not 3 <= self.buffer_size <= 512:
            raise ValueError("invalid observation count or buffer size")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> MovementConfig:
        defaults = cls()

        def number(variable: str, default: float) -> float:
            try:
                return float(env.get(variable, default))
            except ValueError as error:
                raise ValueError(f"{variable} must be numeric") from error

        return cls(
            stationary_radius_m=number("HERMES_MOVEMENT_STATIONARY_RADIUS_M", defaults.stationary_radius_m),
            stationary_min_seconds=number("HERMES_MOVEMENT_STATIONARY_MIN_SECONDS", defaults.stationary_min_seconds),
            start_distance_m=number("HERMES_MOVEMENT_START_DISTANCE_M", defaults.start_distance_m),
            start_min_seconds=number("HERMES_MOVEMENT_START_MIN_SECONDS", defaults.start_min_seconds),
            walk_max_mps=number("HERMES_MOVEMENT_WALK_MAX_MPS", defaults.walk_max_mps),
            cycling_max_mps=number("HERMES_MOVEMENT_CYCLING_MAX_MPS", defaults.cycling_max_mps),
            automotive_min_mps=number("HERMES_MOVEMENT_AUTOMOTIVE_MIN_MPS", defaults.automotive_min_mps),
            heading_change_deg=number("HERMES_MOVEMENT_HEADING_CHANGE_DEG", defaults.heading_change_deg),
            short_gap_seconds=number("HERMES_MOVEMENT_SHORT_GAP_SECONDS", defaults.short_gap_seconds),
            long_gap_seconds=number("HERMES_MOVEMENT_LONG_GAP_SECONDS", defaults.long_gap_seconds),
            max_plausible_mps=number("HERMES_MOVEMENT_MAX_PLAUSIBLE_MPS", defaults.max_plausible_mps),
            state_dir=Path(env.get("HERMES_MOVEMENT_STATE_DIR", str(defaults.state_dir))),
        )


@dataclass(frozen=True, repr=False)
class MovementState:
    mode: MovementMode
    confidence: float
    state_started_at: datetime
    last_observed_at: datetime
    speed_mps: float | None
    heading_deg: float | None
    observation_count: int
    evidence: tuple[str, ...]
    data_quality: DataQuality

    def __post_init__(self) -> None:
        _aware(self.state_started_at, "state_started_at")
        _aware(self.last_observed_at, "last_observed_at")
        if not 0 <= self.confidence <= 1 or self.observation_count < 1:
            raise ValueError("invalid movement state")

    def __repr__(self) -> str:
        return (f"MovementState(mode={self.mode.value!r}, confidence={self.confidence}, "
                f"observations={self.observation_count}, quality={self.data_quality.value!r})")


@dataclass(frozen=True)
class MovementSegment:
    segment_id: str
    mode: MovementMode
    started_at: datetime
    ended_at: datetime | None
    status: SegmentStatus
    start_observation_id: str
    end_observation_id: str
    observation_count: int
    duration_seconds: float
    distance_m: float
    displacement_m: float
    average_speed_mps: float
    maximum_speed_mps: float
    dominant_heading_deg: float | None
    heading_stability: float
    confidence: float
    data_quality: DataQuality
    gap_count: int


@dataclass(frozen=True)
class MovementEvent:
    event_id: str
    event_type: MovementEventType
    observed_at: datetime
    observation_ids: tuple[str, ...]
    previous_mode: MovementMode
    new_mode: MovementMode
    confidence: float
    evidence: tuple[str, ...]
    segment_id: str | None = None
    gap_seconds: float | None = None
    gap_decision: str | None = None


@dataclass(frozen=True, repr=False)
class ObservationFeature:
    observation_id: str
    device_id: str
    observed_at: datetime
    latitude: float
    longitude: float
    speed_mps: float
    heading_deg: float | None
    quality: DataQuality

    def __repr__(self) -> str:
        return f"ObservationFeature(id={self.observation_id!r}, quality={self.quality.value!r})"


@dataclass(frozen=True)
class EngineState:
    schema_version: int = 1
    movement: MovementState | None = None
    active_segment: MovementSegment | None = None
    recent: tuple[ObservationFeature, ...] = ()
    seen_ids: tuple[str, ...] = ()
    pending_mode: MovementMode | None = None
    pending_since: datetime | None = None
    pending_count: int = 0
    last_transition_at: datetime | None = None
    last_status: ProcessingStatus | None = None


@dataclass(frozen=True)
class MovementResult:
    state: EngineState
    events: tuple[MovementEvent, ...]
    segment: MovementSegment | None
    completed_segment: MovementSegment | None
    status: ProcessingStatus
    data_quality: DataQuality
    diagnostic: tuple[str, ...]


def bearing_deg(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dl = math.radians(b_lon - a_lon)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angular_difference(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _mean_heading(values: list[float]) -> tuple[float | None, float]:
    if not values:
        return None, 0.0
    x = sum(math.cos(math.radians(v)) for v in values) / len(values)
    y = sum(math.sin(math.radians(v)) for v in values) / len(values)
    stability = math.hypot(x, y)
    return (math.degrees(math.atan2(y, x)) + 360) % 360, _bounded(stability)


def _digest(prefix: str, *values: object) -> str:
    raw = json.dumps(values, separators=(",", ":"), default=str).encode()
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()}"


class MovementEngine:
    """Pure in-memory state machine; persistence is an explicit outer concern."""

    def __init__(self, config: MovementConfig | None = None, state: EngineState | None = None):
        self.config = config or MovementConfig()
        self.state = state or EngineState()

    def _event(self, kind: MovementEventType, observation: LocationObservation,
               previous: MovementMode, new: MovementMode, confidence: float,
               evidence: tuple[str, ...], segment_id: str | None = None,
               gap_seconds: float | None = None, gap_decision: str | None = None) -> MovementEvent:
        ids = (observation.observation_id,)
        event_id = _digest("movevt", kind.value, observation.observed_at.isoformat(), ids,
                           previous.value, new.value, segment_id, gap_seconds, gap_decision)
        return MovementEvent(event_id, kind, observation.observed_at, ids, previous, new,
                             confidence, evidence, segment_id, gap_seconds, gap_decision)

    def process(self, observation: LocationObservation) -> MovementResult:
        state = self.state
        if observation.observation_id in state.seen_ids:
            return MovementResult(state, (), state.active_segment, None,
                                  ProcessingStatus.DUPLICATE, DataQuality.INVALID,
                                  ("observation already processed",))
        previous = state.recent[-1] if state.recent else None
        if previous and observation.device_id != previous.device_id:
            return MovementResult(state, (), state.active_segment, None,
                                  ProcessingStatus.INVALID, DataQuality.INVALID,
                                  ("device differs from active movement stream",))
        if previous and observation.observed_at <= previous.observed_at:
            reason = "timestamp equals previous observation" if observation.observed_at == previous.observed_at else "observation predates active stream"
            return MovementResult(state, (), state.active_segment, None,
                                  ProcessingStatus.OUT_OF_ORDER, DataQuality.INVALID, (reason,))

        distance, elapsed, derived_speed, heading = 0.0, 0.0, 0.0, observation.course_deg
        reasons: list[str] = []
        quality = DataQuality.GOOD
        gap = 0.0
        if previous:
            elapsed = (observation.observed_at - previous.observed_at).total_seconds()
            distance = haversine_m(previous.latitude, previous.longitude,
                                   observation.latitude, observation.longitude)
            derived_speed = distance / elapsed
            if distance >= self.config.stationary_radius_m:
                heading = bearing_deg(previous.latitude, previous.longitude,
                                      observation.latitude, observation.longitude)
            if elapsed > self.config.short_gap_seconds:
                gap = elapsed
                quality = DataQuality.POOR if elapsed >= self.config.long_gap_seconds else DataQuality.LIMITED
                reasons.append("long data gap" if quality is DataQuality.POOR else "medium data gap")
        sensor_speed = observation.speed_mps
        speed = derived_speed if sensor_speed is None else (sensor_speed + derived_speed) / 2
        if observation.accuracy_m is not None and observation.accuracy_m > self.config.poor_accuracy_m:
            quality, reasons = DataQuality.POOR, reasons + ["poor GPS accuracy"]
        if previous and derived_speed > self.config.max_plausible_mps:
            quality, reasons = DataQuality.INVALID, reasons + ["implausible position jump"]
        if sensor_speed is None:
            reasons.append("source speed unavailable")
            if quality is DataQuality.GOOD:
                quality = DataQuality.LIMITED
        elif previous and abs(sensor_speed - derived_speed) > max(5, derived_speed * 0.75):
            reasons.append("sensor and derived speed disagree")
            quality = DataQuality.POOR
        if heading is None:
            reasons.append("heading unavailable")

        candidate = self._classify(speed, distance, elapsed, quality)
        if not previous:
            candidate, reasons = MovementMode.UNKNOWN, reasons + ["first observation"]
        current = state.movement.mode if state.movement else MovementMode.UNKNOWN
        pending_mode, pending_since, pending_count = state.pending_mode, state.pending_since, state.pending_count
        transitioned = False
        if candidate == current:
            pending_mode, pending_since, pending_count = None, None, 0
        elif candidate == pending_mode:
            pending_count += 1
        else:
            pending_mode, pending_since, pending_count = candidate, observation.observed_at, 1
        pending_duration = ((observation.observed_at - pending_since).total_seconds()
                            if pending_since else 0.0)
        cooldown_ok = (state.last_transition_at is None or
                       (observation.observed_at - state.last_transition_at).total_seconds() >= self.config.cooldown_seconds)
        if (quality not in {DataQuality.POOR, DataQuality.INVALID}
                and pending_count >= self.config.transition_observations
                and pending_duration >= self.config.transition_min_seconds and cooldown_ok):
            current, transitioned = candidate, True
            pending_mode, pending_since, pending_count = None, None, 0
        evidence = tuple(reasons + [f"candidate={candidate.value}", f"speed_band={round(speed, 2)}mps"])
        old_movement = state.movement
        confidence = _bounded((old_movement.confidence + 0.12) if old_movement and current == old_movement.mode else (0.65 if transitioned else 0.3))
        if quality is DataQuality.POOR:
            confidence = min(confidence, 0.35)
        started_at = (observation.observed_at if old_movement is None or transitioned
                      else old_movement.state_started_at)
        movement = MovementState(current, confidence, started_at, observation.observed_at,
                                 round(speed, 3), None if heading is None else round(heading, 1),
                                 (1 if old_movement is None or transitioned else old_movement.observation_count + 1),
                                 evidence, quality)
        feature = ObservationFeature(observation.observation_id, observation.device_id,
                                     observation.observed_at, observation.latitude,
                                     observation.longitude, speed, heading, quality)
        recent = (state.recent + (feature,))[-self.config.buffer_size:]
        seen = (state.seen_ids + (observation.observation_id,))[-self.config.buffer_size:]
        events: list[MovementEvent] = []
        completed: MovementSegment | None = None
        segment = state.active_segment
        if gap:
            decision = "complete_segment" if gap >= self.config.long_gap_seconds else "continue_uncertain"
            events.append(self._event(MovementEventType.DATA_GAP, observation, current, current,
                                      confidence, tuple(reasons), segment.segment_id if segment else None,
                                      gap, decision))
            if segment and gap >= self.config.long_gap_seconds:
                assert previous is not None
                completed = replace(segment, ended_at=previous.observed_at,
                                    status=SegmentStatus.COMPLETED)
                events.append(self._event(MovementEventType.SEGMENT_COMPLETED, observation,
                                          segment.mode, segment.mode, segment.confidence,
                                          ("long data gap",), segment.segment_id))
                segment = None
        moving = current in {MovementMode.WALKING, MovementMode.CYCLING, MovementMode.AUTOMOTIVE}
        old_moving = old_movement is not None and old_movement.mode in {MovementMode.WALKING, MovementMode.CYCLING, MovementMode.AUTOMOTIVE}
        if transitioned:
            kind = MovementEventType.MODE_CHANGED
            if moving and not old_moving:
                kind = MovementEventType.STARTED
            elif old_moving and not moving:
                kind = MovementEventType.STOPPED
            events.append(self._event(kind, observation, old_movement.mode if old_movement else MovementMode.UNKNOWN,
                                      current, confidence, evidence, segment.segment_id if segment else None))
            if segment:
                completed = replace(segment, ended_at=observation.observed_at,
                                    status=SegmentStatus.COMPLETED)
                events.append(self._event(MovementEventType.SEGMENT_COMPLETED, observation,
                                          segment.mode, current, confidence,
                                          ("confirmed state transition",), segment.segment_id))
                segment = None
        if moving:
            if segment is None:
                sid = _digest("movseg", observation.device_id, current.value,
                              observation.observation_id, observation.observed_at.isoformat())
                segment = MovementSegment(sid, current, observation.observed_at, None,
                                          SegmentStatus.ACTIVE, observation.observation_id,
                                          observation.observation_id, 1, 0, 0, 0, speed, speed,
                                          heading, 1.0 if heading is not None else 0.0,
                                          confidence, quality, 1 if gap else 0)
                events.append(self._event(MovementEventType.SEGMENT_STARTED, observation,
                                          current, current, confidence, evidence, sid))
            elif previous:
                headings = [f.heading_deg for f in recent if f.heading_deg is not None]
                dominant, stability = _mean_heading(headings)
                duration = (observation.observed_at - segment.started_at).total_seconds()
                segment = replace(segment, end_observation_id=observation.observation_id,
                                  observation_count=segment.observation_count + 1,
                                  duration_seconds=round(duration, 1),
                                  distance_m=round(segment.distance_m + distance, 1),
                                  displacement_m=round(haversine_m(recent[-segment.observation_count - 1].latitude,
                                                                  recent[-segment.observation_count - 1].longitude,
                                                                  observation.latitude, observation.longitude), 1),
                                  average_speed_mps=round((segment.distance_m + distance) / max(1, duration), 3),
                                  maximum_speed_mps=round(max(segment.maximum_speed_mps, speed), 3),
                                  dominant_heading_deg=None if dominant is None else round(dominant, 1),
                                  heading_stability=stability, confidence=confidence,
                                  data_quality=quality, gap_count=segment.gap_count + (1 if gap else 0))
                events.append(self._event(MovementEventType.SEGMENT_UPDATED, observation,
                                          current, current, confidence, evidence, segment.segment_id))
                if (old_movement and old_movement.heading_deg is not None and heading is not None
                        and speed > self.config.stationary_max_mps
                        and quality in {DataQuality.GOOD, DataQuality.LIMITED}
                        and angular_difference(old_movement.heading_deg, heading) >= self.config.heading_change_deg
                        and stability >= 0.6):
                    events.append(self._event(MovementEventType.HEADING_CHANGED, observation,
                                              current, current, confidence,
                                              ("stable significant heading change",), segment.segment_id))
        status = ProcessingStatus.GAP_DETECTED if gap else (ProcessingStatus.INSUFFICIENT_EVIDENCE if current is MovementMode.UNKNOWN else ProcessingStatus.ACCEPTED)
        new_state = EngineState(1, movement, segment, recent, seen, pending_mode, pending_since,
                                pending_count, observation.observed_at if transitioned else state.last_transition_at,
                                status)
        self.state = new_state
        return MovementResult(new_state, tuple(events), segment, completed, status, quality,
                              tuple(reasons) or ("observation accepted",))

    def _classify(self, speed: float, distance: float, elapsed: float,
                  quality: DataQuality) -> MovementMode:
        if quality is DataQuality.INVALID:
            return MovementMode.UNKNOWN
        if speed <= self.config.stationary_max_mps and distance <= self.config.stationary_radius_m:
            return MovementMode.STATIONARY
        if elapsed < self.config.start_min_seconds and distance < self.config.start_distance_m:
            return MovementMode.UNKNOWN
        if speed <= self.config.walk_max_mps:
            return MovementMode.WALKING
        if speed < self.config.automotive_min_mps:
            return MovementMode.CYCLING
        return MovementMode.AUTOMOTIVE
