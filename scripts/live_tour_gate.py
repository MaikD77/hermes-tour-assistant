#!/usr/bin/env python3
"""Dynamic live-tour gate: variable wake cadence based on speed, proximity, and route state.

Reads configuration dynamically from live_tour_assistant.json — no hardcoded
tour IDs or town lists. Degrades gracefully when no GPX or state is available.

Configure your Telegram chat ID via:
  1. Environment variable: HERMES_TOUR_CHAT_ID
  2. Or edit the CHAT_ID constant below
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────
# Set your Telegram chat ID here or via HERMES_TOUR_CHAT_ID env var
CHAT_ID = os.environ.get("HERMES_TOUR_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")

# State file paths (relative to home directory)
SNAPSHOT = Path.home() / ".hermes/state/telegram_live_locations.json"
STATE = Path.home() / ".hermes/state/live_tour_assistant.json"
GATE_STATE = Path.home() / ".hermes/state/live_tour_gate.json"

# Thresholds
MOVE_THRESHOLD_M = 350.0          # Minimum movement to trigger a wake (meters)
OFF_ROUTE_THRESHOLD_M = 150.0     # Distance from GPX considered "off route" (meters)
TOWN_APPROACH_KM = 3.0            # Wake when next town is this close (km)
FINISH_APPROACH_KM = 5.0          # Wake when remaining distance is below this (km)
LUNCH_START = 10.0                # Lunch reminder window start (hour, 24h)
LUNCH_END = 14.0                  # Lunch reminder window end
LUNCH_REMIND_INTERVAL = 30 * 60   # Minimum seconds between lunch nudges
MAX_SILENCE_SECONDS = 15 * 60     # Absolute maximum silence between wakes


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def save_json(path: Path, payload: dict) -> None:
    """Atomically write JSON to file using temporary file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def haversine_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Haversine distance in meters between two coordinates."""
    radius = 6_371_008.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def parse_gpx(path: Path) -> list[tuple[float, float, float]]:
    """Return list of (lat, lon, cum_dist_km) from GPX trackpoints."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    pts = re.findall(r'<trkpt lat="([\d.-]+)" lon="([\d.-]+)"', text)
    if not pts:
        return []
    result: list[tuple[float, float, float]] = []
    cum = 0.0
    prev_lat = prev_lon = None
    for lat_str, lon_str in pts:
        lat, lon = float(lat_str), float(lon_str)
        if prev_lat is not None:
            cum += haversine_m(prev_lat, prev_lon, lat, lon) / 1000.0
        result.append((lat, lon, cum))
        prev_lat, prev_lon = lat, lon
    return result


def extract_towns_from_gpx(track: list[tuple[float, float, float]]) -> list[tuple[float, float, str, float]]:
    """Sample waypoints every ~5 km from GPX track for town-approach detection."""
    if not track:
        return []
    towns: list[tuple[float, float, str, float]] = []
    total_km = track[-1][2]
    step_km = 5.0
    last_sampled_km = -step_km
    for lat, lon, cum_km in track:
        if cum_km - last_sampled_km >= step_km or (cum_km >= total_km * 0.98 and not towns):
            label = f"~{round(cum_km)} km"
            towns.append((lat, lon, label, round(cum_km, 1)))
            last_sampled_km = cum_km
    if track:
        towns[0] = (track[0][0], track[0][1], "Start", 0.0)
        towns[-1] = (track[-1][0], track[-1][1], "Ziel", round(total_km, 1))
    return towns


def snap_to_route(lat: float, lon: float, track: list[tuple[float, float, float]]) -> dict:
    """Find nearest point on GPX track. Returns index, distance, progress."""
    if not track:
        return {"index": -1, "dist_m": float("inf"), "km_from_start": 0.0, "remaining_km": 0.0}

    best_idx = 0
    best_dist = float("inf")
    total_km = track[-1][2] if track else 0.0

    for i, (tlat, tlon, _) in enumerate(track):
        d = haversine_m(lat, lon, tlat, tlon)
        if d < best_dist:
            best_dist = d
            best_idx = i

    km_from_start = track[best_idx][2] if best_idx < len(track) else 0.0
    remaining_km = max(0.0, total_km - km_from_start)

    return {
        "index": best_idx,
        "dist_m": best_dist,
        "km_from_start": round(km_from_start, 1),
        "remaining_km": round(remaining_km, 1),
    }


def find_next_town(km_from_start: float, towns: list[tuple[float, float, str, float]]) -> tuple[str | None, float]:
    """Return (town_name, km_ahead) for the next unpassed waypoint."""
    next_town = None
    next_km_ahead = float("inf")
    for tlat, tlon, name, tkm in towns:
        ahead = tkm - km_from_start
        if ahead > 0.5 and ahead < next_km_ahead:
            next_km_ahead = ahead
            next_town = name
    return next_town, round(next_km_ahead, 1)


def estimate_speed_kmh(lat: float, lon: float, now: float, state: dict) -> float:
    """Estimate speed from last known position and time delta."""
    prev_lat = state.get("last_position_lat")
    prev_lon = state.get("last_position_lon")
    prev_time = state.get("last_position_time")
    if prev_lat is None or prev_lon is None or prev_time is None:
        return 0.0
    dt = now - float(prev_time)
    if dt <= 0:
        return 0.0
    dist_m = haversine_m(lat, lon, float(prev_lat), float(prev_lon))
    return (dist_m / 1000.0) / (dt / 3600.0)


def compute_dynamic_params(
    lat: float, lon: float, now: float,
    state: dict, track: list[tuple[float, float, float]],
    towns: list[tuple[float, float, str, float]],
) -> dict:
    """Compute cadence, trigger type, and context based on rider's state."""
    cadence_minutes = 5
    trigger = "periodic"
    context_flags: list[str] = []

    route = snap_to_route(lat, lon, track)
    off_route = route["dist_m"] > OFF_ROUTE_THRESHOLD_M
    remaining_km = route["remaining_km"]
    km_from_start = route["km_from_start"]

    speed = estimate_speed_kmh(lat, lon, now, state)

    if speed > 20:
        cadence_minutes = 3
    elif speed < 5:
        cadence_minutes = 15
    else:
        cadence_minutes = 5

    if remaining_km > 0 and remaining_km < FINISH_APPROACH_KM:
        cadence_minutes = min(cadence_minutes, 2)
        trigger = "finish_approach"
        context_flags.append("finish_approach")

    if off_route and route["dist_m"] < 500:
        trigger = "off_route"
        context_flags.append("off_route")

    next_town, next_km = find_next_town(km_from_start, towns)
    next_town_name = next_town
    if next_town and next_km <= TOWN_APPROACH_KM and next_km > 0.5:
        context_flags.append("town_approach")
        if next_km <= 1.0:
            trigger = "town_approach"
            context_flags.append("town_imminent")

    hour = time.localtime(now).tm_hour + time.localtime(now).tm_min / 60.0
    lunch_suggested = state.get("lunch_suggested", False)
    if LUNCH_START <= hour <= LUNCH_END and not lunch_suggested:
        last_wake = state.get("last_wake_at", 0)
        if last_wake and (now - float(last_wake)) > LUNCH_REMIND_INTERVAL:
            context_flags.append("lunch_time")
            if "off_route" not in context_flags:
                trigger = "lunch_time"

    context = {
        "reason": trigger,
        "latitude": lat,
        "longitude": lon,
        "speed_kmh": round(speed, 1),
        "cadence_minutes": cadence_minutes,
        "remaining_km": remaining_km,
        "km_from_start": km_from_start,
        "off_route_m": round(route["dist_m"], 0),
        "next_town": next_town_name,
        "next_town_km_ahead": next_km,
        "flags": context_flags,
        "live_location_expires_at": None,
    }

    return {
        "cadence_minutes": cadence_minutes,
        "trigger": trigger,
        "context": context,
        "new_share": False,
        "off_route": off_route,
        "remaining_km": remaining_km,
        "km_from_start": km_from_start,
        "speed": speed,
        "next_town": next_town_name,
        "next_town_km_ahead": next_km,
        "lunch_suggested": lunch_suggested,
    }


def skip() -> None:
    """Output 'do not wake' for the cron scheduler."""
    print(json.dumps({"wakeAgent": False}))


def main() -> None:
    now = time.time()

    # Load assistant state → derive GPX path dynamically
    assistant_state = load_json(STATE, {})
    tour_info = assistant_state.get("tour", {})
    tour_gpx_str = tour_info.get("gpx", "")
    gpx_path = Path(tour_gpx_str) if tour_gpx_str else None

    # Load GPX track (graceful degrade if none exists)
    track: list[tuple[float, float, float]] = []
    towns: list[tuple[float, float, str, float]] = []
    if gpx_path and gpx_path.exists():
        track = parse_gpx(gpx_path)
        towns = extract_towns_from_gpx(track)

    # Load live-location snapshot
    snapshot = load_json(SNAPSHOT, {"locations": []})
    candidates = [
        item
        for item in snapshot.get("locations", [])
        if str(item.get("chat_id", "")) == CHAT_ID
        and float(item.get("expires_at", 0) or 0) > now
    ]
    gate_state = load_json(GATE_STATE, {})

    # No active live share → deactivate
    if not candidates:
        if gate_state.get("active"):
            save_json(GATE_STATE, {"active": False, "stopped_at": now})
        skip()
        return

    # Latest position
    current = max(candidates, key=lambda item: float(item.get("updated_at", 0) or 0))
    lat = float(current["lat"])
    lon = float(current["lon"])
    message_id = str(current.get("message_id", ""))
    expires_at = current.get("expires_at")

    new_share = not gate_state.get("active") or str(gate_state.get("message_id", "")) != message_id

    if track:
        dyn = compute_dynamic_params(lat, lon, now, gate_state, track, towns)
    else:
        # Graceful degrade: no GPX — simple timer-based wake
        dyn = {
            "cadence_minutes": 5,
            "trigger": "periodic",
            "context": {
                "reason": "periodic",
                "latitude": lat,
                "longitude": lon,
                "speed_kmh": 0.0,
                "cadence_minutes": 5,
                "remaining_km": 0,
                "km_from_start": 0,
                "off_route_m": 0,
                "next_town": None,
                "next_town_km_ahead": None,
                "flags": [],
                "live_location_expires_at": expires_at,
            },
            "new_share": new_share,
            "off_route": False,
            "remaining_km": 0,
            "km_from_start": 0,
            "speed": 0.0,
            "next_town": None,
            "next_town_km_ahead": None,
            "lunch_suggested": False,
        }
        # Don't wake for every periodic check without a route
        elapsed = now - float(gate_state.get("last_wake_at", 0) or 0)
        if not new_share and elapsed < 5 * 60 + 30:
            save_json(GATE_STATE, {
                "active": True,
                "message_id": message_id,
                "last_position_lat": lat,
                "last_position_lon": lon,
                "last_position_time": now,
                "last_wake_at": gate_state.get("last_wake_at", now),
            })
            skip()
            return

    # Wake decision logic
    moved_m = float("inf")
    if gate_state.get("last_wake_lat") is not None:
        moved_m = haversine_m(
            float(gate_state["last_wake_lat"]),
            float(gate_state["last_wake_lon"]),
            lat, lon,
        )

    elapsed = now - float(gate_state.get("last_wake_at", 0) or 0)
    dyn_cadence = dyn["cadence_minutes"]
    max_silence = min(dyn_cadence * 60 + 30, MAX_SILENCE_SECONDS)

    wake = (
        new_share
        or dyn["off_route"]
        or moved_m >= MOVE_THRESHOLD_M
        or elapsed >= max_silence
        or dyn["trigger"] == "town_approach"
        or dyn["trigger"] == "lunch_time"
        or dyn["trigger"] == "finish_approach"
    )

    # Update gate state
    next_state = {
        "active": True,
        "message_id": message_id,
        "last_wake_lat": lat,
        "last_wake_lon": lon,
        "last_wake_at": now,
        "last_position_lat": lat,
        "last_position_lon": lon,
        "last_position_time": now,
        "last_speed_kmh": round(dyn["speed"], 1),
        "last_gpx_index": -1,
        "remaining_km": dyn["remaining_km"],
        "km_from_start": dyn["km_from_start"],
        "last_trigger": dyn["trigger"],
        "cadence_minutes": dyn_cadence,
        "last_town": dyn.get("next_town"),
        "last_town_km_ahead": dyn.get("next_town_km_ahead"),
        "lunch_suggested": dyn["lunch_suggested"] or dyn["trigger"] == "lunch_time",
    }

    if not wake:
        next_state.pop("last_wake_lat", None)
        next_state.pop("last_wake_lon", None)
        next_state.pop("last_wake_at", None)
        save_json(GATE_STATE, next_state)
        skip()
        return

    # Build final context
    context = dyn["context"]
    context["live_location_expires_at"] = expires_at

    if new_share:
        context["reason"] = "live_location_started"
    elif dyn["off_route"]:
        context["reason"] = "off_route"
    elif dyn["trigger"] == "town_approach":
        context["reason"] = "town_approach"
    elif dyn["trigger"] == "lunch_time":
        context["reason"] = "lunch_time"
    elif dyn["trigger"] == "finish_approach":
        context["reason"] = "finish_approach"
    elif moved_m >= MOVE_THRESHOLD_M:
        context["reason"] = "moved"
    elif elapsed >= max_silence:
        context["reason"] = "check_in"

    context["cadence_minutes"] = dyn_cadence

    if not track:
        context["flags"] = context.get("flags", []) + ["no_route"]
        if new_share:
            context["reason"] = "live_location_started"

    next_state["last_trigger"] = context["reason"]
    save_json(GATE_STATE, next_state)

    print(json.dumps({"wakeAgent": True, "context": context}))


if __name__ == "__main__":
    main()