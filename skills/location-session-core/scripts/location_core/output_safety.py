#!/usr/bin/env python3
"""Safe rendering helpers for untrusted provider labels and navigation URLs."""

from __future__ import annotations

import math
import re
import urllib.parse

MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+.!|~-])")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PROSE_MARKDOWN = re.compile(r"([\\`\[\]<>])")


def safe_label(value: object, *, maximum_length: int = 160) -> str:
    text = CONTROL_CHARACTERS.sub("", str(value)).strip()
    text = " ".join(text.split())
    text = text[:maximum_length]
    return MARKDOWN_SPECIAL.sub(r"\\\1", text)


def safe_prose(value: object, *, maximum_length: int = 2_500) -> str:
    """Normalize provider prose and neutralize Markdown control sequences."""
    text = CONTROL_CHARACTERS.sub("", str(value)).strip()
    text = " ".join(text.split())[:maximum_length]
    return PROSE_MARKDOWN.sub(r"\\\1", text)


def _coordinate(value: float, minimum: float, maximum: float) -> str:
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError("invalid navigation coordinate")
    return f"{number:.6f}"


def navigation_url(
    origin_lat: float,
    origin_lon: float,
    destination_lat: float,
    destination_lon: float,
    *,
    travel_mode: str = "bicycling",
) -> str:
    if travel_mode not in {"bicycling", "walking"}:
        raise ValueError("unsupported travel mode")
    origin = f"{_coordinate(origin_lat, -90, 90)},{_coordinate(origin_lon, -180, 180)}"
    destination = (
        f"{_coordinate(destination_lat, -90, 90)},"
        f"{_coordinate(destination_lon, -180, 180)}"
    )
    query = urllib.parse.urlencode(
        {
            "api": "1",
            "origin": origin,
            "destination": destination,
            "travelmode": travel_mode,
        }
    )
    return f"https://www.google.com/maps/dir/?{query}"
