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
