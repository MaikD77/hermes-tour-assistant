"""Shared runtime primitives for Hermes location-aware skills."""

from .contracts import (
    AlertDecision,
    GateDecision,
    LocationSample,
    ProviderResult,
    RouteGeometry,
)
from .location_sources import (
    HttpOwnTracksReceiver,
    LocationObservation,
    LocationSource,
    LocationSourceResolver,
    LocationSourceResult,
    LocationStatus,
    OwnTracksLocationSource,
    ReplayLocationSource,
    TelegramLocationSource,
    canonical_device_id_from_env,
    parse_source_order,
)
from .movement import (
    DataQuality,
    EngineState,
    MovementConfig,
    MovementEngine,
    MovementEvent,
    MovementEventType,
    MovementMode,
    MovementResult,
    MovementSegment,
    MovementState,
    ProcessingStatus,
)
from .movement_state import MovementStateRepository
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
    "LocationObservation",
    "LocationSource",
    "LocationSourceResolver",
    "LocationSourceResult",
    "LocationStatus",
    "DataQuality",
    "EngineState",
    "MovementConfig",
    "MovementEngine",
    "MovementEvent",
    "MovementEventType",
    "MovementMode",
    "MovementResult",
    "MovementSegment",
    "MovementState",
    "MovementStateRepository",
    "ProcessingStatus",
    "HttpOwnTracksReceiver",
    "OwnTracksLocationSource",
    "ReplayLocationSource",
    "TelegramLocationSource",
    "canonical_device_id_from_env",
    "parse_source_order",
    "ProviderRegistry",
    "ProviderResult",
    "RouteGeometry",
    "StateRepository",
    "navigation_url",
    "safe_label",
    "safe_prose",
]
