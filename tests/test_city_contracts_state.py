from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest


def test_guide_request_defaults_and_coordinates(city_contracts_module) -> None:
    request = city_contracts_module.GuideRequest.from_mapping({})

    assert request.duration_minutes == 90
    assert request.round_trip is True
    assert request.language == "de"
    assert request.fallback_language == "en"
    assert request.max_stops == 8
    assert "local_life" in request.interests

    point_to_point = city_contracts_module.GuideRequest.from_mapping(
        {
            "duration_minutes": 60,
            "start": [48.1, 11.5],
            "destination": [48.2, 11.6],
            "round_trip": False,
            "interests": ["history"],
            "max_stops": 4,
        }
    )
    assert point_to_point.start == (48.1, 11.5)
    assert point_to_point.destination == (48.2, 11.6)


@pytest.mark.parametrize(
    "value",
    [
        {"duration_minutes": 29},
        {"duration_minutes": 241},
        {"interests": []},
        {"interests": "history"},
        {"language": "../de"},
        {"max_stops": 2},
        {"start": [91, 10]},
        {"round_trip": False},
        {"round_trip": "false"},
    ],
)
def test_guide_request_rejects_invalid_values(
    city_contracts_module,
    value: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        city_contracts_module.GuideRequest.from_mapping(value)


def test_knowledge_and_decision_contracts(city_contracts_module) -> None:
    fact = city_contracts_module.KnowledgeFact(
        text="Belegter Fakt.",
        source="Wikipedia",
        source_url="https://de.wikipedia.org/example",
        language="de",
        observed_at=1.0,
    )
    assert fact.confidence == 0.8

    with pytest.raises(ValueError):
        city_contracts_module.KnowledgeFact(
            text="Fakt",
            source="Quelle",
            source_url="http://unsafe.example",
            language="de",
            observed_at=1.0,
        )
    with pytest.raises(ValueError):
        city_contracts_module.GuideDecision(silent=True, text="unerlaubt")
    with pytest.raises(ValueError):
        city_contracts_module.GuideDecision(silent=False, title="unvollständig")


def test_city_state_is_private_and_round_trips(
    city_state_module,
    tmp_path: Path,
) -> None:
    path = tmp_path / "private" / "city_guide_state.json"
    repository = city_state_module.StateRepository(path)
    state = repository.load()
    state["session"].update(
        {
            "id": "session-1",
            "status": "active",
            "started_at": 1.0,
            "expires_at": 5_000.0,
        }
    )
    state["itinerary"]["status"] = "planning"
    repository.save(state)

    assert repository.load()["session"]["id"] == "session-1"
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.with_suffix(".json.lock")).st_mode & 0o777 == 0o600


def test_city_state_quarantines_invalid_json(
    city_state_module,
    tmp_path: Path,
) -> None:
    path = tmp_path / "city_guide_state.json"
    path.write_text("{broken", encoding="utf-8")
    repository = city_state_module.StateRepository(path)

    with pytest.raises(city_state_module.CorruptStateError):
        repository.load()

    quarantined = list(tmp_path.glob("city_guide_state.json.corrupt-*"))
    assert len(quarantined) == 1
    assert not path.exists()


def test_state_repository_rejects_symbolic_links(
    city_state_module,
    tmp_path: Path,
) -> None:
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    linked_state = tmp_path / "linked-state.json"
    linked_state.symlink_to(target)
    with pytest.raises(OSError, match="symbolic link"):
        city_state_module.StateRepository(linked_state).load()

    real_directory = tmp_path / "real"
    real_directory.mkdir()
    linked_directory = tmp_path / "linked-directory"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(OSError, match="symbolic link"):
        city_state_module.StateRepository(
            linked_directory / "state.json"
        ).load()


def test_city_state_rejects_invalid_route_point(
    city_state_module,
    tmp_path: Path,
) -> None:
    state = city_state_module.empty_state()
    state["itinerary"]["route_points"] = [[999, 11]]

    with pytest.raises(ValueError):
        city_state_module.StateRepository(tmp_path / "state.json").save(state)


def test_state_contains_no_credentials(city_state_module) -> None:
    serialized = json.dumps(city_state_module.empty_state())

    assert "api_key" not in serialized
    assert "telegram_chat" not in serialized


def test_city_state_validation_rejects_malformed_sections(city_state_module) -> None:
    base = city_state_module.empty_state()
    mutations = [
        lambda state: state.update(schema_version=999),
        lambda state: state.update(session=None),
        lambda state: state["session"].update(status="active", id=None),
        lambda state: state.update(preferences=None),
        lambda state: state["preferences"].update(duration_minutes=5),
        lambda state: state["preferences"].update(interests="history"),
        lambda state: state["itinerary"].update(status="unknown"),
        lambda state: state["itinerary"].update(route_points=None),
        lambda state: state["itinerary"].update(stops=["invalid"]),
        lambda state: state.update(position="invalid"),
        lambda state: state.update(position={"lat": 91, "lon": 11}),
        lambda state: state.update(position={"lat": 48, "lon": 181}),
        lambda state: state.update(schedule=None),
        lambda state: state["schedule"].update(operational_errors=[]),
        lambda state: state.update(stories=[]),
        lambda state: state.update(provider_health=[]),
    ]

    for mutate in mutations:
        candidate = deepcopy(base)
        mutate(candidate)
        with pytest.raises(ValueError):
            city_state_module.validate_state(candidate)

    assert city_state_module.migrate_state(None)["schema_version"] == 1
    with pytest.raises(ValueError):
        city_state_module.migrate_state({"schema_version": 99})
