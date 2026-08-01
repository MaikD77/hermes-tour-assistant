"""Single sanitized CalendarContext snapshot; no raw event history or credentials."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .calendar_context import CalendarContext
from .repository import JsonStateRepository

SCHEMA_VERSION = 1


def _empty() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "last_calendar_context": None}


def _validate(raw: dict[str, Any]) -> None:
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid calendar state schema")
    serialized = json.dumps(raw).lower()
    forbidden = ("access_token", "refresh_token", "attendees", "hangoutlink",
                 "conferencedata", "raw_payload", "http://", "https://")
    if any(value in serialized for value in forbidden):
        raise ValueError("calendar state contains forbidden sensitive data")


def export_calendar_context(context: CalendarContext) -> dict[str, Any]:
    def default(value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        raise TypeError(type(value).__name__)
    return json.loads(json.dumps(asdict(context), default=default))


class CalendarStateRepository:
    def __init__(self, directory: Path) -> None:
        self.repository = JsonStateRepository(directory / "calendar-state.json",
            empty_factory=_empty, migrate=lambda value: value, validate=_validate)

    def load(self) -> dict[str, Any] | None:
        return self.repository.load()["last_calendar_context"]

    def save(self, context: CalendarContext) -> None:
        self.repository.save({"schema_version": SCHEMA_VERSION,
                              "last_calendar_context": export_calendar_context(context)})

    def reset(self) -> None:
        self.repository.save(_empty())
