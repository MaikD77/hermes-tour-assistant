#!/usr/bin/env python3
"""Compatibility exports from the shared location-session core."""

from __future__ import annotations

import os
import sys
from pathlib import Path

CORE_SCRIPTS = Path(
    os.environ.get(
        "HERMES_LOCATION_CORE_DIR",
        str(
            Path(__file__).resolve().parents[2]
            / "location-session-core"
            / "scripts"
        ),
    )
).expanduser()
if str(CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CORE_SCRIPTS))

from location_core.contracts import (  # noqa: E402,F401
    AlertDecision,
    GateDecision,
    LocationSample,
    ProviderResult,
    RouteGeometry,
)

__all__ = [
    "AlertDecision",
    "GateDecision",
    "LocationSample",
    "ProviderResult",
    "RouteGeometry",
]
