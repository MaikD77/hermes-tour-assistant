from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest
from location_core.location_sources import (
    LocationObservation,
    LocationSourceResolver,
    LocationSourceResult,
    LocationStatus,
    OwnTracksLocationSource,
    ReplayLocationSource,
    TelegramLocationSource,
    parse_source_order,
)

NOW = 2_000_000_000.0
DT_NOW = datetime.fromtimestamp(NOW, timezone.utc)


def observation(source: str = "replay", **changes) -> LocationObservation:
    values = dict(
        source=source,
        device_id="device-1",
        observed_at=datetime.fromtimestamp(NOW - 1, timezone.utc),
        received_at=DT_NOW,
        latitude=48.1,
        longitude=11.5,
    )
    values.update(changes)
    return LocationObservation(**values)


class Receiver:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error

    def latest_payload(self):
        if self.error:
            raise self.error
        return self.payload


def owntracks_payload(**changes):
    stamp = datetime.fromtimestamp(NOW - 1, timezone.utc).isoformat()
    values = {
        "result": "ok", "device_id": "phone", "latitude": 48.1,
        "longitude": 11.5, "observed_at": stamp, "received_at": stamp,
        "stale": False, "accuracy_m": 4.0, "battery_percent": 75,
    }
    values.update(changes)
    return values


def test_valid_owntracks_observation_and_optional_fields():
    result = OwnTracksLocationSource(Receiver(owntracks_payload())).latest(
        now=DT_NOW, max_age_seconds=300
    )
    assert result.status is LocationStatus.OK
    assert result.observation is not None
    assert result.observation.source == "owntracks"
    assert result.observation.accuracy_m == 4.0
    assert result.observation.speed_mps is None
    assert result.observation.source_metadata == ()


def test_valid_telegram_observation(tmp_path):
    path = tmp_path / "locations.json"
    path.write_text(
        '{"locations":[{"chat_id":"42","message_id":"m1",'
        f'"lat":48.1,"lon":11.5,"updated_at":{NOW - 1},"expires_at":{NOW + 60}'
        '}]}'
    )
    result = TelegramLocationSource(path, "42").latest(now=DT_NOW, max_age_seconds=300)
    assert result.status is LocationStatus.OK
    assert result.observation is not None and result.observation.source == "telegram"
    assert result.observation.source_metadata == (("message_id", "m1"),)


def test_adapters_share_configured_canonical_device_identity(tmp_path):
    owntracks = OwnTracksLocationSource(
        Receiver(owntracks_payload(event_id="ot-1")), "maik-iphone"
    ).latest(now=DT_NOW, max_age_seconds=300)
    path = tmp_path / "locations.json"
    path.write_text(
        '{"locations":[{"chat_id":"42","message_id":"tg-1",'
        f'"lat":48.1,"lon":11.5,"updated_at":{NOW - 1},"expires_at":{NOW + 60}'
        '}]}',
        encoding="utf-8",
    )
    telegram = TelegramLocationSource(path, "42", "maik-iphone").latest(
        now=DT_NOW, max_age_seconds=300
    )
    assert owntracks.observation is not None and telegram.observation is not None
    assert owntracks.observation.device_id == telegram.observation.device_id == "maik-iphone"
    assert dict(owntracks.observation.source_metadata) == {"event_id": "ot-1"}
    assert dict(telegram.observation.source_metadata) == {"message_id": "tg-1"}


@pytest.mark.parametrize("source", ["owntracks", "telegram"])
def test_adapters_reject_empty_canonical_device_identity(tmp_path, source):
    with pytest.raises(ValueError, match="canonical_device_id"):
        if source == "owntracks":
            OwnTracksLocationSource(Receiver(), " ")
        else:
            TelegramLocationSource(tmp_path / "locations.json", "42", " ")


def test_replay_observation_and_staleness():
    source = ReplayLocationSource([observation()])
    assert source.latest(now=DT_NOW, max_age_seconds=300).status is LocationStatus.OK
    assert source.latest(now=datetime.fromtimestamp(NOW + 500, timezone.utc), max_age_seconds=300).status is LocationStatus.STALE


class StubSource:
    def __init__(self, name, result):
        self.name, self.result, self.calls = name, result, 0

    def latest(self, **kwargs):
        self.calls += 1
        return self.result


@pytest.mark.parametrize("first_status", [LocationStatus.UNREACHABLE, LocationStatus.STALE])
def test_resolver_falls_back_and_obeys_order(first_status):
    first = StubSource("first", LocationSourceResult(first_status))
    second = StubSource("second", LocationSourceResult(LocationStatus.OK, observation()))
    result = LocationSourceResolver({"first": first, "second": second}, ["first", "second"]).resolve(
        now=DT_NOW, max_age_seconds=300
    )
    assert result.status is LocationStatus.OK
    assert first.calls == second.calls == 1


def test_resolver_does_not_call_fallback_after_valid_first():
    first = StubSource("first", LocationSourceResult(LocationStatus.OK, observation()))
    second = StubSource("second", LocationSourceResult(LocationStatus.OK, observation()))
    LocationSourceResolver({"first": first, "second": second}, ["first", "second"]).resolve(
        now=DT_NOW, max_age_seconds=300
    )
    assert first.calls == 1 and second.calls == 0


def test_configurable_default_and_legacy_order():
    assert parse_source_order(None) == ("owntracks", "telegram")
    assert parse_source_order("telegram,owntracks") == ("telegram", "owntracks")


@pytest.mark.parametrize(
    "changes",
    [
        {"latitude": 91.0}, {"longitude": -181.0}, {"latitude": "48.1"},
        {"observed_at": -1.0}, {"observed_at": datetime.fromtimestamp(NOW + 61, timezone.utc)},
        {"accuracy_m": -1.0}, {"speed_mps": -1.0}, {"course_deg": 360.0},
        {"battery_percent": 101.0},
    ],
)
def test_observation_rejects_invalid_values_without_coercion(changes):
    with pytest.raises(ValueError):
        observation(**changes)


def test_diagnostics_do_not_contain_coordinates(caplog):
    source = StubSource("replay", LocationSourceResult(LocationStatus.OK, observation()))
    with caplog.at_level(logging.INFO):
        LocationSourceResolver({"replay": source}, ["replay"]).resolve(
            now=DT_NOW, max_age_seconds=300
        )
    assert "48.1" not in caplog.text and "11.5" not in caplog.text


def test_observation_id_is_deterministic_and_coordinate_free():
    first = observation(source_metadata={"replay_id": "event-1"})
    second = observation(source_metadata={"replay_id": "event-1"})
    assert first.observation_id == second.observation_id
    assert "48.1" not in first.observation_id
    assert "11.5" not in first.observation_id


def test_observation_id_changes_with_canonical_input():
    assert observation().observation_id != observation(latitude=48.2).observation_id


def test_metadata_mapping_order_does_not_change_identity():
    first = observation(source_metadata={"replay_id": "r1", "event_id": "e1"})
    second = observation(source_metadata={"event_id": "e1", "replay_id": "r1"})
    assert first.source_metadata == second.source_metadata
    assert first.observation_id == second.observation_id


def test_source_metadata_is_immutable_allowlisted_and_safe():
    assert observation().source_metadata == ()
    with pytest.raises((AttributeError, TypeError)):
        observation().source_metadata["event_id"] = "changed"
    with pytest.raises(ValueError, match="not allowed"):
        observation(source_metadata={"raw_payload": "no"})
    with pytest.raises(ValueError, match="credentials or URLs"):
        observation(source_metadata={"event_id": "https://user:secret@example.test"})
    with pytest.raises(ValueError, match="coordinate copies"):
        observation(source_metadata={"event_id": "48.12345, 11.54321"})


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="timezone"):
        observation(observed_at=datetime(2033, 5, 18))
