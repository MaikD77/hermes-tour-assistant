#!/usr/bin/env python3
"""Immutable, normalized location model for OwnTracks receiver."""

from __future__ import annotations

import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Location:
    """Immutable, normalized location record — the only location shape
    this receiver produces. Every field is validated before construction."""

    device_id: str
    latitude: float
    longitude: float
    accuracy_m: float | None
    altitude_m: float | None
    battery_percent: int | None
    connection_type: str | None
    trigger: str | None
    observed_at: datetime
    received_at: datetime
    source: str = "owntracks"

    def __post_init__(self) -> None:
        if not math.isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("latitude out of range")
        if not math.isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("longitude out of range")
        if self.accuracy_m is not None and (not math.isfinite(self.accuracy_m) or self.accuracy_m < 0):
            raise ValueError("accuracy must be non-negative")
        if self.altitude_m is not None and not math.isfinite(self.altitude_m):
            raise ValueError("altitude must be finite")
        if self.battery_percent is not None and not (0 <= self.battery_percent <= 100):
            raise ValueError("battery_percent must be 0–100")
        if self.connection_type is not None and self.connection_type not in ("w", "c", "m", "o"):
            raise ValueError(f"invalid connection_type: {self.connection_type}")
        if self.trigger is not None and self.trigger not in ("b", "c", "i", "p", "r", "u", "t", "s"):
            raise ValueError(f"invalid trigger: {self.trigger}")

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.received_at).total_seconds()

    def is_stale(self, max_age_seconds: int = 300) -> bool:
        return self.age_seconds > max_age_seconds


class LocationStore:
    """In-memory store + SQLite persistence.

    Only the last location is kept in memory for fast queries.
    Every update is also written to SQLite so data survives restarts.
    On init, the last stored location is loaded from the DB.
    """

    def __init__(self, stale_seconds: int = 300, db_path: str | None = None):
        self._location: Location | None = None
        self._stale_seconds = stale_seconds
        self._db_path = db_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "owntracks.db"
        )
        self._init_db()
        self._load_last()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS locations (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id   TEXT    NOT NULL,
                    latitude    REAL    NOT NULL,
                    longitude   REAL    NOT NULL,
                    accuracy_m  REAL,
                    altitude_m  REAL,
                    battery     INTEGER,
                    conn_type   TEXT,
                    trigger     TEXT,
                    observed_at TEXT    NOT NULL,
                    received_at TEXT    NOT NULL,
                    source      TEXT    DEFAULT 'owntracks'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_locations_device_received
                ON locations(device_id, received_at DESC)
            """)

    def _load_last(self) -> None:
        """Load the most recent location from DB (survives restarts)."""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT device_id, latitude, longitude, accuracy_m, altitude_m,
                       battery, conn_type, trigger, observed_at, received_at
                FROM locations
                ORDER BY received_at DESC LIMIT 1
            """).fetchone()
        if row is None:
            return
        try:
            self._location = Location(
                device_id=row[0],
                latitude=row[1],
                longitude=row[2],
                accuracy_m=row[3],
                altitude_m=row[4],
                battery_percent=row[5],
                connection_type=row[6],
                trigger=row[7],
                observed_at=datetime.fromisoformat(row[8]),
                received_at=datetime.fromisoformat(row[9]),
            )
        except (TypeError, ValueError):
            self._location = None

    def update(self, location: Location) -> None:
        self._location = location
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO locations
                   (device_id, latitude, longitude, accuracy_m, altitude_m,
                    battery, conn_type, trigger, observed_at, received_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    location.device_id,
                    location.latitude,
                    location.longitude,
                    location.accuracy_m,
                    location.altitude_m,
                    location.battery_percent,
                    location.connection_type,
                    location.trigger,
                    location.observed_at.isoformat(),
                    location.received_at.isoformat(),
                ),
            )

    def history(self, limit: int = 100) -> list[dict]:
        """Return the last *limit* locations as dicts (newest first)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT device_id, latitude, longitude, accuracy_m, altitude_m,
                          battery, conn_type, trigger, observed_at, received_at
                   FROM locations ORDER BY received_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "device_id": r[0],
                "latitude": r[1],
                "longitude": r[2],
                "accuracy_m": r[3],
                "altitude_m": r[4],
                "battery_percent": r[5],
                "connection_type": r[6],
                "trigger": r[7],
                "observed_at": r[8],
                "received_at": r[9],
            }
            for r in rows
        ]

    @property
    def current(self) -> Location | None:
        return self._location

    @property
    def stale_seconds(self) -> int:
        return self._stale_seconds

    @property
    def is_stale(self) -> bool:
        if self._location is None:
            return True
        return self._location.is_stale(self._stale_seconds)

    @property
    def age_seconds(self) -> float | None:
        if self._location is None:
            return None
        return self._location.age_seconds

    def to_dict(self) -> dict | None:
        """Serialise current location for the /location endpoint."""
        loc = self._location
        if loc is None:
            return None
        return {
            "device_id": loc.device_id,
            "latitude": loc.latitude,
            "longitude": loc.longitude,
            "accuracy_m": loc.accuracy_m,
            "altitude_m": loc.altitude_m,
            "battery_percent": loc.battery_percent,
            "connection_type": loc.connection_type,
            "trigger": loc.trigger,
            "observed_at": loc.observed_at.isoformat(),
            "received_at": loc.received_at.isoformat(),
            "age_seconds": loc.age_seconds,
            "stale": self.is_stale,
            "source": loc.source,
        }