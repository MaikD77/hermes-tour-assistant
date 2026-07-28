"""Shared runtime primitives for Hermes location-aware skills."""

from .contracts import (
    AlertDecision,
    GateDecision,
    LocationSample,
    ProviderResult,
    RouteGeometry,
)
from .output_safety import navigation_url, safe_label, safe_prose
from .providers import ProviderRegistry
from .repository import CorruptStateError, JsonStateRepository

StateRepository = JsonStateRepository

__all__ = [
    "AlertDecision",
    "CorruptStateError",
    "GateDecision",
    "JsonStateRepository",
    "LocationSample",
    "ProviderRegistry",
    "ProviderResult",
    "RouteGeometry",
    "StateRepository",
    "navigation_url",
    "safe_label",
    "safe_prose",
]
