from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from location_core.movement import DataQuality, MovementMode, MovementSegment, SegmentStatus
from location_core.place import PlaceVisit
from location_core.profile import (
    FactStatus,
    FactType,
    MobilityProfileEngine,
    ProfileConfig,
    circular_window,
    overnight_seconds,
)
from location_core.profile_state import ProfileStateRepository
from location_core.repository import CorruptStateError


def visit(number: int, place: str = "place_A", *, hour: int = 8,
          duration: int = 3600, confidence: float = .9,
          base: datetime = datetime(2026, 1, 1, tzinfo=UTC)) -> PlaceVisit:
    arrived = base + timedelta(days=number, hours=hour)
    return PlaceVisit(f"visit_{place}_{number}_{hour}", place, f"stay_{number}", arrived,
                      arrived + timedelta(seconds=duration), duration, None, None, confidence)


def config(**changes: object) -> ProfileConfig:
    values = {"timezone": "Europe/Berlin", "candidate_visits": 3,
              "confirmed_visits": 5, "confirmed_distinct_days": 4,
              "transition_minimum_samples": 3}
    values.update(changes)
    return ProfileConfig(**values)  # type: ignore[arg-type]


def segment(number: int, start: datetime, duration: int = 1800,
            mode: MovementMode = MovementMode.WALKING, gap: int = 0) -> MovementSegment:
    return MovementSegment(f"segment_{number}", mode, start,
        start + timedelta(seconds=duration), SegmentStatus.COMPLETED, "start", "end", 5,
        duration, 1000, 900, .8, 1.2, None, .8, .9, DataQuality.GOOD, gap)


def test_requires_explicit_valid_timezone() -> None:
    with pytest.raises(ValueError, match="required"):
        ProfileConfig.from_env({})
    with pytest.raises(ValueError, match="IANA"):
        ProfileConfig(timezone="Moon/Base")


def test_configuration_reads_every_threshold(tmp_path) -> None:
    cfg = ProfileConfig.from_env({"HERMES_PROFILE_TIMEZONE": "UTC",
        "HERMES_PROFILE_CANDIDATE_VISITS": "2", "HERMES_PROFILE_CONFIRMED_VISITS": "4",
        "HERMES_PROFILE_CONFIRMED_DISTINCT_DAYS": "3", "HERMES_PROFILE_NIGHT_START": "21:30",
        "HERMES_PROFILE_NIGHT_END": "05:15", "HERMES_PROFILE_MIN_OVERNIGHT_SECONDS": "7200",
        "HERMES_PROFILE_STALE_DAYS": "10", "HERMES_PROFILE_REVOKE_DAYS": "20",
        "HERMES_PROFILE_CANDIDATE_CONFIDENCE": ".2", "HERMES_PROFILE_CONFIRMED_CONFIDENCE": ".5",
        "HERMES_PROFILE_TRANSITION_MIN_SAMPLES": "2", "HERMES_PROFILE_MAX_TRANSITION_SECONDS": "5000",
        "HERMES_PROFILE_RETENTION_DAYS": "100", "HERMES_PROFILE_DEDUPLICATION_LIMIT": "20",
        "HERMES_PROFILE_STATE_DIR": str(tmp_path)})
    assert (cfg.candidate_visits, cfg.night_start.hour, cfg.retention_days, cfg.state_dir) == (2, 21, 100, tmp_path)


def test_candidate_needs_multiple_visits_and_same_day_not_confirmed() -> None:
    engine = MobilityProfileEngine(config())
    engine.process_visit(visit(0))
    assert not engine.state.facts
    engine.process_visit(visit(0, hour=10))
    engine.process_visit(visit(0, hour=12))
    assert engine.state.facts
    assert {f.status for f in engine.state.facts} == {FactStatus.CANDIDATE}
    assert engine.state.place_statistics[0].distinct_days == 1


def test_confirmed_frequent_daytime_place_has_structured_evidence() -> None:
    engine = MobilityProfileEngine(config())
    for number in range(8):
        engine.process_visit(visit(number), computed_at=visit(number).departed_at)
    fact = next(f for f in engine.state.facts if f.fact_type is FactType.FREQUENT_PLACE)
    assert fact.status is FactStatus.CONFIRMED
    assert fact.evidence.visit_count == 8
    assert fact.evidence.distinct_days == 8
    assert 0 <= fact.confidence <= 1
    assert any(f.fact_type is FactType.FREQUENT_DAYTIME_PLACE for f in engine.state.facts)


def test_low_quality_reduces_confidence() -> None:
    good = MobilityProfileEngine(config())
    poor = MobilityProfileEngine(config())
    for number in range(6):
        good.process_visit(visit(number))
        poor.process_visit(visit(number, confidence=.1))
    assert good.state.facts[0].confidence > poor.state.facts[0].confidence


def test_stale_and_revoked_are_recomputed() -> None:
    visits = [visit(i) for i in range(6)]
    last = visits[-1].departed_at
    stale = MobilityProfileEngine(config(stale_days=10, revoke_days=20)).rebuild(
        visits, computed_at=last + timedelta(days=11))
    assert {f.status for f in stale.facts} == {FactStatus.STALE}
    revoked = MobilityProfileEngine(config(stale_days=10, revoke_days=20)).rebuild(
        visits, computed_at=last + timedelta(days=21))
    assert {f.status for f in revoked.facts} == {FactStatus.REVOKED}


@pytest.mark.parametrize(("hour", "duration", "expected"), [(23, 4 * 3600, True),
    (20, 10 * 3600, True), (21, 90 * 60, False)])
def test_overnight_semantics(hour: int, duration: int, expected: bool) -> None:
    item = visit(0, hour=hour, duration=duration)
    assert (overnight_seconds(item, config()) >= config().minimum_overnight_seconds) is expected


def test_dst_night_uses_real_elapsed_time() -> None:
    start = datetime(2026, 3, 28, 21, tzinfo=UTC)
    item = PlaceVisit("dst", "p", "s", start, start + timedelta(hours=8), 28800, None, None, .9)
    assert overnight_seconds(item, config()) == 7 * 3600


def test_overnight_fact_is_not_semantic_role() -> None:
    engine = MobilityProfileEngine(config())
    for number in range(6):
        engine.process_visit(visit(number, hour=22, duration=9 * 3600))
    types = {f.fact_type.value for f in engine.state.facts}
    assert "frequent_overnight_place" in types
    assert not ({"home", "work", "commute_to_work"} & types)


def test_circular_window_handles_midnight_and_outlier() -> None:
    start, end = circular_window([1430, 1435, 5, 10, 720])
    assert start > 1400 and end < 30


def test_incremental_is_idempotent_and_matches_rebuild() -> None:
    visits = [visit(i) for i in range(6)]
    incremental = MobilityProfileEngine(config())
    for item in visits:
        incremental.process_visit(item)
        incremental.process_visit(item)
    rebuilt = MobilityProfileEngine(config()).rebuild(visits)
    assert incremental.state == rebuilt


def test_transition_direction_mode_duration_outlier_and_duplicate() -> None:
    engine = MobilityProfileEngine(config())
    for number, duration in enumerate((1800, 1900, 1850, 9000)):
        a = visit(number, "place_A", duration=3600)
        b = visit(number, "place_B", hour=10, duration=3600)
        start = a.departed_at
        item = segment(number, start, duration=(b.arrived_at - start).seconds)
        engine.process_transition(a, b, [item])
        engine.process_transition(a, b, [item])
    pattern = engine.state.transitions[0]
    assert (pattern.from_place_id, pattern.to_place_id, pattern.sample_count) == ("place_A", "place_B", 4)
    assert pattern.typical_mode is MovementMode.WALKING
    assert pattern.typical_duration_seconds == 3600


def test_transition_rejects_gap_unknown_stay_implausible_and_same_place() -> None:
    a, b = visit(0, "A"), visit(0, "B", hour=10)
    movement = segment(0, a.departed_at, duration=3600)
    engine = MobilityProfileEngine(config())
    engine.process_transition(a, b, [movement], data_gap=True)
    engine.process_transition(a, b, [movement], unknown_intermediate_stay=True)
    engine.process_transition(a, a, [movement])
    engine.process_transition(a, b, [segment(1, a.departed_at, 3600, gap=1)])
    assert not engine.state.transition_samples


def test_state_roundtrip_export_forget_reset_and_permissions(tmp_path) -> None:
    repo = ProfileStateRepository(tmp_path)
    engine = MobilityProfileEngine(config())
    for number in range(4):
        engine.process_visit(visit(number))
    repo.save(engine.state)
    assert repo.load() == engine.state
    exported = json.dumps(repo.export())
    assert "latitude" not in exported and "longitude" not in exported
    assert "visit_place_A_0" not in exported
    assert oct((tmp_path / "profile-state.json").stat().st_mode & 0o777) == "0o600"
    assert repo.forget_place("place_A")
    assert not repo.load().facts
    repo.reset()
    assert not repo.load().place_statistics


def test_explain_is_deterministic_and_contains_no_coordinates() -> None:
    engine = MobilityProfileEngine(config())
    for number in range(4):
        engine.process_visit(visit(number))
    result = engine.explain(engine.state.facts[0].fact_id)
    assert "4 visits" in result and "Confidence" in result
    assert "latitude" not in result and "longitude" not in result


def test_repository_symlink_and_corruption_protection(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    symlink = tmp_path / "link"
    symlink.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError, match="symbolic"):
        ProfileStateRepository(symlink).load()
    repo = ProfileStateRepository(tmp_path / "state")
    repo.repository.ensure_private_directory()
    repo.repository.path.write_text("not json")
    with pytest.raises(CorruptStateError):
        repo.load()
    assert list((tmp_path / "state").glob("*.corrupt-*"))


def test_lock_file_is_private(tmp_path) -> None:
    repo = ProfileStateRepository(tmp_path)
    repo.load()
    assert (os.stat(tmp_path / "profile-state.json.lock").st_mode & 0o777) == 0o600
