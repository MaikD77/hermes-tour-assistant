# Dynamic Gate Script — live_tour_gate.py

## Purpose

Replace a fixed-interval cron wake with a context-aware gate that decides **whether** and **how often** to wake the assistant based on the rider's actual state.

## Key Improvement (27.07.2026)

The gate script no longer has hardcoded GPX paths or town lists. It reads:
- **GPX path** dynamically from `live_tour_assistant.json` (`tour.gpx` field)
- **Towns** are extracted automatically from the GPX track every ~5 km

This means the gate script works for **any tour without editing**. When no GPX exists,
it degrades gracefully to a simple 5-min timer-based wake pattern.

## Architecture

```
Cron (every 5 min) ──► live_tour_gate.py ──► { wakeAgent: true/false, context: {...} }
                                │
                    ┌───────────┴───────────┐
                    │                       │
            telegram_live_           current-tour-
            locations.json          {TOUR_ID}.gpx
```

The cron fires every 5 minutes. The gate reads:
- **`telegram_live_locations.json`** — latest snapshot from the Telegram adapter (all active live shares)
- **`current-tour-{TOUR_ID}.gpx`** — GPX track for the matched tour, pre-downloaded via Komoot MCP

It computes dynamic parameters and returns `wakeAgent: false` (skip) or `wakeAgent: true` with a context object.

## State File

The gate maintains its own state at `~/.hermes/state/live_tour_gate.json`:

```json
{
  "active": true,
  "message_id": "12345",
  "last_wake_lat": 49.7012,
  "last_wake_lon": 9.2569,
  "last_wake_at": 1785144000.0,
  "last_position_lat": 49.7012,
  "last_position_lon": 9.2569,
  "last_position_time": 1785144000.0,
  "last_speed_kmh": 12.4,
  "remaining_km": 54.7,
  "km_from_start": 22.7,
  "last_trigger": "moved",
  "cadence_minutes": 5,
  "last_town": "Klingenberg am Main",
  "last_town_km_ahead": 2.3,
  "lunch_suggested": false
}
```

## Dynamic Cadence Computation

### Speed-based Cadence

Estimated from the last two recorded positions (Haversine distance / time delta):

| Speed | Cadence | Trigger reason |
|-------|---------|----------------|
| >20 km/h | 3 min | Fast riding, frequent updates |
| 5–20 km/h | 5 min | Normal cruising |
| <5 km/h | 15 min | Paused, walking, or in town |

### Town Proximity

The gate snaps the rider's position to the GPX, computes `km_from_start`, and checks which town is next ahead (`>0.5 km` and `<3 km`):

- Town <3 km ahead → `town_approach` flag in context
- Town <1 km ahead → `town_imminent` flag
- Sets `trigger: "town_approach"` when <1 km

### Finish Approach

When `remaining_km < 5`:
- Cadence forced to 2 min (overrides speed-based cadence)
- `finish_approach` flag added

### Off-Route Detection

When `off_route_m > 150` (distance from nearest GPX trackpoint):
- `off_route` flag added
- `trigger: "off_route"` — immediate wake regardless of movement threshold
- Still uses speed-based cadence for the *next* wake after the off-route alert

### Lunch Timer

When `10:00 <= local_hour <= 14:00` and `lunch_suggested` is `false`:
- If last wake was >30 min ago → `trigger: "lunch_time"`
- `lunch_suggested` is persisted so the reminder fires only once per tour

### Max Silence Fallback

If none of the above triggers fire and the rider hasn't moved >350 m, the gate still wakes after `cadence_minutes * 60 + 30` seconds, capped at 15 minutes. This prevents the agent from going silent for too long.

## Wake Decision Logic

The gate wakes the agent if ANY of these are true:

1. **New share** — `message_id` changed or `active` was false
2. **Off-route** — `off_route_m > 150`
3. **Movement** — `moved_m >= 350` since last wake
4. **Max silence** — `elapsed >= max_silence` (dynamic based on cadence)
5. **Town imminent** — trigger is `town_approach`
6. **Lunch time** — trigger is `lunch_time`
7. **Finish approach** — trigger is `finish_approach`

## Context Object

When `wakeAgent: true`, the gate returns:

```json
{
  "wakeAgent": true,
  "context": {
    "reason": "moved",
    "latitude": 49.86,
    "longitude": 9.155,
    "speed_kmh": 12.4,
    "cadence_minutes": 5,
    "remaining_km": 54.7,
    "km_from_start": 22.7,
    "off_route_m": 37,
    "next_town": "Klingenberg am Main",
    "next_town_km_ahead": 2.3,
    "flags": ["town_approach"],
    "live_location_expires_at": 1785145000.0
  }
}
```

## Towns Configuration

Towns are extracted **automatically from the GPX track** every ~5 km. No manual town list is needed. The gate calls `extract_towns_from_gpx()` which samples waypoints from the track.

For tours where specific named towns are important (e.g. for town-approach triggers), the agent should update `assistant_state.towns` in `live_tour_assistant.json` with explicit entries. Otherwise, the auto-extracted waypoints are sufficient for cadence decisions.

## Test Scenarios

| Scenario | Position | Speed | Expected |
|----------|----------|-------|----------|
| Start (Miltenberg) | 49.7012, 9.2570 | 0 km/h | Wake with `live_location_started`, cadence 15 min |
| Cruising (Wörth→Erlenbach) | 49.86, 9.155 | 12 km/h | `moved`, cadence 5 min, town_approach for Klingenberg |
| Fast rider (Sulzbach) | 49.925, 9.012 | 48 km/h | `moved`, cadence 3 min |
| Finish approach (Fechenheim) | 50.105, 8.670 | 9 km/h | `off_route`, cadence 2 min, finish_approach flag |