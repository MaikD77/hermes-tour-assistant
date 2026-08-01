"""Private persistence of one sanitized current-context snapshot (never history)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .context import CurrentContext, CurrentContextEngine
from .repository import JsonStateRepository

SCHEMA_VERSION = 1


def empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "last_context": None}


def migrate_state(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("schema_version", 0) == 0 and not raw.get("last_context"):
        return empty_state()
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported context state schema")
    return raw


def validate_state(raw: dict[str, Any]) -> None:
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid context state schema")
    snapshot = raw.get("last_context")
    if snapshot is not None and not isinstance(snapshot, dict):
        raise ValueError("last context must be an object")
    if isinstance(snapshot, dict) and any(key in str(snapshot).lower()
                                         for key in ("latitude", "longitude", "raw_payload")):
        raise ValueError("context state contains forbidden precise data")


class ContextStateRepository:
    """Atomic, locked, private and symlink-safe last-snapshot repository."""

    def __init__(self, directory: Path):
        self.repository = JsonStateRepository(directory / "context-state.json",
            empty_factory=empty_state, migrate=migrate_state, validate=validate_state)

    def load(self) -> dict[str, Any] | None:
        return self.repository.load().get("last_context")

    def save(self, context: CurrentContext) -> None:
        self.repository.save({"schema_version": SCHEMA_VERSION,
            "last_context": CurrentContextEngine().export(context)})

    def reset(self) -> None:
        self.repository.save(empty_state())
