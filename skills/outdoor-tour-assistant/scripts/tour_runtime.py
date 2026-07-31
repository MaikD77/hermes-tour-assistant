#!/usr/bin/env python3
"""Deterministic integration layer for sessions, routing, gating and event policy."""

from __future__ import annotations

import math
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from contracts import AlertDecision, GateDecision, LocationObservation  # noqa: E402
from event_engine import (  # noqa: E402
    TourEvent,
    mark_delivered,
    select_for_delivery,
    upsert_event,
)
from location_core.location_sources import (  # noqa: E402
    adapt_legacy_sample,
    observation_session_id,
)
from route_engine import (  # noqa: E402
    MAX_GPX_BYTES,
    RouteMatch,
    haversine_m,
    match_position,
    parse_gpx,
)
from tour_state import (  # noqa: E402
    StateRepository,
    empty_state,
    end_session,
    set_route_match,
    start_session,
)

MOVE_THRESHOLD_M = 350.0
OFF_ROUTE_ENTER_M = 150.0
OFF_ROUTE_EXIT_M = 80.0
TOWN_APPROACH_M = 3_000.0
FINISH_APPROACH_M = 5_000.0
MAX_SILENCE_SECONDS = 15 * 60
EVENT_COOLDOWN_SECONDS = 10 * 60
OPERATIONAL_ERROR_COOLDOWN_SECONDS = 24 * 60 * 60


class TourProfile:
    """Small immutable activity profile without adding a runtime dependency."""

    def __init__(
        self,
        activity: str = "cycling",
        *,
        locale: str = "de-DE",
        fast_speed_kmh: float | None = None,
        slow_speed_kmh: float | None = None,
        max_plausible_speed_kmh: float | None = None,
        move_threshold_m: float | None = None,
        off_route_enter_m: float | None = None,
        off_route_exit_m: float | None = None,
        settlement_approach_m: float | None = None,
        finish_approach_m: float | None = None,
    ) -> None:
        if activity not in {"cycling", "walking"}:
            raise ValueError("activity must be cycling or walking")
        if not locale or len(locale) > 32:
            raise ValueError("locale must contain 1 to 32 characters")
        self.activity = activity
        self.locale = locale
        self.fast_speed_kmh = (
            fast_speed_kmh
            if fast_speed_kmh is not None
            else (20.0 if activity == "cycling" else 6.0)
        )
        self.slow_speed_kmh = (
            slow_speed_kmh
            if slow_speed_kmh is not None
            else (5.0 if activity == "cycling" else 1.5)
        )
        self.max_plausible_speed_kmh = (
            max_plausible_speed_kmh
            if max_plausible_speed_kmh is not None
            else (100.0 if activity == "cycling" else 20.0)
        )
        self.move_threshold_m = (
            move_threshold_m
            if move_threshold_m is not None
            else (MOVE_THRESHOLD_M if activity == "cycling" else 150.0)
        )
        self.off_route_enter_m = (
            off_route_enter_m
            if off_route_enter_m is not None
            else (OFF_ROUTE_ENTER_M if activity == "cycling" else 100.0)
        )
        self.off_route_exit_m = (
            off_route_exit_m
            if off_route_exit_m is not None
            else (OFF_ROUTE_EXIT_M if activity == "cycling" else 50.0)
        )
        self.settlement_approach_m = (
            settlement_approach_m
            if settlement_approach_m is not None
            else (TOWN_APPROACH_M if activity == "cycling" else 1_500.0)
        )
        self.finish_approach_m = (
            finish_approach_m
            if finish_approach_m is not None
            else (FINISH_APPROACH_M if activity == "cycling" else 2_000.0)
        )
        positive_values = (
            self.fast_speed_kmh,
            self.slow_speed_kmh,
            self.max_plausible_speed_kmh,
            self.move_threshold_m,
            self.off_route_enter_m,
            self.off_route_exit_m,
            self.settlement_approach_m,
            self.finish_approach_m,
        )
        if any(not math.isfinite(value) or value <= 0 for value in positive_values):
            raise ValueError("profile thresholds must be finite and positive")
        if self.off_route_exit_m >= self.off_route_enter_m:
            raise ValueError("off-route exit threshold must be below enter threshold")

    def cadence_minutes(
        self,
        speed_kmh: float,
        remaining_m: float | None,
        *,
        route_verified: bool,
    ) -> int:
        if (
            route_verified
            and remaining_m is not None
            and 0 < remaining_m < self.finish_approach_m
        ):
            return 2
        if not route_verified:
            return 5
        if speed_kmh > self.fast_speed_kmh:
            return 3
        if speed_kmh < self.slow_speed_kmh:
            return 15
        return 5


class TourRuntime:
    def __init__(self, state_path: Path, *, profile: TourProfile | None = None):
        self.repository = StateRepository(state_path)
        self.profile = profile or TourProfile()

    def begin_session(
        self,
        session_id: str,
        *,
        started_at: float,
        expires_at: float | None,
    ) -> dict[str, Any]:
        return self.repository.update(
            lambda state: start_session(
                state,
                session_id,
                started_at=started_at,
                expires_at=expires_at,
            )
        )

    def _install_gpx(self, source: Path, route_id: str | int) -> Path:
        source = source.expanduser().resolve()
        if not source.is_file():
            raise ValueError("GPX source file does not exist")
        if source.stat().st_size > MAX_GPX_BYTES:
            raise ValueError("GPX file exceeds the 20 MiB safety limit")
        self.repository._ensure_private_directory()
        safe_route_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(route_id)).strip("-") or "route"
        destination = self.repository.path.parent / f"current-tour-{safe_route_id}.gpx"
        if source != destination.resolve():
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            temporary = Path(temporary_name)
            try:
                os.fchmod(descriptor, 0o600)
                with source.open("rb") as source_handle, os.fdopen(
                    descriptor, "wb"
                ) as destination_handle:
                    shutil.copyfileobj(source_handle, destination_handle)
                    destination_handle.flush()
                    os.fsync(destination_handle.fileno())
                os.replace(temporary, destination)
                os.chmod(destination, 0o600)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        else:
            os.chmod(destination, 0o600)
        parse_gpx(destination)
        return destination

    def attach_route(
        self,
        *,
        provider: str,
        route_id: str | int,
        name: str,
        gpx_path: Path,
    ) -> dict[str, Any]:
        installed = self._install_gpx(gpx_path, route_id)
        return self.repository.update(
            lambda state: set_route_match(
                state,
                "matched",
                provider=provider,
                route_id=route_id,
                name=name,
                gpx_path=str(installed),
            )
        )

    def prepare_route(
        self,
        *,
        provider: str,
        route_id: str | int,
        name: str,
        gpx_path: Path,
    ) -> dict[str, Any]:
        installed = self._install_gpx(gpx_path, route_id)
        return self.repository.update(
            lambda state: set_route_match(
                state,
                "matched",
                provider=provider,
                route_id=route_id,
                name=name,
                gpx_path=str(installed),
                prepared=True,
            )
        )

    def prepare_pending_route(
        self,
        *,
        provider: str,
        route_id: str | int,
        name: str,
    ) -> dict[str, Any]:
        def operation(state: dict[str, Any]) -> dict[str, Any]:
            fresh = empty_state()
            fresh["route"].update(
                {
                    "match_status": "matching",
                    "provider": provider,
                    "id": route_id,
                    "name": name,
                }
            )
            return fresh

        return self.repository.update(operation)

    def set_route_status(
        self,
        status: str,
        *,
        provider: str | None = None,
        route_id: str | int | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        if status == "matched":
            raise ValueError("use attach_route to validate a matched route")
        return self.repository.update(
            lambda state: set_route_match(
                state,
                status,
                provider=provider,
                route_id=route_id,
                name=name,
            )
        )

    def set_verified_settlements(
        self,
        settlements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized: list[dict[str, Any]] = []
        for item in settlements:
            if not item.get("verified_place"):
                continue
            name = str(item.get("name") or "").strip()
            source = str(item.get("source") or "").strip()
            try:
                route_progress_m = float(item["route_progress_m"])
                confidence = float(item.get("confidence", 0))
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("settlement has invalid route data") from error
            if (
                not name
                or not source
                or not math.isfinite(route_progress_m)
                or route_progress_m < 0
                or not 0.5 <= confidence <= 1
            ):
                raise ValueError("settlement is not sufficiently verified")
            normalized.append(
                {
                    "id": str(item.get("id") or f"{source}:{name}"),
                    "name": name,
                    "source": source,
                    "route_progress_m": route_progress_m,
                    "confidence": confidence,
                    "verified_place": True,
                    "observed_at": item.get("observed_at"),
                }
            )

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            if not state["route"].get("verified"):
                raise RuntimeError("cannot attach settlements without a verified route")
            state["route"]["settlements"] = normalized
            return state

        return self.repository.update(operation)

    def update_position(self, lat: float, lon: float, *, observed_at: float) -> RouteMatch:
        matched: list[RouteMatch] = []

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            route_state = state["route"]
            if not route_state.get("verified") or not route_state.get("gpx_path"):
                raise RuntimeError("no verified route attached")
            route = parse_gpx(Path(route_state["gpx_path"]))
            previous = state.get("position") or {}
            match = match_position(
                lat,
                lon,
                route,
                previous_segment_index=previous.get("segment_index"),
                previous_progress_m=previous.get("progress_m"),
            )
            matched.append(match)
            state["position"] = {
                "observed_at": observed_at,
                "lat": lat,
                "lon": lon,
                **asdict(match),
            }
            return state

        self.repository.update(operation)
        return matched[0]

    def record_event(self, event: TourEvent) -> None:
        def operation(state: dict[str, Any]) -> dict[str, Any]:
            upsert_event(state["events"], event)
            return state

        self.repository.update(operation)

    def next_notifications(self, *, now: float) -> list[dict[str, Any]]:
        selected_events: list[dict[str, Any]] = []

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            selected = select_for_delivery(state["events"], now)
            mark_delivered(state["events"], selected, now)
            selected_events.extend(selected)
            return state

        self.repository.update(operation)
        return selected_events

    def next_alert(self, *, now: float) -> AlertDecision:
        events = self.next_notifications(now=now)
        return AlertDecision(silent=not events, events=tuple(events))

    def end_active_session(self, *, ended_at: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if ended_at is None else ended_at

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            if state["session"].get("status") == "inactive":
                return state
            return end_session(state, ended_at=timestamp)

        return self.repository.update(operation)

    @staticmethod
    def _event_due(schedule: dict[str, Any], event_key: str, now: float) -> bool:
        last_sent = float((schedule.get("event_last_sent") or {}).get(event_key, 0) or 0)
        return now - last_sent >= EVENT_COOLDOWN_SECONDS

    @staticmethod
    def _speed_kmh(
        sample: LocationObservation,
        previous_position: dict[str, Any] | None,
        max_plausible_speed_kmh: float,
    ) -> float:
        if not previous_position:
            return 0.0
        try:
            elapsed = sample.observed_at.timestamp() - float(previous_position["observed_at"])
            if elapsed <= 0:
                return 0.0
            distance = haversine_m(
                sample.latitude,
                sample.longitude,
                float(previous_position["lat"]),
                float(previous_position["lon"]),
            )
        except (KeyError, TypeError, ValueError):
            return 0.0
        speed = distance / 1000 / (elapsed / 3600)
        if not math.isfinite(speed) or speed > max_plausible_speed_kmh:
            return 0.0
        return speed

    def evaluate_gate(self, sample: LocationObservation, *, now: float) -> GateDecision:
        sample = adapt_legacy_sample(
            sample, received_at=datetime.fromtimestamp(now, UTC)
        )
        decision: list[GateDecision] = []

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            previous_session_id = state["session"].get("id")
            new_share = (
                previous_session_id != observation_session_id(sample)
                or state["session"].get("status") == "inactive"
            )
            state = start_session(
                state,
                observation_session_id(sample),
                started_at=sample.observed_at.timestamp(),
                expires_at=sample.observed_at.timestamp() + 300,
            )
            previous_position = state.get("position")
            schedule = state["schedule"]
            speed = self._speed_kmh(
                sample,
                previous_position,
                self.profile.max_plausible_speed_kmh,
            )
            route_match: RouteMatch | None = None
            route_error = False
            if state["route"].get("verified") and state["route"].get("gpx_path"):
                try:
                    route = parse_gpx(Path(state["route"]["gpx_path"]))
                    route_match = match_position(
                        sample.latitude,
                        sample.longitude,
                        route,
                        previous_segment_index=(previous_position or {}).get("segment_index"),
                        previous_progress_m=(previous_position or {}).get("progress_m"),
                    )
                except (OSError, ValueError):
                    metadata = state["route"]
                    state = set_route_match(
                        state,
                        "failed",
                        provider=metadata.get("provider"),
                        route_id=metadata.get("id"),
                        name=metadata.get("name"),
                    )
                    schedule = state["schedule"]
                    route_error = True

            position: dict[str, Any] = {
                "observed_at": sample.observed_at.timestamp(),
                "lat": sample.latitude,
                "lon": sample.longitude,
            }
            if route_match:
                position.update(asdict(route_match))
            state["position"] = position

            remaining_m = route_match.remaining_m if route_match else None
            cadence = self.profile.cadence_minutes(
                speed,
                remaining_m,
                route_verified=bool(route_match),
            )
            flags: list[str] = []
            if not route_match:
                flags.append("no_route")
            if route_error:
                flags.append("route_error")
            if route_match and route_match.ambiguous:
                flags.append("direction_ambiguous")

            was_off_route = bool(schedule.get("off_route_active"))
            off_route = False
            if route_match and not route_match.ambiguous:
                off_route = (
                    route_match.offset_m >= self.profile.off_route_exit_m
                    if was_off_route
                    else route_match.offset_m > self.profile.off_route_enter_m
                )
            if off_route:
                flags.append("off_route")

            finish = bool(
                route_match
                and remaining_m is not None
                and 0 < remaining_m < self.profile.finish_approach_m
            )
            if finish:
                flags.append("finish_approach")

            settlement_key: str | None = None
            if route_match and not route_match.ambiguous:
                ahead = [
                    item
                    for item in state["route"].get("settlements", [])
                    if 200
                    < float(item["route_progress_m"]) - route_match.progress_m
                    <= self.profile.settlement_approach_m
                ]
                if ahead:
                    nearest = min(
                        ahead,
                        key=lambda item: float(item["route_progress_m"]),
                    )
                    settlement_key = f"town:{nearest['id']}"
                    flags.append("town_approach")

            local = time.localtime(now)
            local_day = time.strftime("%Y-%m-%d", local)
            local_hour = local.tm_hour + local.tm_min / 60
            lunch = (
                10 <= local_hour <= 14
                and schedule.get("lunch_suggested_on") != local_day
            )
            if lunch:
                flags.append("lunch_time")

            moved_m = 0.0
            last_wake_position = schedule.get("last_wake_position")
            if last_wake_position:
                moved_m = haversine_m(
                    float(last_wake_position["lat"]),
                    float(last_wake_position["lon"]),
                    sample.latitude,
                    sample.longitude,
                )
            next_due_at = schedule.get("next_due_at")
            due = next_due_at is None or now >= float(next_due_at)

            reason: str | None = None
            event_key: str | None = None
            if new_share:
                reason = "live_location_started"
            elif off_route and (
                not was_off_route or self._event_due(schedule, "off_route", now)
            ):
                reason = "off_route"
                event_key = "off_route"
            elif finish and self._event_due(schedule, "finish_approach", now):
                reason = "finish_approach"
                event_key = "finish_approach"
            elif settlement_key and self._event_due(schedule, settlement_key, now):
                reason = "town_approach"
                event_key = settlement_key
            elif lunch:
                reason = "lunch_time"
                event_key = f"lunch:{local_day}"
            elif moved_m >= self.profile.move_threshold_m:
                reason = "moved"
            elif due:
                reason = "check_in"

            wake = reason is not None
            schedule.update(
                {
                    "active": True,
                    "cadence_minutes": cadence,
                    "off_route_active": off_route,
                }
            )
            if wake:
                schedule["last_wake_at"] = now
                schedule["next_due_at"] = now + min(
                    cadence * 60,
                    MAX_SILENCE_SECONDS,
                )
                schedule["last_trigger"] = reason
                schedule["last_wake_position"] = {
                    "lat": sample.latitude,
                    "lon": sample.longitude,
                }
                if event_key:
                    schedule.setdefault("event_last_sent", {})[event_key] = now
                if reason == "lunch_time":
                    schedule["lunch_suggested_on"] = local_day
            else:
                proposed_due = (
                    float(schedule.get("last_wake_at") or now)
                    + min(cadence * 60, MAX_SILENCE_SECONDS)
                )
                existing_due = schedule.get("next_due_at")
                schedule["next_due_at"] = (
                    proposed_due
                    if existing_due is None
                    else min(float(existing_due), proposed_due)
                )
            state["schedule"] = schedule
            if state["route"].get("verified"):
                state["session"]["status"] = "active"
            elif state["session"].get("status") == "starting":
                state["session"]["status"] = "matching_route"
            decision.append(
                GateDecision(
                    wake_agent=wake,
                    session_id=observation_session_id(sample) if wake else None,
                    reason=reason,
                    cadence_minutes=cadence if wake else None,
                    flags=tuple(flags) if wake else (),
                )
            )
            return state

        self.repository.update(operation)
        return decision[0]

    def operational_decision(
        self,
        error_code: str,
        *,
        now: float,
        cooldown_seconds: float = OPERATIONAL_ERROR_COOLDOWN_SECONDS,
    ) -> GateDecision:
        should_wake: list[bool] = []

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            errors = state["schedule"].setdefault("operational_errors", {})
            last = float(errors.get(error_code, 0) or 0)
            wake = now - last >= cooldown_seconds
            if wake:
                errors[error_code] = now
            should_wake.append(wake)
            return state

        self.repository.update(operation)
        return GateDecision(
            wake_agent=should_wake[0],
            reason="operational_error" if should_wake[0] else None,
            flags=("operational_error",) if should_wake[0] else (),
            error_code=error_code if should_wake[0] else None,
        )

    def agent_context(self, *, include_location: bool = False) -> dict[str, Any]:
        state = self.repository.load()
        result = {
            "schema_version": state["schema_version"],
            "session": state["session"],
            "route": state["route"],
            "schedule": {
                "cadence_minutes": state["schedule"].get("cadence_minutes"),
                "last_trigger": state["schedule"].get("last_trigger"),
            },
            "weather": state.get("weather"),
            "provider_health": state.get("provider_health"),
        }
        position = state.get("position")
        if position:
            result["position"] = {
                key: value
                for key, value in position.items()
                if include_location or key not in {"lat", "lon"}
            }
        return result

    def reset(self) -> dict[str, Any]:
        self.repository.save(empty_state())
        return self.repository.load()
