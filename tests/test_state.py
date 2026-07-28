from __future__ import annotations

import json


def test_location_sample_validates_and_hashes_session(contracts_module) -> None:
    sample = contracts_module.LocationSample.from_mapping(
        {
            "chat_id": "secret-chat",
            "message_id": "42",
            "lat": 50.0,
            "lon": 10.0,
            "updated_at": 999.0,
            "expires_at": 2_000.0,
        },
        expected_chat_id="secret-chat",
        now=1_000.0,
        max_age_seconds=300,
    )

    assert sample.session_id.startswith("telegram-")
    assert "secret-chat" not in sample.session_id


def test_stale_location_is_rejected(contracts_module) -> None:
    try:
        contracts_module.LocationSample.from_mapping(
            {
                "chat_id": "chat",
                "message_id": "42",
                "lat": 50.0,
                "lon": 10.0,
                "updated_at": 100.0,
                "expires_at": 2_000.0,
            },
            expected_chat_id="chat",
            now=1_000.0,
            max_age_seconds=300,
        )
    except ValueError as error:
        assert "stale" in str(error)
    else:
        raise AssertionError("expected stale location rejection")


def test_gate_decision_never_serializes_coordinates(contracts_module) -> None:
    decision = contracts_module.GateDecision(
        wake_agent=True,
        session_id="telegram-abc",
        reason="moved",
        cadence_minutes=5,
        flags=("off_route",),
    )

    serialized = json.dumps(decision.to_cron_payload())

    assert "lat" not in serialized
    assert "lon" not in serialized


def test_quiet_gate_decision_contains_only_wake_flag(contracts_module) -> None:
    decision = contracts_module.GateDecision(wake_agent=False)

    assert decision.to_cron_payload() == {"wakeAgent": False}


def test_location_sample_rejects_wrong_chat_and_missing_message(
    contracts_module,
) -> None:
    base = {
        "chat_id": "chat",
        "message_id": "42",
        "lat": 50.0,
        "lon": 10.0,
        "updated_at": 999.0,
        "expires_at": 2_000.0,
    }
    for change, expected in [
        ({"chat_id": "other"}, "different chat"),
        ({"message_id": ""}, "message_id"),
        ({"lat": 999}, "latitude"),
        ({"lon": 999}, "longitude"),
        ({"updated_at": 0}, "observation time"),
        ({"expires_at": 999}, "expired"),
        ({"updated_at": 2_000}, "future"),
    ]:
        value = {**base, **change}
        try:
            contracts_module.LocationSample.from_mapping(
                value,
                expected_chat_id="chat",
                now=1_000.0,
                max_age_seconds=300,
            )
        except ValueError as error:
            assert expected in str(error)
        else:
            raise AssertionError(f"expected rejection for {change}")


def test_provider_and_alert_contracts(contracts_module) -> None:
    success = contracts_module.ProviderResult(
        provider="test",
        capability="weather.current",
        observed_at=1.0,
        data={"temperature": 20},
        confidence=0.9,
    )
    failure = contracts_module.ProviderResult(
        provider="test",
        capability="weather.current",
        observed_at=1.0,
        error_code="offline",
    )

    assert success.ok is True
    assert success.to_dict()["data"] == {"temperature": 20}
    assert failure.ok is False
    try:
        contracts_module.AlertDecision(silent=True, events=({"id": "event"},))
    except ValueError as error:
        assert "silent" in str(error)
    else:
        raise AssertionError("expected invalid alert decision")
    try:
        contracts_module.AlertDecision(
            silent=False,
            events=({"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}),
        )
    except ValueError as error:
        assert "three" in str(error)
    else:
        raise AssertionError("expected safety event limit")
