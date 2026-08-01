from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from location_core.location_sources import LocationObservation
from location_core.movement import DataQuality
from location_core.place import (
    MatchStatus,
    Place,
    PlaceConfig,
    PlaceEngine,
    PlaceEventType,
    PlaceStatus,
    StayStatus,
    aggregate_quality,
)
from location_core.place_state import PlaceStateRepository
from location_core.repository import CorruptStateError

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def observation(index: int, seconds: int, *, lat: float = 50, lon: float = 10,
                accuracy: float = 8, speed: float = 0) -> LocationObservation:
    when = BASE + timedelta(seconds=seconds)
    return LocationObservation("test", "device", when, when, lat, lon,
                               accuracy_m=accuracy, speed_mps=speed,
                               source_metadata={"replay_id": str(index)})


def config(**values: object) -> PlaceConfig:
    configured: dict[str, object] = {
        "candidate_seconds": 120, "confirmed_seconds": 300,
        "departure_confirmation_seconds": 120, "promotion_visits": 2,
        "promotion_dwell_seconds": 600,
    }
    configured.update(values)
    return PlaceConfig(**configured)


def confirm(engine: PlaceEngine, *, offset: int = 0, lat: float = 50,
            lon: float = 10, index: int = 0):
    outputs = [engine.process(observation(index, offset, lat=lat, lon=lon)),
               engine.process(observation(index + 1, offset + 120, lat=lat + .00005, lon=lon)),
               engine.process(observation(index + 2, offset + 300, lat=lat, lon=lon + .00005))]
    return outputs


def depart(engine: PlaceEngine, *, offset: int, index: int):
    engine.process(observation(index, offset, lat=50.002, lon=10.002, speed=2))
    return engine.process(observation(index + 1, offset + 120,
                                      lat=50.003, lon=10.003, speed=2))


def test_single_observation_starts_only_candidate_and_duplicate_is_ignored() -> None:
    engine = PlaceEngine(config())
    item = observation(0, 0)
    first = engine.process(item)
    assert first.status.value == "candidate"
    assert first.active_stay is None
    assert [e.event_type for e in first.events] == [PlaceEventType.STAY_CANDIDATE_STARTED]
    assert engine.process(item).status.value == "duplicate"


@pytest.mark.parametrize(("seconds", "active"), [(299, False), (300, True)])
def test_confirmation_boundary(seconds: int, active: bool) -> None:
    engine = PlaceEngine(config())
    engine.process(observation(0, 0))
    engine.process(observation(1, 120))
    result = engine.process(observation(2, seconds))
    assert (result.active_stay is not None) is active


def test_confirmed_stay_backdates_arrival_and_has_deterministic_events() -> None:
    first = PlaceEngine(config())
    second = PlaceEngine(config())
    outputs = [confirm(first), confirm(second)]
    event = next(e for e in outputs[0][-1].events if e.event_type is PlaceEventType.STAY_CONFIRMED)
    assert event.observed_at == BASE
    assert event.confirmed_at == BASE + timedelta(seconds=300)
    assert outputs[0][-1].events == outputs[1][-1].events
    assert outputs[0][-1].active_stay.status is StayStatus.ACTIVE


def test_hysteresis_ignores_drift_bad_accuracy_and_short_exit() -> None:
    engine = PlaceEngine(config())
    confirm(engine)
    assert engine.process(observation(3, 360, lat=50.002, accuracy=250)).active_stay
    assert engine.process(observation(4, 400, lat=50.001, lon=10.001)).active_stay
    result = engine.process(observation(5, 440, lat=50, lon=10))
    assert result.active_stay
    assert not any(e.event_type is PlaceEventType.STAY_COMPLETED for e in result.events)


def test_confirmed_departure_creates_visit_without_track() -> None:
    engine = PlaceEngine(config())
    confirm(engine)
    result = depart(engine, offset=600, index=3)
    assert result.status.value == "departure_confirmed"
    assert result.active_stay is None
    assert len(result.state.visits) == 1
    visit = result.state.visits[0]
    completed = next(e for e in result.events
                     if e.event_type is PlaceEventType.STAY_COMPLETED)
    assert completed.observed_at == BASE + timedelta(seconds=600)
    assert completed.confirmed_at == BASE + timedelta(seconds=720)
    assert visit.departed_at == completed.observed_at
    assert visit.duration_seconds == 600
    assert not hasattr(visit, "observations")
    assert {e.event_type for e in result.events} >= {
        PlaceEventType.STAY_COMPLETED, PlaceEventType.PLACE_DEPARTED}


def test_long_gap_is_uncertainty_not_departure() -> None:
    engine = PlaceEngine(config())
    confirm(engine)
    result = engine.process(observation(3, 2500, lat=51, lon=11))
    assert result.status.value == "gap_detected"
    assert result.active_stay is not None
    assert not result.events


def test_matching_shifted_place_and_ambiguity() -> None:
    engine = PlaceEngine(config())
    first = confirm(engine)[-1]
    place_id = first.active_stay.place_id
    depart(engine, offset=600, index=3)
    second = confirm(engine, offset=1000, lat=50.0002, lon=10.0002, index=5)[-1]
    assert second.match.status is MatchStatus.MATCHED
    assert second.active_stay.place_id == place_id
    stay = second.active_stay
    close = Place("other", engine.state.places[0].centroid, 30, .8,
                  BASE, BASE, 3, 1000, 300, DataQuality.GOOD, PlaceStatus.CONFIRMED)
    engine.state = engine.state.__class__(**{**engine.state.__dict__,
                              "places": engine.state.places + (close,)})
    assert engine.match(stay).status is MatchStatus.AMBIGUOUS


def test_promotion_requires_repeated_completed_visits_and_id_stays_stable() -> None:
    engine = PlaceEngine(config())
    first = confirm(engine)[-1].active_stay.place_id
    depart(engine, offset=600, index=3)
    assert engine.state.places[0].status is PlaceStatus.CANDIDATE
    second = confirm(engine, offset=1000, lat=50.0001, index=5)[-1]
    assert second.active_stay.place_id == first
    depart(engine, offset=1600, index=8)
    assert engine.state.places[0].place_id == first
    assert engine.state.places[0].status is PlaceStatus.CONFIRMED


def test_fast_observation_does_not_create_stay() -> None:
    engine = PlaceEngine(config())
    assert engine.process(observation(0, 0, speed=12)).status.value == "insufficient_evidence"


def test_diagnostics_and_repr_never_contain_coordinates() -> None:
    result = PlaceEngine(config()).process(observation(0, 0, lat=50.123456, lon=10.654321))
    rendered = json.dumps(result.diagnostic)
    assert "50.123456" not in rendered and "10.654321" not in rendered


def test_repository_round_trip_quantizes_and_forgets(tmp_path) -> None:
    repository = PlaceStateRepository(tmp_path)
    engine = PlaceEngine(config())
    confirm(engine, lat=50.123456, lon=10.654321)
    depart(engine, offset=600, index=3)
    place_id = engine.state.places[0].place_id
    repository.save(engine.state)
    raw = (tmp_path / "place-state.json").read_text()
    assert "50.123456" not in raw and "10.654321" not in raw
    assert os.stat(tmp_path / "place-state.json").st_mode & 0o777 == 0o600
    assert repository.load().places[0].place_id == place_id
    assert repository.forget(place_id)
    assert not repository.load().places and not repository.load().visits


def test_repository_reset_and_corrupt_quarantine(tmp_path) -> None:
    repository = PlaceStateRepository(tmp_path)
    repository.reset()
    (tmp_path / "place-state.json").write_text("not-json")
    with pytest.raises(CorruptStateError):
        repository.load()
    assert list(tmp_path.glob("place-state.json.corrupt-*"))


def test_repository_rejects_symlink(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        PlaceStateRepository(link).reset()


@pytest.mark.parametrize(("good", "limited", "poor", "expected"), [
    (4, 0, 0, DataQuality.GOOD),
    (0, 4, 0, DataQuality.LIMITED),
    (3, 0, 1, DataQuality.GOOD),
    (1, 0, 3, DataQuality.POOR),
    (1, 1, 0, DataQuality.LIMITED),
    (2, 1, 0, DataQuality.GOOD),
    (0, 0, 0, DataQuality.INVALID),
])
def test_explicit_quality_aggregation(good: int, limited: int, poor: int,
                                      expected: DataQuality) -> None:
    assert aggregate_quality(good, limited, poor) is expected


def test_single_poor_outlier_reduces_confidence_without_dominating_stay() -> None:
    engine = PlaceEngine(config())
    confirm(engine)
    before = engine.state.active_stay.stay.confidence
    result = engine.process(observation(3, 360, lat=55, lon=15, accuracy=500))
    assert result.active_stay.data_quality is DataQuality.GOOD
    assert result.active_stay.confidence < before
    assert result.active_stay.centroid == engine.state.places[0].centroid


def test_quality_aggregation_survives_persistence_reload(tmp_path) -> None:
    engine = PlaceEngine(config())
    confirm(engine)
    engine.process(observation(3, 360, accuracy=500))
    repository = PlaceStateRepository(tmp_path)
    repository.save(engine.state)
    loaded = repository.load()
    assert loaded.active_stay is not None
    assert loaded.active_stay.good_quality_count == 3
    assert loaded.active_stay.poor_quality_count == 1
    resumed = PlaceEngine(config(), loaded)
    result = resumed.process(observation(4, 420, accuracy=50))
    assert result.active_stay.data_quality is DataQuality.GOOD


def test_poor_quality_stay_cannot_promote_place() -> None:
    engine = PlaceEngine(config(promotion_visits=1, promotion_dwell_seconds=1))
    confirm(engine)
    for index, seconds in enumerate((360, 420, 480, 540), start=3):
        engine.process(observation(index, seconds, accuracy=500))
    depart(engine, offset=600, index=7)
    assert engine.state.places[0].status is PlaceStatus.CANDIDATE


def test_pending_departure_survives_reload_and_backdates_departure(tmp_path) -> None:
    engine = PlaceEngine(config())
    confirm(engine)
    first_outside = engine.process(observation(3, 600, lat=50.002, lon=10.002))
    assert first_outside.active_stay is not None
    assert not first_outside.events
    repository = PlaceStateRepository(tmp_path)
    repository.save(engine.state)
    resumed = PlaceEngine(config(), repository.load())
    result = resumed.process(observation(4, 720, lat=50.003, lon=10.003))
    event = next(e for e in result.events if e.event_type is PlaceEventType.STAY_COMPLETED)
    assert event.observed_at == BASE + timedelta(seconds=600)
    assert event.confirmed_at == BASE + timedelta(seconds=720)


def test_return_during_pending_departure_cancels_it() -> None:
    engine = PlaceEngine(config())
    confirm(engine)
    engine.process(observation(3, 600, lat=50.002, lon=10.002))
    result = engine.process(observation(4, 650, lat=50, lon=10))
    assert result.active_stay is not None
    assert result.state.active_stay.departure_observed_at is None
    assert not result.events


def test_gap_during_pending_departure_restarts_evidence_window() -> None:
    engine = PlaceEngine(config())
    confirm(engine)
    engine.process(observation(3, 600, lat=50.002, lon=10.002))
    after_gap = engine.process(observation(4, 1000, lat=50.003, lon=10.003))
    assert after_gap.active_stay is not None
    assert after_gap.state.active_stay.departure_observed_at == BASE + timedelta(seconds=1000)
    result = engine.process(observation(5, 1120, lat=50.004, lon=10.004))
    event = next(e for e in result.events if e.event_type is PlaceEventType.STAY_COMPLETED)
    assert event.observed_at == BASE + timedelta(seconds=1000)
    assert event.confirmed_at == BASE + timedelta(seconds=1120)


def test_from_env_loads_all_operational_configuration(tmp_path) -> None:
    env = {
        "HERMES_PLACE_STAY_RADIUS_M": "40", "HERMES_PLACE_CANDIDATE_SECONDS": "60",
        "HERMES_PLACE_CONFIRMED_SECONDS": "180", "HERMES_PLACE_MINIMUM_OBSERVATIONS": "4",
        "HERMES_PLACE_DEPARTURE_RADIUS_M": "70", "HERMES_PLACE_DEPARTURE_SECONDS": "90",
        "HERMES_PLACE_MATCH_RADIUS_M": "65", "HERMES_PLACE_MAXIMUM_ACCURACY_M": "80",
        "HERMES_PLACE_SHORT_GAP_SECONDS": "200", "HERMES_PLACE_LONG_GAP_SECONDS": "800",
        "HERMES_PLACE_PROMOTION_VISITS": "2", "HERMES_PLACE_PROMOTION_DWELL_SECONDS": "900",
        "HERMES_PLACE_VISIT_RETENTION": "50", "HERMES_PLACE_DEDUPLICATION_RETENTION": "64",
        "HERMES_PLACE_CENTROID_PRECISION": "5", "HERMES_PLACE_STATE_DIR": str(tmp_path),
    }
    loaded = PlaceConfig.from_env(env)
    assert loaded.minimum_observations == 4 and loaded.maximum_accuracy_m == 80
    assert loaded.short_gap_seconds == 200 and loaded.long_gap_seconds == 800
    assert loaded.promotion_visits == 2 and loaded.promotion_dwell_seconds == 900
    assert loaded.visit_retention == 50 and loaded.deduplication_retention == 64
    assert loaded.centroid_precision == 5 and loaded.state_dir == tmp_path


@pytest.mark.parametrize(("name", "value"), [
    ("HERMES_PLACE_MINIMUM_OBSERVATIONS", "2.5"),
    ("HERMES_PLACE_CENTROID_PRECISION", "2"),
    ("HERMES_PLACE_LONG_GAP_SECONDS", "100"),
])
def test_from_env_strictly_validates_types_and_relationships(name: str, value: str) -> None:
    env = {name: value}
    if name == "HERMES_PLACE_LONG_GAP_SECONDS":
        env["HERMES_PLACE_SHORT_GAP_SECONDS"] = "200"
    with pytest.raises(ValueError):
        PlaceConfig.from_env(env)
