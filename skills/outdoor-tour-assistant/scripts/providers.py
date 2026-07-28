#!/usr/bin/env python3
"""Compatibility exports from the shared location-session core."""

from __future__ import annotations

import os
import sys
import urllib  # noqa: F401
from pathlib import Path

CORE_SCRIPTS = Path(
    os.environ.get(
        "HERMES_LOCATION_CORE_DIR",
        str(Path(__file__).resolve().parents[2] / "location-session-core" / "scripts"),
    )
).expanduser()
if str(CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CORE_SCRIPTS))

from location_core.providers import *  # noqa: E402,F403
from location_core.providers import _matches_category  # noqa: E402,F401
