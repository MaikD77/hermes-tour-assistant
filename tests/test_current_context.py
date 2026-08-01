from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from location_core.context import (
    ContextConfig,
    ContextProcessingStatus,
    ContextStatus,
    CurrentContextEngine,
    Freshness,
    FreshnessThresholds,
    cyclic_window_matches,
)
from location_core.context_state import ContextStateRepository
from location_core.location_sources import LocationObservation
from location_core.movement import (
    DataQuality,
    EngineState,
    MovementMode,
    MovementState,
    ObservationFeature,
)
from location_core.place import (
    CandidateAccumulator,
    Place,
    PlaceEngineState,
    PlaceStatus,
    Stay,
    StayStatus,
)
from location_core.profile import (
    FactEvidence,
    FactStatus,
    FactType,
    PersonalContextFact,
    ProfileState,
)

NOW = datetime(2026, 7, 15, 10, tzinfo=UTC)


def observation(age: int = 0, device: str = "phone") -> LocationObservation:
    return LocationObservation("replay", device, NOW - timedelta(seconds=age), NOW,
        52.5, 13.4, accuracy_m=8, speed_mps=0, course_deg=90)


def movement(age: int = 0, mode: MovementMode = MovementMode.STATIONARY,
             confidence: float = .9, device: str = "phone") -> EngineState:
    at = NOW - timedelta(seconds=age)
    state = MovementState(mode, confidence, at - timedelta(minutes=5), at,
        0 if mode is MovementMode.STATIONARY else 4, 90, 5, ("speed",), DataQuality.GOOD)
    recent = (ObservationFeature("obs", device, at, 52.5, 13.4,
        state.speed_mps or 0, 90, DataQuality.GOOD),)
    return EngineState(movement=state, recent=recent)


def places(age: int = 0, candidate: bool = False) -> PlaceEngineState:
    at = NOW - timedelta(seconds=age)
    status = StayStatus.CANDIDATE if candidate else StayStatus.ACTIVE
    stay = Stay("stay_1", None if candidate else "place_1", at - timedelta(hours=1), None,
        status, 3600, 10, (52.5, 13.4), 15, .9, DataQuality.GOOD,
        MovementMode.STATIONARY)
    accumulator = CandidateAccumulator(stay, 525, 134)
    place = Place("place_1", (52.5, 13.4), 25, .9, at - timedelta(days=10), at,
        10, 36000, 3600, DataQuality.GOOD, PlaceStatus.CONFIRMED)
    return PlaceEngineState(candidate=accumulator if candidate else None,
        active_stay=None if candidate else accumulator, places=(place,),
        last_observation_id="obs", last_observed_at=at)


def profile(age: int = 0, status: FactStatus = FactStatus.CONFIRMED) -> ProfileState:
    at = NOW - timedelta(seconds=age)
    fact = PersonalContextFact("fact_1", FactType.FREQUENT_PLACE, "place_1", {}, .9,
        FactEvidence(visit_count=10), at - timedelta(days=30), at, at, 10, status)
    return ProfileState(facts=(fact,), last_computed_at=at)


def full_result(**kwargs):
    return CurrentContextEngine(kwargs.pop("config", ContextConfig())).compute(
        observation=kwargs.pop("observation", observation()),
        movement_state=kwargs.pop("movement_state", movement()),
        place_state=kwargs.pop("place_state", places()),
        profile_state=kwargs.pop("profile_state", profile()), computed_at=NOW)


def test_full_context_is_available_and_deterministic() -> None:
    first, second = full_result(), full_result()
    assert first.context.status is ContextStatus.AVAILABLE
    assert first.status is ContextProcessingStatus.COMPUTED
    assert first.context.context_id == second.context.context_id
    assert first.context.overall_confidence > .8


@pytest.mark.parametrize(("inputs", "expected"), [
    ({"observation": observation(), "movement_state": None, "place_state": None,
      "profile_state": None}, ContextStatus.PARTIAL),
    ({"observation": observation(), "movement_state": movement(), "place_state": None,
      "profile_state": None}, ContextStatus.PARTIAL),
    ({"observation": None, "movement_state": movement(), "place_state": places(),
      "profile_state": None}, ContextStatus.PARTIAL),
    ({"observation": None, "movement_state": None, "place_state": None,
      "profile_state": profile()}, ContextStatus.PARTIAL),
    ({"observation": None, "movement_state": None, "place_state": None,
      "profile_state": None}, ContextStatus.UNKNOWN),
])
def test_partial_inputs_do_not_crash(inputs, expected) -> None:
    result = CurrentContextEngine().compute(**inputs, computed_at=NOW)
    assert result.context.status is expected


@pytest.mark.parametrize(("age", "expected"), [
    (120, Freshness.FRESH), (121, Freshness.AGING), (300, Freshness.AGING),
    (301, Freshness.STALE), (900, Freshness.STALE), (901, Freshness.EXPIRED),
])
def test_location_freshness_boundaries(age: int, expected: Freshness) -> None:
    result = full_result(observation=observation(age))
    assert result.context.location_context.freshness is expected


def test_freshness_configuration_is_productive() -> None:
    config = ContextConfig(location_freshness=FreshnessThresholds(1, 2, 3))
    assert full_result(config=config, observation=observation(4)).context.location_context.freshness is Freshness.EXPIRED


def test_candidate_stay_is_explicit() -> None:
    result = full_result(place_state=places(candidate=True))
    assert result.context.place_context.matching_status == "candidate"
    assert "stay_candidate_only" in {u.code for u in result.context.uncertainties}
    assert any(t.value == "currently_at_candidate_place" for t in result.context.traits)


def test_device_mismatch_is_invalid() -> None:
    result = full_result(movement_state=movement(device="watch"))
    assert result.context.status is ContextStatus.INVALID
    assert "device_mismatch" in {u.code for u in result.context.uncertainties}


def test_stale_profile_fact_is_weak_and_marked() -> None:
    result = full_result(profile_state=profile(status=FactStatus.STALE))
    assert result.context.profile_context.status.value == "stale"
    assert result.context.profile_context.fact_confidence < .5
    assert "profile_stale" in {u.code for u in result.context.uncertainties}


def test_revoked_fact_is_never_evidence() -> None:
    result = full_result(profile_state=profile(status=FactStatus.REVOKED))
    assert result.context.profile_context.relevant_fact_ids == ()
    assert not any(e.evidence_type == "profile_fact" for e in result.context.evidence)


@pytest.mark.parametrize(("minute", "start", "end", "tolerance", "matches"), [
    (23 * 60, 22 * 60, 6 * 60, 0, True), (5 * 60, 22 * 60, 6 * 60, 0, True),
    (12 * 60, 22 * 60, 6 * 60, 0, False), (9 * 60 + 29, 600, 660, 31, True),
    (720, 600, 660, 30, False),
])
def test_cyclic_time_windows(minute, start, end, tolerance, matches) -> None:
    assert cyclic_window_matches(minute, start, end, tolerance) is matches


def test_berlin_summer_and_winter_dst() -> None:
    engine = CurrentContextEngine(ContextConfig(timezone="Europe/Berlin"))
    summer = engine.compute(computed_at=NOW).context.temporal_context
    winter = engine.compute(computed_at=datetime(2026, 1, 15, 10, tzinfo=UTC)).context.temporal_context
    assert summer.dst_active is True and summer.local_time.hour == 12
    assert winter.dst_active is False and winter.local_time.hour == 11


def test_privacy_export_and_explain() -> None:
    engine, context = CurrentContextEngine(), full_result().context
    exported, explanation = engine.export(context), engine.explain(context)
    serialized = json.dumps(exported).lower() + explanation.lower()
    assert "latitude" not in serialized and "longitude" not in serialized
    assert "52.5" not in serialized and "13.4" not in serialized
    assert "source_metadata" not in serialized and "raw_payload" not in serialized


def test_last_snapshot_persistence_and_reset(tmp_path) -> None:
    repository = ContextStateRepository(tmp_path)
    context = full_result().context
    repository.save(context)
    assert repository.load()["context_id"] == context.context_id
    repository.reset()
    assert repository.load() is None
    assert (tmp_path / "context-state.json").stat().st_mode & 0o777 == 0o600


def test_future_timestamp_is_invalid() -> None:
    result = CurrentContextEngine(ContextConfig(future_skew_seconds=1)).compute(
        observation=observation(-10), computed_at=NOW)
    assert result.context.status is ContextStatus.INVALID
    assert "clock_skew" in {u.code for u in result.context.uncertainties}
