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
    return PlaceConfig(candidate_seconds=120, confirmed_seconds=300,
                       departure_confirmation_seconds=120, promotion_visits=2,
                       promotion_dwell_seconds=600, **values)


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
