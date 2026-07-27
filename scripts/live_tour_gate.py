#!/usr/bin/env python3
"""Dynamic live-tour gate with explicit due times and event cooldowns."""

from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path

CHAT_ID = os.environ.get("HERMES_TOUR_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
SNAPSHOT = Path.home() / ".hermes/state/telegram_live_locations.json"
STATE = Path.home() / ".hermes/state/live_tour_assistant.json"
GATE_STATE = Path.home() / ".hermes/state/live_tour_gate.json"

MOVE_THRESHOLD_M = 350.0
OFF_ROUTE_ENTER_M = 150.0
OFF_ROUTE_EXIT_M = 80.0
TOWN_APPROACH_KM = 3.0
FINISH_APPROACH_KM = 5.0
LUNCH_START = 10.0
LUNCH_END = 14.0
MAX_SILENCE_SECONDS = 15 * 60
EVENT_COOLDOWN_SECONDS = 10 * 60


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def haversine_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    radius = 6_371_008.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def parse_gpx(path: Path) -> list[tuple[float, float, float]]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    pts = re.findall(r'<trkpt lat="([\d.-]+)" lon="([\d.-]+)"', text)
    result: list[tuple[float, float, float]] = []
    cumulative = 0.0
    previous: tuple[float, float] | None = None
    for lat_text, lon_text in pts:
        lat, lon = float(lat_text), float(lon_text)
        if previous is not None:
            cumulative += haversine_m(previous[0], previous[1], lat, lon) / 1000.0
        result.append((lat, lon, cumulative))
        previous = (lat, lon)
    return result


def extract_towns_from_gpx(
    track: list[tuple[float, float, float]],
) -> list[tuple[float, float, str, float]]:
    if not track:
        return []
    checkpoints: list[tuple[float, float, str, float]] = []
    last_km = -5.0
    for lat, lon, cumulative in track:
        if cumulative - last_km >= 5.0:
            checkpoints.append((lat, lon, f"~{round(cumulative)} km", round(cumulative, 1)))
            last_km = cumulative
    checkpoints[0] = (track[0][0], track[0][1], "Start", 0.0)
    checkpoints[-1] = (track[-1][0], track[-1][1], "Ziel", round(track[-1][2], 1))
    return checkpoints


def snap_to_route(lat: float, lon: float, track: list[tuple[float, float, float]]) -> dict:
    if not track:
        return {"index": -1, "dist_m": float("inf"), "km_from_start": 0.0, "remaining_km": 0.0}
    best_index = min(
        range(len(track)),
        key=lambda index: haversine_m(lat, lon, track[index][0], track[index][1]),
    )
    distance = haversine_m(lat, lon, track[best_index][0], track[best_index][1])
    progress = track[best_index][2]
    return {
        "index": best_index,
        "dist_m": distance,
        "km_from_start": round(progress, 1),
        "remaining_km": round(max(0.0, track[-1][2] - progress), 1),
    }


def find_next_town(
    km_from_start: float,
    towns: list[tuple[float, float, str, float]],
) -> tuple[str | None, float | None]:
    candidates = [(name, km - km_from_start) for _, _, name, km in towns if km - km_from_start > 0.5]
    if not candidates:
        return None, None
    name, distance = min(candidates, key=lambda item: item[1])
    return name, round(distance, 1)


def estimate_speed_kmh(lat: float, lon: float, now: float, state: dict) -> float:
    previous = (
        state.get("last_position_lat"),
        state.get("last_position_lon"),
        state.get("last_position_time"),
    )
    if any(value is None for value in previous):
        return 0.0
    elapsed = now - float(previous[2])
    if elapsed <= 0:
        return 0.0
    distance = haversine_m(lat, lon, float(previous[0]), float(previous[1]))
    return distance / 1000.0 / (elapsed / 3600.0)


def cadence_for(speed_kmh: float, remaining_km: float) -> int:
    if 0 < remaining_km < FINISH_APPROACH_KM:
        return 2
    if speed_kmh > 20:
        return 3
    if speed_kmh < 5:
        return 15
    return 5


def event_due(state: dict, event: str, now: float) -> bool:
    last_sent = float((state.get("event_last_sent") or {}).get(event, 0) or 0)
    return now - last_sent >= EVENT_COOLDOWN_SECONDS


def compute_dynamic_params(
    lat: float,
    lon: float,
    now: float,
    state: dict,
    track: list[tuple[float, float, float]],
    towns: list[tuple[float, float, str, float]],
) -> dict:
    route = snap_to_route(lat, lon, track)
    speed = estimate_speed_kmh(lat, lon, now, state)
    cadence = cadence_for(speed, route["remaining_km"])

    was_off_route = bool(state.get("off_route_active", False))
    if was_off_route:
        off_route = route["dist_m"] >= OFF_ROUTE_EXIT_M
    else:
        off_route = route["dist_m"] > OFF_ROUTE_ENTER_M

    flags: list[str] = []
    trigger = "periodic"
    if 0 < route["remaining_km"] < FINISH_APPROACH_KM:
        flags.append("finish_approach")
        if event_due(state, "finish_approach", now):
            trigger = "finish_approach"
    if off_route:
        flags.append("off_route")
        if not was_off_route or event_due(state, "off_route", now):
            trigger = "off_route"

    next_town, next_town_km = find_next_town(route["km_from_start"], towns)
    if next_town_km is not None and 0.5 < next_town_km <= TOWN_APPROACH_KM:
        flags.append("town_approach")

    local = time.localtime(now)
    hour = local.tm_hour + local.tm_min / 60.0
    lunch_suggested = bool(state.get("lunch_suggested", False))
    if LUNCH_START <= hour <= LUNCH_END and not lunch_suggested:
        flags.append("lunch_time")
        if trigger == "periodic":
            trigger = "lunch_time"

    return {
        "cadence_minutes": cadence,
        "trigger": trigger,
        "off_route": off_route,
        "remaining_km": route["remaining_km"],
        "km_from_start": route["km_from_start"],
        "speed": speed,
        "next_town": next_town,
        "next_town_km_ahead": next_town_km,
        "lunch_suggested": lunch_suggested,
        "context": {
            "reason": trigger,
            "latitude": lat,
            "longitude": lon,
            "speed_kmh": round(speed, 1),
            "cadence_minutes": cadence,
            "remaining_km": route["remaining_km"],
            "km_from_start": route["km_from_start"],
            "off_route_m": round(route["dist_m"], 0),
            "next_town": next_town,
            "next_town_km_ahead": next_town_km,
            "flags": flags,
            "live_location_expires_at": None,
        },
    }


def skip() -> None:
    print(json.dumps({"wakeAgent": False}))


def main(now: float | None = None) -> None:
    current_time = time.time() if now is None else now
    assistant_state = load_json(STATE, {})
    tour_info = assistant_state.get("tour", {})
    gpx_value = tour_info.get("gpx", "")
    gpx_path = Path(gpx_value) if gpx_value else None
    track = parse_gpx(gpx_path) if gpx_path and gpx_path.exists() else []
    towns = extract_towns_from_gpx(track)

    snapshot = load_json(SNAPSHOT, {"locations": []})
    candidates = [
        item
        for item in snapshot.get("locations", [])
        if str(item.get("chat_id", "")) == CHAT_ID
        and float(item.get("expires_at", 0) or 0) > current_time
    ]
    gate_state = load_json(GATE_STATE, {})

    if not candidates:
        if gate_state.get("active"):
            save_json(GATE_STATE, {"active": False, "stopped_at": current_time})
        skip()
        return

    current = max(candidates, key=lambda item: float(item.get("updated_at", 0) or 0))
    lat = float(current["lat"])
    lon = float(current["lon"])
    message_id = str(current.get("message_id", ""))
    expires_at = current.get("expires_at")
    new_share = not gate_state.get("active") or str(gate_state.get("message_id", "")) != message_id

    if track:
        dynamic = compute_dynamic_params(lat, lon, current_time, gate_state, track, towns)
    else:
        speed = estimate_speed_kmh(lat, lon, current_time, gate_state)
        cadence = cadence_for(speed, 0)
        dynamic = {
            "cadence_minutes": cadence,
            "trigger": "periodic",
            "off_route": False,
            "remaining_km": 0,
            "km_from_start": 0,
            "speed": speed,
            "next_town": None,
            "next_town_km_ahead": None,
            "lunch_suggested": bool(gate_state.get("lunch_suggested", False)),
            "context": {
                "reason": "periodic",
                "latitude": lat,
                "longitude": lon,
                "speed_kmh": round(speed, 1),
                "cadence_minutes": cadence,
                "remaining_km": 0,
                "km_from_start": 0,
                "off_route_m": 0,
                "next_town": None,
                "next_town_km_ahead": None,
                "flags": ["no_route"],
                "live_location_expires_at": expires_at,
            },
        }

    moved_m = 0.0
    if gate_state.get("last_wake_lat") is not None:
        moved_m = haversine_m(
            float(gate_state["last_wake_lat"]),
            float(gate_state["last_wake_lon"]),
            lat,
            lon,
        )

    last_wake = float(gate_state.get("last_wake_at", 0) or 0)
    cadence_seconds = dynamic["cadence_minutes"] * 60
    due_at = float(gate_state.get("next_due_at", last_wake + cadence_seconds) or 0)
    due = current_time >= due_at
    event_trigger = dynamic["trigger"] in {"off_route", "finish_approach", "lunch_time"}
    wake = new_share or moved_m >= MOVE_THRESHOLD_M or due or event_trigger

    next_state = dict(gate_state)
    next_state.update(
        {
            "active": True,
            "message_id": message_id,
            "last_position_lat": lat,
            "last_position_lon": lon,
            "last_position_time": current_time,
            "last_speed_kmh": round(dynamic["speed"], 1),
            "remaining_km": dynamic["remaining_km"],
            "km_from_start": dynamic["km_from_start"],
            "cadence_minutes": dynamic["cadence_minutes"],
            "next_due_at": last_wake + min(cadence_seconds, MAX_SILENCE_SECONDS),
            "off_route_active": dynamic["off_route"],
            "last_town": dynamic.get("next_town"),
            "last_town_km_ahead": dynamic.get("next_town_km_ahead"),
        }
    )

    if not wake:
        save_json(GATE_STATE, next_state)
        skip()
        return

    context = dynamic["context"]
    context["live_location_expires_at"] = expires_at
    if new_share:
        context["reason"] = "live_location_started"
    elif dynamic["trigger"] != "periodic":
        context["reason"] = dynamic["trigger"]
    elif moved_m >= MOVE_THRESHOLD_M:
        context["reason"] = "moved"
    else:
        context["reason"] = "check_in"

    event_last_sent = dict(gate_state.get("event_last_sent") or {})
    if context["reason"] in {"off_route", "finish_approach", "lunch_time"}:
        event_last_sent[context["reason"]] = current_time
    next_state.update(
        {
            "last_wake_lat": lat,
            "last_wake_lon": lon,
            "last_wake_at": current_time,
            "next_due_at": current_time
            + min(dynamic["cadence_minutes"] * 60, MAX_SILENCE_SECONDS),
            "last_trigger": context["reason"],
            "event_last_sent": event_last_sent,
            "lunch_suggested": dynamic["lunch_suggested"] or context["reason"] == "lunch_time",
        }
    )
    save_json(GATE_STATE, next_state)
    print(json.dumps({"wakeAgent": True, "context": context}))


if __name__ == "__main__":
    main()
