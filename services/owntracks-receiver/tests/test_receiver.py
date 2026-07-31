"""Automated tests for the OwnTracks receiver.

Run with:
    OWNTRACKS_API_KEY=test-key pytest tests/test_receiver.py -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["OWNTRACKS_API_KEY"] = "test-key"
from models import Location, LocationStore  # noqa: E402
from owntracks_receiver import app  # noqa: E402
from validation import PayloadError, normalize_location  # noqa: E402

client = TestClient(app)

API_KEY = "test-key"
AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}

NOW = int(time.time())


def loc_payload(**overrides) -> dict:
    """Build a valid OwnTracks location payload."""
    base = {
        "_type": "location",
        "lat": 50.978,
        "lon": 11.029,
        "tst": NOW,
        "acc": 15.0,
        "alt": 200.0,
        "batt": 85,
        "conn": "w",
        "t": "p",
        "tid": "MD",
        "username": "maik",
        "device": "iphone",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestLocation:
    def test_valid_location(self):
        loc = Location(
            device_id="maik/iphone",
            latitude=50.978,
            longitude=11.029,
            accuracy_m=15.0,
            altitude_m=200.0,
            battery_percent=85,
            connection_type="w",
            trigger="p",
            observed_at=datetime.now(timezone.utc),
            received_at=datetime.now(timezone.utc),
        )
        assert loc.source == "owntracks"
        assert loc.age_seconds >= 0

    def test_invalid_latitude(self):
        with pytest.raises(ValueError):
            Location(
                device_id="maik/iphone",
                latitude=100.0,
                longitude=0.0,
                accuracy_m=None,
                altitude_m=None,
                battery_percent=None,
                connection_type=None,
                trigger=None,
                observed_at=datetime.now(timezone.utc),
                received_at=datetime.now(timezone.utc),
            )

    def test_invalid_battery(self):
        with pytest.raises(ValueError):
            Location(
                device_id="maik/iphone",
                latitude=0.0,
                longitude=0.0,
                accuracy_m=None,
                altitude_m=None,
                battery_percent=150,
                connection_type=None,
                trigger=None,
                observed_at=datetime.now(timezone.utc),
                received_at=datetime.now(timezone.utc),
            )

    def test_invalid_connection(self):
        with pytest.raises(ValueError):
            Location(
                device_id="maik/iphone",
                latitude=0.0,
                longitude=0.0,
                accuracy_m=None,
                altitude_m=None,
                battery_percent=None,
                connection_type="x",
                trigger=None,
                observed_at=datetime.now(timezone.utc),
                received_at=datetime.now(timezone.utc),
            )


class TestLocationStore:
    def test_empty_store(self):
        store = LocationStore(db_path=tempfile.mktemp(suffix=".db"))
        assert store.current is None
        assert store.is_stale
        assert store.age_seconds is None

    def test_update_and_retrieve(self):
        store = LocationStore(db_path=tempfile.mktemp(suffix=".db"))
        loc = Location(
            device_id="maik/iphone",
            latitude=50.978,
            longitude=11.029,
            accuracy_m=15.0,
            altitude_m=None,
            battery_percent=85,
            connection_type="w",
            trigger="p",
            observed_at=datetime.now(timezone.utc),
            received_at=datetime.now(timezone.utc),
        )
        store.update(loc)
        assert store.current is not None
        assert not store.is_stale
        assert store.age_seconds is not None

    def test_stale_detection(self):
        store = LocationStore(stale_seconds=1)
        loc = Location(
            device_id="maik/iphone",
            latitude=50.978,
            longitude=11.029,
            accuracy_m=15.0,
            altitude_m=None,
            battery_percent=85,
            connection_type="w",
            trigger="p",
            observed_at=datetime.now(timezone.utc),
            received_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
        store.update(loc)
        assert store.is_stale

    def test_to_dict_empty(self):
        store = LocationStore(db_path=tempfile.mktemp(suffix=".db"))
        assert store.to_dict() is None

    def test_to_dict_populated(self):
        store = LocationStore(db_path=tempfile.mktemp(suffix=".db"))
        loc = Location(
            device_id="maik/iphone",
            latitude=50.978,
            longitude=11.029,
            accuracy_m=15.0,
            altitude_m=200.0,
            battery_percent=85,
            connection_type="w",
            trigger="p",
            observed_at=datetime.now(timezone.utc),
            received_at=datetime.now(timezone.utc),
        )
        store.update(loc)
        data = store.to_dict()
        assert data is not None
        assert data["device_id"] == "maik/iphone"
        assert data["latitude"] == 50.978
        assert data["source"] == "owntracks"
        assert "age_seconds" in data
        assert "stale" in data


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_location(self):
        loc = normalize_location(loc_payload())
        assert loc.device_id == "maik/iphone"
        assert loc.latitude == 50.978
        assert loc.accuracy_m == 15.0
        assert loc.battery_percent == 85

    def test_valid_location_minimal_fields(self):
        loc = normalize_location({
            "_type": "location",
            "lat": 48.0,
            "lon": 10.0,
            "tst": NOW,
        })
        assert loc.latitude == 48.0
        assert loc.accuracy_m is None
        assert loc.battery_percent is None

    def test_invalid_latitude_too_high(self):
        with pytest.raises(PayloadError, match="lat"):
            normalize_location(loc_payload(lat=91.0))

    def test_invalid_latitude_too_low(self):
        with pytest.raises(PayloadError, match="lat"):
            normalize_location(loc_payload(lat=-91.0))

    def test_invalid_longitude(self):
        with pytest.raises(PayloadError, match="lon"):
            normalize_location(loc_payload(lon=181.0))

    def test_nan_latitude(self):
        with pytest.raises(PayloadError):
            normalize_location(loc_payload(lat=float("nan")))

    def test_inf_latitude(self):
        with pytest.raises(PayloadError):
            normalize_location(loc_payload(lat=float("inf")))

    def test_invalid_timestamp_zero(self):
        with pytest.raises(PayloadError, match="tst"):
            normalize_location(loc_payload(tst=0))

    def test_invalid_timestamp_negative(self):
        with pytest.raises(PayloadError, match="tst"):
            normalize_location(loc_payload(tst=-1))

    def test_invalid_timestamp_future(self):
        with pytest.raises(PayloadError, match="tst"):
            normalize_location(loc_payload(tst=NOW + 3600))

    def test_invalid_battery(self):
        with pytest.raises(PayloadError, match="batt"):
            normalize_location(loc_payload(batt=150))

    def test_invalid_accuracy_negative(self):
        with pytest.raises(PayloadError, match="acc"):
            normalize_location(loc_payload(acc=-1.0))

    def test_invalid_connection_type(self):
        with pytest.raises(PayloadError, match="conn"):
            normalize_location(loc_payload(conn="x"))

    def test_wrong_type(self):
        with pytest.raises(PayloadError, match="_type"):
            normalize_location({"_type": "waypoint", "lat": 50.0, "lon": 10.0, "tst": NOW})

    def test_missing_lat(self):
        with pytest.raises(PayloadError, match="lat"):
            normalize_location({"_type": "location", "lon": 10.0, "tst": NOW})

    def test_bool_are_not_numbers(self):
        with pytest.raises(PayloadError, match="lat"):
            normalize_location(loc_payload(lat=True))

    def test_device_id_fallback(self):
        loc = normalize_location({"_type": "location", "lat": 48.0, "lon": 10.0, "tst": NOW})
        assert loc.device_id == "unknown"

    def test_device_id_from_device_id_field(self):
        loc = normalize_location(loc_payload(device_id="custom/phone"))
        assert loc.device_id == "custom/phone"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class TestEndpoints:
    def _fresh_store(self):
        import owntracks_receiver
        owntracks_receiver._store = LocationStore(db_path=tempfile.mktemp(suffix=".db"))
        owntracks_receiver._rate_limiter = __import__("owntracks_receiver").TokenBucket(rate=10)

    def test_valid_location_payload(self):
        self._fresh_store()
        resp = client.post("/owntracks", json=loc_payload(), headers=AUTH_HEADER)
        assert resp.status_code == 200

    def test_valid_location_basic_auth(self):
        import base64
        self._fresh_store()
        creds = base64.b64encode(f"maik:{API_KEY}".encode()).decode()
        resp = client.post(
            "/owntracks",
            json=loc_payload(),
            headers={"Authorization": f"Basic {creds}"},
        )
        assert resp.status_code == 200

    def test_missing_auth(self):
        self._fresh_store()
        resp = client.post("/owntracks", json=loc_payload())
        assert resp.status_code == 401

    def test_invalid_auth(self):
        self._fresh_store()
        resp = client.post("/owntracks", json=loc_payload(), headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_malformed_auth_header(self):
        self._fresh_store()
        resp = client.post("/owntracks", json=loc_payload(), headers={"Authorization": "Basic xyz"})
        assert resp.status_code == 401

    def test_invalid_coordinates(self):
        self._fresh_store()
        resp = client.post("/owntracks", json=loc_payload(lat=100.0), headers=AUTH_HEADER)
        assert resp.status_code == 400

    def test_invalid_timestamp(self):
        self._fresh_store()
        resp = client.post("/owntracks", json=loc_payload(tst=0), headers=AUTH_HEADER)
        assert resp.status_code == 400

    def test_invalid_json(self):
        self._fresh_store()
        resp = client.post("/owntracks", content=b"not json", headers=AUTH_HEADER)
        assert resp.status_code == 400

    def test_unsupported_type(self):
        self._fresh_store()
        resp = client.post("/owntracks", json={"_type": "waypoint", "tst": NOW}, headers=AUTH_HEADER)
        assert resp.status_code == 400

    def test_transition_accepted(self):
        self._fresh_store()
        resp = client.post("/owntracks", json={
            "_type": "transition",
            "tst": NOW,
            "event": "enter",
        }, headers=AUTH_HEADER)
        assert resp.status_code == 200

    def test_oversized_request(self):
        self._fresh_store()
        huge = loc_payload()
        huge["_padding"] = "x" * 5000
        resp = client.post("/owntracks", json=huge, headers=AUTH_HEADER)
        assert resp.status_code == 413

    def test_invalid_battery_too_high(self):
        self._fresh_store()
        resp = client.post("/owntracks", json=loc_payload(batt=101), headers=AUTH_HEADER)
        assert resp.status_code == 400

    def test_unknown_extra_fields_ignored(self):
        self._fresh_store()
        resp = client.post("/owntracks", json=loc_payload(magheading=45.0, extra="stuff"), headers=AUTH_HEADER)
        assert resp.status_code == 200

    def test_health_ok(self):
        self._fresh_store()
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_get_location(self):
        self._fresh_store()
        # Seed a location
        client.post("/owntracks", json=loc_payload(), headers=AUTH_HEADER)
        resp = client.get("/location")
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "ok"
        assert data["latitude"] == 50.978
        assert data["source"] == "owntracks"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_rate_limit_exceeded(self):
        import owntracks_receiver
        owntracks_receiver._rate_limiter = __import__("owntracks_receiver").TokenBucket(rate=3)

        for i in range(3):
            resp = client.post("/owntracks", json=loc_payload(), headers=AUTH_HEADER)
            assert resp.status_code == 200, f"Request {i+1} should pass"

        resp = client.post("/owntracks", json=loc_payload(), headers=AUTH_HEADER)
        assert resp.status_code == 429

    def test_rate_limit_does_not_affect_health(self):
        import owntracks_receiver
        owntracks_receiver._rate_limiter = __import__("owntracks_receiver").TokenBucket(rate=1)

        client.post("/owntracks", json=loc_payload(), headers=AUTH_HEADER)

        resp = client.get("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Integration: end-to-end receive and query
# ---------------------------------------------------------------------------


class TestIntegration:
    def _fresh_store(self):
        import owntracks_receiver
        owntracks_receiver._store = LocationStore(db_path=tempfile.mktemp(suffix=".db"))
        owntracks_receiver._rate_limiter = __import__("owntracks_receiver").TokenBucket(rate=10)

    def test_receive_then_query(self):
        self._fresh_store()
        resp = client.post(
            "/owntracks",
            json=loc_payload(lat=48.1, lon=16.3, acc=10.0, batt=50),
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200

        resp = client.get("/location")
        data = resp.json()
        assert data["result"] == "ok"
        assert data["latitude"] == 48.1
        assert data["longitude"] == 16.3
        assert data["accuracy_m"] == 10.0
        assert data["battery_percent"] == 50
        assert data["stale"] is False

    def test_receive_overwrites_previous(self):
        self._fresh_store()
        client.post("/owntracks", json=loc_payload(lat=48.0, lon=10.0), headers=AUTH_HEADER)
        client.post("/owntracks", json=loc_payload(lat=49.0, lon=11.0), headers=AUTH_HEADER)

        data = client.get("/location").json()
        assert data["latitude"] == 49.0
        assert data["longitude"] == 11.0

    def test_empty_store_returns_not_found(self):
        self._fresh_store()
        resp = client.get("/location")
        assert resp.json()["result"] == "not_found"