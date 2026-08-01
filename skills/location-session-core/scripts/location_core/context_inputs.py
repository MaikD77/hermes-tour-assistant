"""Sanitized, independently loaded inputs for Current Context computation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from .context import ContextConfig
from .location_sources import (
    HttpOwnTracksReceiver,
    LocationObservation,
    LocationSourceResolver,
    LocationSourceResult,
    LocationStatus,
    OwnTracksLocationSource,
    TelegramLocationSource,
    canonical_device_id_from_env,
    parse_source_order,
)
from .movement import EngineState, MovementConfig
from .movement_state import MovementStateRepository
from .place import PlaceConfig, PlaceEngineState
from .place_state import PlaceStateRepository
from .profile import ProfileConfig, ProfileState
from .profile_state import ProfileRebuildRequired, ProfileStateRepository
from .repository import CorruptStateError


@dataclass(frozen=True)
class ContextInputIssue:
    component: str
    code: str
    reason: str
    critical: bool = False


@dataclass(frozen=True)
class ContextInputBundle:
    observation: LocationObservation | None = None
    movement_state: EngineState | None = None
    place_state: PlaceEngineState | None = None
    profile_state: ProfileState | None = None
    location_result: LocationSourceResult = LocationSourceResult(LocationStatus.NOT_AVAILABLE)
    issues: tuple[ContextInputIssue, ...] = ()


def build_location_source_resolver(
    env: Mapping[str, str] = os.environ,
) -> LocationSourceResolver:
    """Build productive adapters without exposing either payload schema to callers."""
    device_id = canonical_device_id_from_env(env)
    home = Path(env.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    tour_state = Path(env.get("HERMES_TOUR_STATE_DIR", str(home / "state"))).expanduser()
    snapshot = Path(env.get(
        "HERMES_TELEGRAM_LOCATION_SNAPSHOT", str(tour_state / "telegram_live_locations.json")
    )).expanduser()
    owntracks_url = env.get(
        "HERMES_OWNTRACKS_URL", "http://127.0.0.1:9090/location"
    ).strip()
    chat_id = env.get("HERMES_TOUR_CHAT_ID", "").strip()
    if not owntracks_url:
        raise ValueError("HERMES_OWNTRACKS_URL must not be empty")
    return LocationSourceResolver({
        "owntracks": OwnTracksLocationSource(
            HttpOwnTracksReceiver(owntracks_url), canonical_device_id=device_id
        ),
        "telegram": TelegramLocationSource(snapshot, chat_id, canonical_device_id=device_id),
    }, parse_source_order(env.get("HERMES_LOCATION_SOURCE_ORDER")))


class ContextInputLoader:
    """Load each lower layer independently and report only sanitized failure facts."""

    def __init__(self, config: ContextConfig, env: Mapping[str, str] = os.environ,
                 resolver: LocationSourceResolver | None = None) -> None:
        self.config, self.env = config, env
        self.resolver = resolver or build_location_source_resolver(env)

    def load(self, *, now: datetime) -> ContextInputBundle:
        issues: list[ContextInputIssue] = []
        try:
            location = self.resolver.resolve(
                now=now, max_age_seconds=self.config.location_freshness.stale_seconds
            )
        except (OSError, TypeError, ValueError):
            location = LocationSourceResult(LocationStatus.INVALID, detail="resolver_invalid")
        if location.status is not LocationStatus.OK:
            issues.append(ContextInputIssue("location", f"location_{location.status.value}",
                f"location source result is {location.status.value}"))

        movement: EngineState | None = None
        try:
            movement = MovementStateRepository(MovementConfig.from_env(self.env).state_dir).load()
        except (CorruptStateError, OSError, TypeError, ValueError):
            issues.append(ContextInputIssue("movement", "movement_state_invalid",
                "movement state is corrupt or semantically invalid", True))

        places: PlaceEngineState | None = None
        try:
            places = PlaceStateRepository(PlaceConfig.from_env(self.env).state_dir).load()
        except (CorruptStateError, OSError, TypeError, ValueError):
            issues.append(ContextInputIssue("place", "place_state_invalid",
                "place state is corrupt or semantically invalid", True))

        profile: ProfileState | None = None
        try:
            profile_env = dict(self.env)
            profile_env.setdefault("HERMES_PROFILE_TIMEZONE", self.config.timezone)
            profile_config = ProfileConfig.from_env(profile_env)
            profile = ProfileStateRepository(profile_config.state_dir).load()
        except ProfileRebuildRequired:
            issues.append(ContextInputIssue("profile", "profile_rebuild_required",
                "profile state requires an explicit rebuild"))
        except (CorruptStateError, OSError, TypeError, ValueError):
            issues.append(ContextInputIssue("profile", "profile_state_invalid",
                "profile state is corrupt or semantically invalid", True))
        return ContextInputBundle(location.observation, movement, places, profile,
                                  location, tuple(issues))
