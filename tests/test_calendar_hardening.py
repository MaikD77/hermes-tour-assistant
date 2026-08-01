from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import calendarctl
import contextctl
import pytest
from location_core.calendar_context import (
    CalendarContextConfig,
    CalendarContextEngine,
    CalendarContextStatus,
)
from location_core.calendar_contracts import (
    CalendarAvailable,
    CalendarEvent,
    CalendarInvalid,
    CalendarPartial,
    CalendarRateLimited,
    CalendarUnauthorized,
    EventStatus,
    Transparency,
    UserResponse,
    Visibility,
)
from location_core.calendar_factory import build_calendar_provider
from location_core.calendar_providers import CalendarProviderConfig, GoogleCalendarProvider
from location_core.calendar_state import export_calendar_context
from location_core.context import ContextConfig, CurrentContextEngine
from location_core.context_state import migrate_state

NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)


def event(event_id: str = "event") -> CalendarEvent:
    return CalendarEvent(event_id, "replay", "primary", "Synthetic", "", "",
        NOW, NOW + timedelta(hours=1), False, EventStatus.CONFIRMED,
        Visibility.DEFAULT, Transparency.BUSY, False, 0, UserResponse.ACCEPTED)


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list_events(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"items": []}


def google_env(credential: str, **changes: str) -> dict[str, str]:
    values = {"HERMES_CALENDAR_PROVIDER": "google",
        "HERMES_GOOGLE_CREDENTIALS_FILE": credential,
        "HERMES_GOOGLE_OAUTH_SCOPE": "https://www.googleapis.com/auth/calendar.readonly"}
    values.update(changes)
    return values


def test_factory_builds_google_with_private_credential_and_injected_transport(tmp_path) -> None:
    credential = tmp_path / "credential.json"
    credential.write_text("synthetic-not-a-real-credential")
    credential.chmod(0o600)
    transport = RecordingTransport()
    provider = build_calendar_provider(env=google_env(str(credential)),
        config=CalendarProviderConfig(("work", "family")), transport=transport, clock=lambda: NOW)
    assert isinstance(provider, GoogleCalendarProvider)
    result = provider.list_events(window_start=NOW, window_end=NOW + timedelta(days=1))
    assert isinstance(result, CalendarAvailable)
    assert [call["calendar_id"] for call in transport.calls] == ["work", "family"]
    assert all(call["single_events"] is True and call["order_by"] == "startTime"
               for call in transport.calls)


@pytest.mark.parametrize(("setup", "reason"), [
    ("missing", "unavailable"), ("unsafe", "permissions"), ("scope", "scope")])
def test_factory_rejects_invalid_google_configuration(tmp_path, setup: str, reason: str) -> None:
    credential = tmp_path / "credential.json"
    path = str(credential)
    env = google_env(path)
    if setup != "missing":
        credential.write_text("synthetic")
        credential.chmod(0o644 if setup == "unsafe" else 0o600)
    if setup == "scope":
        env["HERMES_GOOGLE_OAUTH_SCOPE"] = "https://www.googleapis.com/auth/calendar"
    result = build_calendar_provider(env=env, config=CalendarProviderConfig(("primary",)),
        transport=RecordingTransport(), clock=lambda: NOW).list_events(
            window_start=NOW, window_end=NOW + timedelta(hours=1))
    assert isinstance(result, CalendarInvalid)
    assert reason in result.reason.lower()


def test_google_include_exclude_applies_to_explicit_ids(tmp_path) -> None:
    credential = tmp_path / "credential.json"
    credential.write_text("synthetic")
    credential.chmod(0o600)
    transport = RecordingTransport()
    provider = build_calendar_provider(env=google_env(str(credential)),
        config=CalendarProviderConfig(("work", "family", "ignored"),
            include=("work", "family"), exclude=("family",)), transport=transport, clock=lambda: NOW)
    provider.list_events(window_start=NOW, window_end=NOW + timedelta(hours=1))
    assert [call["calendar_id"] for call in transport.calls] == ["work"]


@pytest.mark.parametrize(("status", "expected"), [(401, CalendarUnauthorized),
                                                   (429, CalendarRateLimited)])
def test_google_http_errors_are_typed(status: int, expected: type) -> None:
    class HttpFailure(Exception):
        def __init__(self) -> None:
            self.resp = SimpleNamespace(status=status)

    class FailingTransport:
        def list_events(self, **kwargs):
            raise HttpFailure()

    result = GoogleCalendarProvider(FailingTransport(), CalendarProviderConfig(("primary",)),
        clock=lambda: NOW).list_events(window_start=NOW, window_end=NOW + timedelta(hours=1))
    assert isinstance(result, expected)


def test_source_metadata_is_detached_sorted_immutable_and_exportable() -> None:
    source = {"sequence": "2", "etag": "one"}
    first = CalendarEvent(**{**event().__dict__, "source_metadata": source})
    source["etag"] = "changed"
    second = CalendarEvent(**{**event().__dict__,
        "source_metadata": {"etag": "one", "sequence": "2"}})
    assert first.source_metadata == (("etag", "one"), ("sequence", "2"))
    assert first == second
    with pytest.raises(TypeError):
        first.source_metadata[0] = ("etag", "changed")  # type: ignore[index]
    context = CalendarContextEngine().compute(provider_result=CalendarAvailable((first,), NOW),
        computed_at=NOW, window_start=NOW, window_end=NOW + timedelta(hours=2))
    assert export_calendar_context(context)["current_events"][0]["source_metadata"] == [
        ["etag", "one"], ["sequence", "2"]]


def test_metadata_rejects_non_allowlisted_key() -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        CalendarEvent(**{**event().__dict__, "source_metadata": (("payload", "no"),)})


@pytest.mark.parametrize(("offset", "status", "code"), [
    (30, CalendarContextStatus.AVAILABLE, None),
    (61, CalendarContextStatus.INVALID, "calendar_clock_skew")])
def test_provider_future_skew(offset: int, status: CalendarContextStatus,
                              code: str | None) -> None:
    context = CalendarContextEngine(CalendarContextConfig(future_skew_seconds=60)).compute(
        provider_result=CalendarAvailable((), NOW + timedelta(seconds=offset)), computed_at=NOW,
        window_start=NOW, window_end=NOW + timedelta(hours=1))
    assert context.status is status
    assert (context.uncertainties[0].code if context.uncertainties else None) == code


def test_naive_fetch_and_invalid_window_return_deterministic_invalid_context() -> None:
    engine = CalendarContextEngine()
    result = CalendarAvailable((), NOW.replace(tzinfo=None))
    first = engine.compute(provider_result=result, computed_at=NOW,
        window_start=NOW, window_end=NOW + timedelta(hours=1))
    second = engine.compute(provider_result=result, computed_at=NOW,
        window_start=NOW, window_end=NOW + timedelta(hours=1))
    assert first.status is CalendarContextStatus.INVALID
    assert first.context_id == second.context_id
    invalid_window = engine.compute(provider_result=CalendarAvailable((), NOW), computed_at=NOW,
        window_start=NOW, window_end=NOW)
    assert invalid_window.status is CalendarContextStatus.INVALID


def test_mixed_invalid_events_becomes_partial_without_crash() -> None:
    invalid = event("invalid")
    object.__setattr__(invalid, "start_at", NOW.replace(tzinfo=None))
    context = CalendarContextEngine().compute(
        provider_result=CalendarAvailable((event("valid"), invalid), NOW), computed_at=NOW,
        window_start=NOW - timedelta(hours=1), window_end=NOW + timedelta(hours=2))
    assert context.status is CalendarContextStatus.PARTIAL
    assert {item.event_id for item in context.current_events} == {"valid"}
    assert "event_time_invalid" in {item.code for item in context.uncertainties}


def test_result_reason_and_retry_validation() -> None:
    assert "secret" not in CalendarInvalid(NOW, "https://host?token=secret").reason
    assert len(CalendarInvalid(NOW, "x" * 500).reason) == 160
    with pytest.raises(ValueError, match="negative"):
        CalendarRateLimited(NOW, -1)


def test_calendarctl_fetch_uses_central_factory(monkeypatch, tmp_path, capsys) -> None:
    calls: list[str] = []

    class Provider:
        def list_events(self, **kwargs):
            calls.append("called")
            return CalendarAvailable((), NOW)

    monkeypatch.setenv("HERMES_CALENDAR_IDS", "primary")
    monkeypatch.setenv("HERMES_CALENDAR_PROVIDER", "google")
    monkeypatch.setenv("HERMES_CALENDAR_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(calendarctl, "build_calendar_provider", lambda **kwargs: Provider())
    monkeypatch.setattr(calendarctl, "datetime", SimpleNamespace(now=lambda zone: NOW))
    assert calendarctl.main(["fetch"]) == 0
    assert calls == ["called"]
    assert json.loads(capsys.readouterr().out)["status"] == "available"


def test_context_compute_uses_factory_only_when_enabled(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    class Provider:
        def list_events(self, **kwargs):
            calls.append("called")
            return CalendarAvailable((), NOW)

    loader = SimpleNamespace(load=lambda **kwargs: SimpleNamespace(observation=None,
        movement_state=None, place_state=None, profile_state=None, issues=()))
    repository = SimpleNamespace(save=lambda context: None)
    config = ContextConfig(timezone="UTC", state_dir=tmp_path)
    monkeypatch.setattr(contextctl, "build_calendar_provider", lambda **kwargs: Provider())
    monkeypatch.setenv("HERMES_CALENDAR_IDS", "primary")
    monkeypatch.setenv("HERMES_CONTEXT_CALENDAR_ENABLED", "false")
    disabled = contextctl.compute_context(config, now=NOW, loader=loader, repository=repository)
    assert calls == [] and disabled["calendar_context"] is None
    monkeypatch.setenv("HERMES_CONTEXT_CALENDAR_ENABLED", "true")
    enabled = contextctl.compute_context(config, now=NOW, loader=loader, repository=repository)
    assert calls == ["called"] and enabled["calendar_context"]["status"] == "available"


def test_current_context_only_imports_usable_calendar_traits() -> None:
    active = event()
    available = CalendarContextEngine().compute(provider_result=CalendarAvailable((active,), NOW),
        computed_at=NOW, window_start=NOW, window_end=NOW + timedelta(hours=2))
    stale = CalendarContextEngine().compute(
        provider_result=CalendarAvailable((active,), NOW - timedelta(hours=1)),
        computed_at=NOW, window_start=NOW, window_end=NOW + timedelta(hours=2))
    available_context = CurrentContextEngine().compute(computed_at=NOW,
                                                       calendar_context=available).context
    stale_context = CurrentContextEngine().compute(computed_at=NOW, calendar_context=stale).context
    assert "calendar_event_active" in {trait.trait_type for trait in available_context.traits}
    assert "calendar_event_active" not in {trait.trait_type for trait in stale_context.traits}
    assert available_context.context_id != stale_context.context_id


@pytest.mark.parametrize("calendar", [
    CalendarContextEngine().compute(provider_result=CalendarPartial((event(),), NOW, False),
        computed_at=NOW, window_start=NOW, window_end=NOW + timedelta(hours=2)),
    CalendarContextEngine().compute(provider_result=CalendarAvailable((event(),),
        NOW - timedelta(minutes=20)), computed_at=NOW, window_start=NOW,
        window_end=NOW + timedelta(hours=2)),
    CalendarContextEngine().compute(provider_result=CalendarAvailable((event(),),
        NOW - timedelta(hours=2)), computed_at=NOW, window_start=NOW,
        window_end=NOW + timedelta(hours=2)),
    CalendarContextEngine().compute(provider_result=CalendarUnauthorized(NOW),
        computed_at=NOW, window_start=NOW, window_end=NOW + timedelta(hours=2)),
    CalendarContextEngine().compute(provider_result=CalendarInvalid(NOW),
        computed_at=NOW, window_start=NOW, window_end=NOW + timedelta(hours=2)),
])
def test_calendar_subcontext_status_is_preserved_without_base_confidence_change(calendar) -> None:
    base = CurrentContextEngine().compute(computed_at=NOW).context
    combined = CurrentContextEngine().compute(computed_at=NOW, calendar_context=calendar).context
    assert combined.calendar_context is calendar
    assert combined.overall_confidence == base.overall_confidence


def test_context_schema_one_migrates_calendar_to_none() -> None:
    migrated = migrate_state({"schema_version": 1, "last_context": {"context_id": "old"}})
    assert migrated == {"schema_version": 2,
                        "last_context": {"context_id": "old", "calendar_context": None}}
