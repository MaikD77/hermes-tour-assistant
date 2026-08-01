from datetime import UTC, datetime, timedelta

import pytest
from location_core.location_sources import LocationObservation
from location_core.movement import (
    DataQuality,
    MovementConfig,
    MovementEngine,
    MovementEventType,
    MovementMode,
    ProcessingStatus,
    angular_difference,
    bearing_deg,
)
from location_core.movement_state import MovementStateRepository

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def observation(index: int, *, lat: float = 52.0, lon: float = 13.0,
                speed: float | None = None, course: float | None = None,
                accuracy: float = 5, device: str = "phone",
                seconds: int | None = None, source: str = "test") -> LocationObservation:
    when = BASE + timedelta(seconds=index * 30 if seconds is None else seconds)
    return LocationObservation(source, device, when, when, lat, lon, accuracy_m=accuracy,
                               speed_mps=speed, course_deg=course,
                               source_metadata={"replay_id": str(index)})


def engine() -> MovementEngine:
    return MovementEngine(MovementConfig(transition_observations=2,
                                         transition_min_seconds=20,
                                         stationary_min_seconds=20,
                                         cooldown_seconds=1))


def test_geodesic_helpers() -> None:
    assert bearing_deg(0, 0, 1, 0) == pytest.approx(0)
    assert bearing_deg(0, 0, 0, 1) == pytest.approx(90)
    assert angular_difference(350, 10) == 20


def test_first_duplicate_and_ordering_are_typed() -> None:
    movement = engine()
    first = observation(0)
    assert movement.process(first).status is ProcessingStatus.INSUFFICIENT_EVIDENCE
    assert movement.process(first).status is ProcessingStatus.DUPLICATE
    assert movement.process(observation(1, seconds=-30)).status is ProcessingStatus.OUT_OF_ORDER
    assert movement.process(observation(2, seconds=0)).status is ProcessingStatus.OUT_OF_ORDER


def test_device_isolation_and_source_switch() -> None:
    movement = engine()
    movement.process(observation(0))
    assert movement.process(observation(1, device="watch")).status is ProcessingStatus.INVALID
    assert movement.process(observation(1, source="other")).status is not ProcessingStatus.INVALID


@pytest.mark.parametrize(("speed", "mode"), [
    (0.0, MovementMode.STATIONARY), (1.4, MovementMode.WALKING),
    (5.5, MovementMode.CYCLING), (15, MovementMode.AUTOMOTIVE),
])
def test_confirmed_speed_modes(speed: float, mode: MovementMode) -> None:
    movement = engine()
    longitude_step = speed * 30 / 68_000
    movement.process(observation(0, speed=speed))
    movement.process(observation(1, lon=13 + longitude_step, speed=speed))
    result = movement.process(observation(2, lon=13 + 2 * longitude_step, speed=speed))
    assert result.state.movement is not None
    assert result.state.movement.mode is mode


def test_segment_and_events_are_deterministic() -> None:
    observations = [observation(i, lon=13 + i * 0.0005, speed=5, course=90) for i in range(4)]
    outputs = []
    for _ in range(2):
        movement = engine()
        outputs.append([movement.process(item) for item in observations])
    assert outputs[0][-1].segment == outputs[1][-1].segment
    assert outputs[0][-1].events == outputs[1][-1].events
    kinds = {event.event_type for result in outputs[0] for event in result.events}
    assert MovementEventType.STARTED in kinds
    assert MovementEventType.SEGMENT_STARTED in kinds
    assert MovementEventType.SEGMENT_UPDATED in kinds
    segment = outputs[0][-1].segment
    assert segment is not None and segment.distance_m > 0 and segment.observation_count == 2


def test_gap_event_and_long_gap_completion() -> None:
    movement = engine()
    for i in range(3):
        movement.process(observation(i, lon=13 + i * 0.0005, speed=5))
    result = movement.process(observation(4, lon=13.002, speed=5, seconds=1200))
    assert result.status is ProcessingStatus.GAP_DETECTED
    assert result.data_quality is DataQuality.POOR
    assert MovementEventType.DATA_GAP in {event.event_type for event in result.events}
    assert result.completed_segment is not None


def test_jump_and_bad_accuracy_cannot_trigger_transition() -> None:
    movement = engine()
    movement.process(observation(0))
    jump = movement.process(observation(1, lat=53, lon=14, speed=40))
    assert jump.data_quality is DataQuality.POOR or jump.data_quality is DataQuality.INVALID
    movement.process(observation(2, lat=53.0001, lon=14.0001, speed=15, accuracy=500))
    assert movement.state.movement is not None
    assert movement.state.movement.confidence <= 0.35


def test_configuration_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        MovementConfig(walk_max_mps=20)
    with pytest.raises(ValueError, match="numeric"):
        MovementConfig.from_env({"HERMES_MOVEMENT_WALK_MAX_MPS": "fast"})


def test_segment_metrics_survive_500_observations_and_buffer_rotation() -> None:
    config = MovementConfig(transition_observations=2, transition_min_seconds=20,
                            cooldown_seconds=1, buffer_size=8)
    movement = MovementEngine(config)
    step = 0.00002
    for index in range(500):
        result = movement.process(observation(index, lon=13 + index * step,
                                              speed=1.5, course=90))
    segment = result.segment
    assert segment is not None
    assert len(result.state.recent) == config.buffer_size
    assert segment.observation_count == 498
    expected_displacement = 497 * step * 68_000
    assert segment.displacement_m == pytest.approx(expected_displacement, rel=0.02)
    assert segment.distance_m == pytest.approx(segment.displacement_m, rel=0.01)
    assert segment.average_speed_mps == pytest.approx(
        segment.distance_m / segment.duration_seconds, abs=0.001
    )
    assert segment.maximum_speed_mps >= segment.average_speed_mps
    assert segment.dominant_heading_deg == pytest.approx(90, abs=0.1)
    assert segment.heading_stability == pytest.approx(1)


def test_segment_accumulator_survives_persistence(tmp_path) -> None:
    config = MovementConfig(transition_observations=2, transition_min_seconds=20,
                            cooldown_seconds=1, buffer_size=5)
    movement = MovementEngine(config)
    step = 0.00002
    for index in range(250):
        movement.process(observation(index, lon=13 + index * step,
                                     speed=1.5, course=90))
    repository = MovementStateRepository(tmp_path)
    repository.save(movement.state)
    restored = MovementEngine(config, repository.load())
    for index in range(250, 500):
        result = restored.process(observation(index, lon=13 + index * step,
                                              speed=1.5, course=90))
    assert result.segment is not None
    assert result.segment.observation_count == 498
    assert result.segment.displacement_m == pytest.approx(497 * step * 68_000, rel=0.02)
    assert result.state.segment_accumulator is not None
    assert "13." not in repr(result.state.segment_accumulator)


@pytest.mark.parametrize("sources", [("owntracks", "telegram"),
                                     ("telegram", "owntracks")])
def test_source_switch_keeps_active_segment(sources: tuple[str, str]) -> None:
    movement = engine()
    for index in range(3):
        result = movement.process(observation(index, lon=13 + index * 0.0005,
                                              speed=5, source=sources[0]))
    assert result.segment is not None
    segment_id = result.segment.segment_id
    switched = movement.process(observation(3, lon=13.0015, speed=5, source=sources[1]))
    assert switched.segment is not None
    assert switched.segment.segment_id == segment_id
    assert switched.segment.observation_count == result.segment.observation_count + 1
    assert movement.process(observation(3, lon=13.0015, speed=5,
                                        source=sources[1])).status is ProcessingStatus.DUPLICATE
    assert movement.process(observation(4, lon=13.002, speed=5,
                                        source=sources[1], device="other")).status is ProcessingStatus.INVALID


def test_stationary_requires_full_minimum_duration() -> None:
    config = MovementConfig(stationary_min_seconds=90, transition_observations=2,
                            transition_min_seconds=10, cooldown_seconds=1)
    movement = MovementEngine(config)
    movement.process(observation(0, speed=0))
    movement.process(observation(1, speed=0))
    before = movement.process(observation(3, speed=0, seconds=119))
    assert before.state.movement is not None
    assert before.state.movement.mode is MovementMode.UNKNOWN
    exact = movement.process(observation(4, speed=0, seconds=120))
    assert exact.state.movement is not None
    assert exact.state.movement.mode is MovementMode.STATIONARY


def test_stationary_radius_resets_on_drift_outside_radius() -> None:
    config = MovementConfig(stationary_min_seconds=60, transition_observations=2,
                            transition_min_seconds=10, cooldown_seconds=1)
    movement = MovementEngine(config)
    movement.process(observation(0, speed=0))
    movement.process(observation(1, lon=13.00005, speed=0))  # inside radius
    movement.process(observation(2, lon=13.0003, speed=0))  # outside pending radius
    result = movement.process(observation(4, lon=13.0003, speed=0, seconds=119))
    assert result.state.movement is not None
    assert result.state.movement.mode is MovementMode.UNKNOWN
    confirmed = movement.process(observation(6, lon=13.0003, speed=0, seconds=179))
    assert confirmed.state.movement is not None
    assert confirmed.state.movement.mode is MovementMode.STATIONARY


def test_traffic_light_stop_does_not_complete_cycling_segment() -> None:
    config = MovementConfig(stationary_min_seconds=90, transition_observations=2,
                            transition_min_seconds=20, cooldown_seconds=1)
    movement = MovementEngine(config)
    for index in range(3):
        moving = movement.process(observation(index, lon=13 + index * 0.0005, speed=5))
    assert moving.segment is not None
    segment_id = moving.segment.segment_id
    movement.process(observation(3, lon=13.001, speed=0))
    stopped_briefly = movement.process(observation(4, lon=13.001, speed=0))
    assert stopped_briefly.segment is not None
    assert stopped_briefly.segment.segment_id == segment_id
    assert stopped_briefly.completed_segment is None


@pytest.mark.parametrize(("speed", "expected"), [
    (0.699, MovementMode.STATIONARY), (0.7, MovementMode.STATIONARY),
    (0.701, MovementMode.WALKING), (2.599, MovementMode.WALKING),
    (2.6, MovementMode.WALKING), (2.601, MovementMode.CYCLING),
    (9.999, MovementMode.CYCLING), (10.0, MovementMode.AUTOMOTIVE),
    (10.001, MovementMode.AUTOMOTIVE),
])
def test_speed_threshold_boundaries(speed: float, expected: MovementMode) -> None:
    config = MovementConfig()
    distance = 0 if expected is MovementMode.STATIONARY else 100
    assert MovementEngine(config)._classify(speed, distance, 30, DataQuality.GOOD) is expected
