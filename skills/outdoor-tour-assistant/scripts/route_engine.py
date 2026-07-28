#!/usr/bin/env python3
"""Compatibility exports from the shared location-session core."""

from __future__ import annotations

import os
import sys
from pathlib import Path

CORE_SCRIPTS = Path(
    os.environ.get(
        "HERMES_LOCATION_CORE_DIR",
        str(Path(__file__).resolve().parents[2] / "location-session-core" / "scripts"),
    )
).expanduser()
if str(CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CORE_SCRIPTS))

from location_core.route_engine import (  # noqa: E402,F401
    MAX_GPX_BYTES,
    MAX_ROUTE_POINTS,
    RouteMatch,
    RoutePoint,
    haversine_m,
    match_position,
    parse_gpx,
)

__all__ = [
    "MAX_GPX_BYTES",
    "MAX_ROUTE_POINTS",
    "RouteMatch",
    "RoutePoint",
    "haversine_m",
    "match_position",
    "parse_gpx",
]
