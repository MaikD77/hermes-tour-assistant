from __future__ import annotations

import json
from datetime import timedelta

import contextctl
import pytest
from location_core.context import (
    ComponentStatus,
    ContextConfig,
    ContextProcessingStatus,
    ContextStatus,
    CurrentContextEngine,
)
from location_core.context_inputs import (
    ContextInputLoader,
    build_location_source_resolver,
)
from location_core.context_state import ContextStateRepository
from location_core.location_sources import (
    LocationSourceResolver,
    LocationSourceResult,
    LocationStatus,
    OwnTracksLocationSource,
)
from location_core.movement_state import MovementStateRepository
from location_core.place_state import PlaceStateRepository
from location_core.profile_state import ProfileStateRepository
from test_current_context import NOW, movement, observation, places, profile


class Receiver:
    def __init__(self, payload):
        self.payload = payload

    def latest_payload(self):
        return self.payload


class StubSource:
    name = "stub"

    def __init__(self, result: LocationSourceResult):
        self.result = result

    def latest(self, **_kwargs):
        return self.result


def owntracks_payload(device_id: str = "phone") -> dict[str, object]:
    return {"result": "ok", "device_id": device_id, "latitude": 52.5,
        "longitude": 13.4, "accuracy_m": 8,
        "observed_at": NOW.isoformat(), "received_at": NOW.isoformat()}


def location_resolver(result: LocationSourceResult) -> LocationSourceResolver:
    return LocationSourceResolver({"stub": StubSource(result)}, ("stub",))


def env_for(tmp_path, **changes) -> dict[str, str]:
    values = {"HERMES_CONTEXT_TIMEZONE": "Europe/Berlin",
        "HERMES_CONTEXT_STATE_DIR": str(tmp_path / "context"),
        "HERMES_MOVEMENT_STATE_DIR": str(tmp_path / "movement"),
        "HERMES_PLACE_STATE_DIR": str(tmp_path / "place"),
        "HERMES_PROFILE_STATE_DIR": str(tmp_path / "profile"),
        "HERMES_TOUR_STATE_DIR": str(tmp_path / "tour"),
        "HERMES_TOUR_CHAT_ID": "42", "HERMES_LOCATION_CANONICAL_DEVICE_ID": "phone"}
    values.update(changes)
    return values


def test_productive_factory_uses_owntracks_and_canonical_device(tmp_path) -> None:
    env = env_for(tmp_path)
    resolver = build_location_source_resolver(env)
    resolver.sources["owntracks"] = OwnTracksLocationSource(
        Receiver(owntracks_payload("provider-device")), "phone")
    result = resolver.resolve(now=NOW, max_age_seconds=300)
    assert result.status is LocationStatus.OK
    assert result.observation is not None
    assert result.observation.source == "owntracks"
    assert result.observation.device_id == "phone"


def test_productive_factory_uses_telegram_fallback(tmp_path) -> None:
    env = env_for(tmp_path)
    snapshot = tmp_path / "tour" / "telegram_live_locations.json"
    snapshot.parent.mkdir()
    snapshot.write_text(json.dumps({"locations": [{"chat_id": "42", "message_id": "m1",
        "lat": 52.5, "lon": 13.4, "updated_at": NOW.timestamp(),
        "expires_at": (NOW + timedelta(minutes=5)).timestamp()}]}))
    resolver = build_location_source_resolver(env)
    resolver.sources["owntracks"] = StubSource(LocationSourceResult(LocationStatus.UNREACHABLE))
    result = resolver.resolve(now=NOW, max_age_seconds=300)
    assert result.status is LocationStatus.OK
    assert result.observation is not None and result.observation.source == "telegram"
    assert result.observation.device_id == "phone"


@pytest.mark.parametrize("status", [LocationStatus.NOT_AVAILABLE, LocationStatus.STALE,
                                     LocationStatus.INVALID, LocationStatus.UNREACHABLE])
def test_input_loader_survives_non_ok_location(tmp_path, status) -> None:
    env = env_for(tmp_path)
    loader = ContextInputLoader(ContextConfig(timezone="Europe/Berlin"), env,
        location_resolver(LocationSourceResult(status)))
    bundle = loader.load(now=NOW)
    assert bundle.observation is None
    assert bundle.location_result.status is status
    assert bundle.issues[0].component == "location"


def test_actual_loader_and_cli_path_can_compute_available_context(tmp_path) -> None:
    env = env_for(tmp_path)
    MovementStateRepository((tmp_path / "movement")).save(movement())
    PlaceStateRepository((tmp_path / "place")).save(places())
    ProfileStateRepository((tmp_path / "profile")).save(profile())
    source = OwnTracksLocationSource(Receiver(owntracks_payload()), "phone")
    resolver = LocationSourceResolver({"owntracks": source}, ("owntracks",))
    config = ContextConfig(timezone="Europe/Berlin", state_dir=tmp_path / "context")
    snapshot = contextctl.compute_context(config, now=NOW,
        loader=ContextInputLoader(config, env, resolver))
    assert snapshot["status"] == "available"
    assert snapshot["location_context"]["observation_id"].startswith("loc_")
    assert snapshot["location_context"]["canonical_device_id"] == "phone"
    assert ContextStateRepository(tmp_path / "context").load()["context_id"] == snapshot["context_id"]


def test_loader_reports_corrupt_lower_states_without_private_paths(tmp_path) -> None:
    env = env_for(tmp_path)
    for directory, filename in (("movement", "movement-state.json"),
                                ("place", "place-state.json"),
                                ("profile", "profile-state.json")):
        path = tmp_path / directory / filename
        path.parent.mkdir()
        path.write_text("not-json")
    loader = ContextInputLoader(ContextConfig(timezone="Europe/Berlin"), env,
        location_resolver(LocationSourceResult(LocationStatus.NOT_AVAILABLE)))
    bundle = loader.load(now=NOW)
    codes = {issue.code for issue in bundle.issues}
    assert {"movement_state_invalid", "place_state_invalid", "profile_state_invalid"} <= codes
    assert str(tmp_path) not in " ".join(issue.reason for issue in bundle.issues)
    result = CurrentContextEngine().compute(computed_at=NOW, input_issues=bundle.issues)
    assert result.status is ContextProcessingStatus.INVALID_INPUT


def test_loader_reports_profile_rebuild_required(tmp_path) -> None:
    env = env_for(tmp_path)
    path = tmp_path / "profile" / "profile-state.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 1, "profile": {}}))
    bundle = ContextInputLoader(ContextConfig(timezone="Europe/Berlin"), env,
        location_resolver(LocationSourceResult(LocationStatus.NOT_AVAILABLE))).load(now=NOW)
    assert "profile_rebuild_required" in {issue.code for issue in bundle.issues}


@pytest.mark.parametrize("component", ["observation", "movement", "stay", "profile"])
def test_naive_lower_timestamp_returns_deterministic_invalid_context(component) -> None:
    obs, mov, stay_state, prof = observation(), movement(), places(), profile()
    if component == "observation":
        object.__setattr__(obs, "observed_at", obs.observed_at.replace(tzinfo=None))
    elif component == "movement":
        object.__setattr__(mov.movement, "last_observed_at",
                           mov.movement.last_observed_at.replace(tzinfo=None))
    elif component == "stay":
        object.__setattr__(stay_state.active_stay.stay, "started_at",
                           stay_state.active_stay.stay.started_at.replace(tzinfo=None))
    else:
        object.__setattr__(prof, "last_computed_at", prof.last_computed_at.replace(tzinfo=None))
    kwargs = dict(observation=obs, movement_state=mov, place_state=stay_state,
                  profile_state=prof, computed_at=NOW)
    first = CurrentContextEngine().compute(**kwargs)
    second = CurrentContextEngine().compute(**kwargs)
    assert first.status is ContextProcessingStatus.INVALID_INPUT
    assert first.context.status is ContextStatus.INVALID
    assert first.context.context_id == second.context.context_id
    assert any(u.code == "invalid_timestamp" for u in first.context.uncertainties)


def test_naive_computed_at_returns_typed_invalid_result() -> None:
    result = CurrentContextEngine().compute(computed_at=NOW.replace(tzinfo=None))
    assert result.status is ContextProcessingStatus.INVALID_INPUT
    assert result.context.status is ContextStatus.INVALID
    assert result.context.temporal_context.status is ComponentStatus.INVALID


def test_future_timestamp_inside_and_outside_tolerance() -> None:
    inside = observation()
    object.__setattr__(inside, "observed_at", NOW + timedelta(seconds=59))
    object.__setattr__(inside, "received_at", NOW + timedelta(seconds=59))
    outside = observation()
    object.__setattr__(outside, "observed_at", NOW + timedelta(seconds=61))
    object.__setattr__(outside, "received_at", NOW + timedelta(seconds=61))
    assert CurrentContextEngine().compute(observation=inside, computed_at=NOW).context.status is ContextStatus.PARTIAL
    invalid = CurrentContextEngine().compute(observation=outside, computed_at=NOW)
    assert invalid.status is ContextProcessingStatus.INVALID_INPUT
    assert invalid.context.location_context.status is ComponentStatus.INVALID


def test_config_timezone_context_then_profile_and_missing() -> None:
    assert ContextConfig.from_env({"HERMES_CONTEXT_TIMEZONE": "Europe/Berlin"}).timezone == "Europe/Berlin"
    assert ContextConfig.from_env({"HERMES_PROFILE_TIMEZONE": "Europe/Paris"}).timezone == "Europe/Paris"
    with pytest.raises(ValueError, match="TIMEZONE"):
        ContextConfig.from_env({})
