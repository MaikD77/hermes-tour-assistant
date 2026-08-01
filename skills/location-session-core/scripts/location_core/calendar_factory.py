"""Central construction of productive read-only calendar providers."""

from __future__ import annotations

import importlib
import importlib.util
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .calendar_context import CalendarContextConfig
from .calendar_contracts import CalendarEvent, CalendarInvalid, CalendarProviderResult
from .calendar_providers import (
    GOOGLE_READ_ONLY_SCOPE,
    CalendarProviderConfig,
    GoogleCalendarProvider,
    GoogleTransport,
    ReplayCalendarProvider,
)


def _integer(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, default))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def provider_config_from_env(env: Mapping[str, str] = os.environ) -> CalendarProviderConfig:
    """Load every provider option from one productive configuration path."""
    ids = tuple(value.strip() for value in env.get("HERMES_CALENDAR_IDS", "").split(",")
                if value.strip())
    private = env.get("HERMES_CALENDAR_PRIVATE_SANITIZATION", "true").lower()
    if private not in ("true", "false"):
        raise ValueError("HERMES_CALENDAR_PRIVATE_SANITIZATION must be true or false")
    return CalendarProviderConfig(ids,
        tuple(value for value in env.get("HERMES_CALENDAR_INCLUDE", "").split(",") if value),
        tuple(value for value in env.get("HERMES_CALENDAR_EXCLUDE", "").split(",") if value),
        _integer(env, "HERMES_CALENDAR_MAX_EVENTS", 250),
        _integer(env, "HERMES_CALENDAR_TITLE_MAX_LENGTH", 120),
        _integer(env, "HERMES_CALENDAR_DESCRIPTION_MAX_LENGTH", 0),
        _integer(env, "HERMES_CALENDAR_LOCATION_MAX_LENGTH", 120), private == "true")


def context_config_from_env(env: Mapping[str, str] = os.environ) -> CalendarContextConfig:
    return CalendarContextConfig(
        _integer(env, "HERMES_CALENDAR_FRESH_SECONDS", 300),
        _integer(env, "HERMES_CALENDAR_AGING_SECONDS", 900),
        _integer(env, "HERMES_CALENDAR_STALE_SECONDS", 3600),
        _integer(env, "HERMES_CALENDAR_LOOKBACK_MINUTES", 120),
        _integer(env, "HERMES_CALENDAR_STARTING_SOON_MINUTES", 15),
        _integer(env, "HERMES_CALENDAR_ENDING_SOON_MINUTES", 10),
        _integer(env, "HERMES_CALENDAR_CONFLICT_BUFFER_MINUTES", 0),
        _integer(env, "HERMES_CALENDAR_UPCOMING_LIMIT", 10),
        _integer(env, "HERMES_CALENDAR_FUTURE_SKEW_SECONDS", 60))


class InvalidCalendarProvider:
    """A typed configuration failure that keeps CurrentContext operational."""

    provider_id = "invalid"

    def __init__(self, reason: str, clock: Callable[[], datetime]) -> None:
        self.reason, self.clock = reason, clock

    def list_events(self, *, window_start: datetime,
                    window_end: datetime) -> CalendarProviderResult:
        return CalendarInvalid(self.clock(), self.reason)


class GoogleApiTransport:
    """Google service-account transport exposing only Calendar v3 events.list."""

    def __init__(self, credential_file: Path) -> None:
        credentials_module = importlib.import_module("google.oauth2.service_account")
        discovery_module = importlib.import_module("googleapiclient.discovery")
        credentials = credentials_module.Credentials.from_service_account_file(
            str(credential_file), scopes=[GOOGLE_READ_ONLY_SCOPE])
        self._service = discovery_module.build(
            "calendar", "v3", credentials=credentials, cache_discovery=False)

    def list_events(self, *, calendar_id: str, time_min: str, time_max: str,
                    page_token: str | None, single_events: bool,
                    order_by: str) -> Mapping[str, Any]:
        request = self._service.events().list(calendarId=calendar_id, timeMin=time_min,
            timeMax=time_max, pageToken=page_token, singleEvents=single_events,
            orderBy=order_by, showDeleted=True)
        response = request.execute()
        return response if isinstance(response, Mapping) else {}


def _credential_problem(path_text: str) -> str | None:
    if not path_text:
        return "Google credential file is not configured"
    path = Path(path_text)
    try:
        details = path.lstat()
    except OSError:
        return "Google credential file is unavailable"
    if not stat.S_ISREG(details.st_mode) or path.is_symlink():
        return "Google credential file must be a regular non-symlink file"
    if details.st_mode & 0o077:
        return "Google credential file permissions must be private"
    return None


def build_calendar_provider(*, env: Mapping[str, str] = os.environ,
                            config: CalendarProviderConfig | None = None,
                            replay_events: Iterable[CalendarEvent] = (),
                            transport: GoogleTransport | None = None,
                            clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
    """Build replay or Google from one validated path; never enumerate calendars."""
    if config is None:
        try:
            config = provider_config_from_env(env)
        except ValueError:
            return InvalidCalendarProvider("calendar provider configuration is invalid", clock)
    provider = env.get("HERMES_CALENDAR_PROVIDER", "replay").strip().lower()
    if provider == "replay":
        return ReplayCalendarProvider(replay_events, fetched_at=clock())
    if provider != "google":
        return InvalidCalendarProvider("calendar provider selection is invalid", clock)
    if env.get("HERMES_GOOGLE_OAUTH_SCOPE", GOOGLE_READ_ONLY_SCOPE) != GOOGLE_READ_ONLY_SCOPE:
        return InvalidCalendarProvider("Google Calendar scope must be calendar.readonly", clock)
    credential_text = env.get("HERMES_GOOGLE_CREDENTIALS_FILE", "")
    problem = _credential_problem(credential_text)
    if problem:
        return InvalidCalendarProvider(problem, clock)
    if transport is None:
        if (importlib.util.find_spec("google.oauth2.service_account") is None or
                importlib.util.find_spec("googleapiclient.discovery") is None):
            return InvalidCalendarProvider("Google Calendar optional dependencies are unavailable", clock)
        try:
            transport = GoogleApiTransport(Path(credential_text))
        except (OSError, ValueError):
            return InvalidCalendarProvider("Google Calendar transport initialization failed", clock)
    return GoogleCalendarProvider(transport, config, clock=clock)
