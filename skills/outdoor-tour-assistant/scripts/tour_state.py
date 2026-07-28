#!/usr/bin/env python3
"""Versioned, private and transaction-safe state for live-tour sessions."""

from __future__ import annotations

import copy
import fcntl
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

SCHEMA_VERSION = 3
ROUTE_MATCH_STATUSES = {"unknown", "matching", "matched", "ambiguous", "unmatched", "failed"}
SESSION_STATUSES = {"inactive", "starting", "matching_route", "active", "ending"}


class CorruptStateError(RuntimeError):
    """Raised after an invalid state file has been moved to private quarantine."""

    def __init__(self, quarantine_path: Path):
        super().__init__(f"state quarantined as {quarantine_path.name}")
        self.quarantine_path = quarantine_path


def empty_schedule() -> dict[str, Any]:
    return {
        "active": False,
        "last_wake_at": None,
        "next_due_at": None,
        "cadence_minutes": 5,
        "last_trigger": None,
        "last_wake_position": None,
        "off_route_active": False,
        "event_last_sent": {},
        "lunch_suggested_on": None,
        "operational_errors": {},
    }


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "session": {
            "id": None,
            "status": "inactive",
            "started_at": None,
            "expires_at": None,
            "ended_at": None,
        },
        "route": {
            "match_status": "unknown",
            "provider": None,
            "id": None,
            "name": None,
            "gpx_path": None,
            "verified": False,
            "prepared": False,
            "settlements": [],
        },
        "position": None,
        "schedule": empty_schedule(),
        "events": {},
        "weather": None,
        "provider_health": {},
    }


def _migrate_v2(raw: dict[str, Any]) -> dict[str, Any]:
    migrated = empty_state()
    migrated["session"].update(raw.get("session") or {})
    migrated["position"] = raw.get("position")
    migrated["events"] = copy.deepcopy(raw.get("events") or {})
    migrated["weather"] = copy.deepcopy(raw.get("weather"))
    migrated["provider_health"] = copy.deepcopy(raw.get("provider_health") or {})

    old_route = raw.get("route") or {}
    verified = bool(old_route.get("verified"))
    gpx_path = old_route.get("gpx_path")
    migrated["route"].update(
        {
            "match_status": "matched" if verified and gpx_path else old_route.get(
                "match_status", "unknown"
            ),
            "provider": old_route.get("provider"),
            "id": old_route.get("id"),
            "name": old_route.get("name"),
            "gpx_path": gpx_path,
            "verified": bool(verified and gpx_path),
            "prepared": bool(
                verified
                and gpx_path
                and migrated["session"].get("status") == "inactive"
            ),
        }
    )
    if verified and not gpx_path:
        migrated["route"]["match_status"] = "failed"
    return migrated


def _migrate_legacy(raw: dict[str, Any]) -> dict[str, Any]:
    migrated = empty_state()
    old_tour = raw.get("tour") or {}
    gpx_path = old_tour.get("gpx")
    verified = bool(old_tour.get("verified") and gpx_path)
    session_id = raw.get("share_message_id")
    if session_id is not None:
        migrated["session"].update(
            {
                "id": str(session_id),
                "status": "active" if verified else "matching_route",
                "started_at": raw.get("started_at"),
            }
        )
    migrated["route"].update(
        {
            "match_status": "matched" if verified else "unknown",
            "id": old_tour.get("id"),
            "name": old_tour.get("name"),
            "gpx_path": gpx_path,
            "verified": verified,
            "prepared": verified,
        }
    )
    migrated["position"] = copy.deepcopy(raw.get("last_position"))
    migrated["weather"] = copy.deepcopy(raw.get("weather"))
    migrated["events"] = {
        str(key): {
            "event_id": str(key),
            "event_type": "legacy",
            "status": "resolved",
            "confidence": 1.0,
        }
        for key in raw.get("reported_facts", [])
    }
    return migrated


def migrate_state(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return empty_state()
    version = raw.get("schema_version")
    if version == SCHEMA_VERSION:
        migrated = copy.deepcopy(raw)
    elif version == 2:
        migrated = _migrate_v2(raw)
    elif version in {None, 1}:
        migrated = _migrate_legacy(raw)
    else:
        raise ValueError("unsupported state schema")
    validate_state(migrated)
    return migrated


def _valid_coordinate(value: Any, minimum: float, maximum: float) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and minimum <= numeric <= maximum


def validate_state(state: dict[str, Any]) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported state schema")
    session = state.get("session") or {}
    route = state.get("route") or {}
    schedule = state.get("schedule") or {}
    if session.get("status") not in SESSION_STATUSES:
        raise ValueError("invalid session status")
    if session.get("status") != "inactive" and not session.get("id"):
        raise ValueError("active session requires an id")
    if route.get("match_status") not in ROUTE_MATCH_STATUSES:
        raise ValueError("invalid route match status")
    if bool(route.get("verified")) != (route.get("match_status") == "matched"):
        raise ValueError("verified route must have match_status=matched")
    if route.get("verified") and not route.get("gpx_path"):
        raise ValueError("verified route requires a validated GPX path")
    if route.get("prepared") and not route.get("verified"):
        raise ValueError("prepared route must be verified")
    if not isinstance(route.get("settlements", []), list):
        raise ValueError("route settlements must be a list")
    if not isinstance(schedule.get("event_last_sent", {}), dict):
        raise ValueError("event_last_sent must be an object")
    if not isinstance(schedule.get("operational_errors", {}), dict):
        raise ValueError("operational_errors must be an object")
    position = state.get("position")
    if position is not None:
        if not _valid_coordinate(position.get("lat"), -90, 90):
            raise ValueError("invalid position latitude")
        if not _valid_coordinate(position.get("lon"), -180, 180):
            raise ValueError("invalid position longitude")


def start_session(
    state: dict[str, Any],
    session_id: str,
    *,
    started_at: float | None = None,
    expires_at: float | None = None,
) -> dict[str, Any]:
    if not session_id:
        raise ValueError("session id is required")
    current = migrate_state(state)
    if current["session"].get("id") == session_id and current["session"].get("status") != "inactive":
        current["session"]["expires_at"] = expires_at
        return current

    prepared_route = (
        copy.deepcopy(current["route"])
        if current["route"].get("prepared") and current["route"].get("verified")
        else None
    )
    fresh = empty_state()
    fresh["session"] = {
        "id": session_id,
        "status": "active" if prepared_route else "starting",
        "started_at": time.time() if started_at is None else started_at,
        "expires_at": expires_at,
        "ended_at": None,
    }
    if prepared_route:
        prepared_route["prepared"] = False
        fresh["route"] = prepared_route
    return fresh


def set_route_match(
    state: dict[str, Any],
    status: str,
    *,
    provider: str | None = None,
    route_id: str | int | None = None,
    name: str | None = None,
    gpx_path: str | None = None,
    prepared: bool = False,
) -> dict[str, Any]:
    if status not in ROUTE_MATCH_STATUSES:
        raise ValueError("invalid route match status")
    if status == "matched" and not gpx_path:
        raise ValueError("matched route requires a validated GPX path")
    current = migrate_state(state)
    settlements = current["route"].get("settlements", [])
    current["route"] = {
        "match_status": status,
        "provider": provider,
        "id": route_id,
        "name": name,
        "gpx_path": gpx_path,
        "verified": status == "matched",
        "prepared": bool(prepared and status == "matched"),
        "settlements": settlements if status == "matched" else [],
    }
    if prepared:
        current["session"]["status"] = "inactive"
    else:
        current["session"]["status"] = "active" if status == "matched" else "matching_route"
    validate_state(current)
    return current


def end_session(state: dict[str, Any], *, ended_at: float | None = None) -> dict[str, Any]:
    current = migrate_state(state)
    current["session"]["status"] = "inactive"
    current["session"]["ended_at"] = time.time() if ended_at is None else ended_at
    current["route"] = empty_state()["route"]
    current["position"] = None
    current["schedule"] = empty_schedule()
    current["events"] = {}
    current["weather"] = None
    return current


class StateRepository:
    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def _ensure_private_directory(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_private_directory()
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.chmod(self.lock_path, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _quarantine_unlocked(self) -> Path:
        quarantine = self.path.with_name(
            f"{self.path.name}.corrupt-{int(time.time())}-{os.getpid()}"
        )
        os.replace(self.path, quarantine)
        os.chmod(quarantine, 0o600)
        return quarantine

    def _load_unlocked(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return empty_state()
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            quarantine = self._quarantine_unlocked()
            raise CorruptStateError(quarantine) from error
        if not isinstance(raw, dict):
            quarantine = self._quarantine_unlocked()
            raise CorruptStateError(quarantine)
        try:
            return migrate_state(raw)
        except (TypeError, ValueError) as error:
            quarantine = self._quarantine_unlocked()
            raise CorruptStateError(quarantine) from error

    def _save_unlocked(self, state: dict[str, Any]) -> None:
        validate_state(state)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def load(self) -> dict[str, Any]:
        with self._locked():
            return self._load_unlocked()

    def save(self, state: dict[str, Any]) -> None:
        with self._locked():
            self._save_unlocked(migrate_state(state))

    def update(
        self,
        operation: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        with self._locked():
            state = self._load_unlocked()
            updated = operation(copy.deepcopy(state))
            if updated is None:
                updated = state
            updated = migrate_state(updated)
            self._save_unlocked(updated)
            return copy.deepcopy(updated)

    def recover_empty(self) -> dict[str, Any]:
        recovered = empty_state()
        with self._locked():
            self._save_unlocked(recovered)
        return recovered
