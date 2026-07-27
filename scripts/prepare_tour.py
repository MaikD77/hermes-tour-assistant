#!/usr/bin/env python3
"""Prepare the tour assistant for a new tour: reset state and generate prompt template.

Usage:
    python3 prepare_tour.py <komoot_tour_id> \\
        --tour-name "My Tour" \\
        --distance-km 85 \\
        --elevation 1200 \\
        --sport racebike

This script handles FILE operations. The agent must provide tour metadata.
Outputs a JSON dict with the new state file path and a prompt template.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

STATE_DIR = Path.home() / ".hermes/state"

TEMPLATE = """# Live Tour Assistant — {{TOUR_NAME}}

## Function

You are a quiet, route-aware assistant during an active Telegram live-location share.
Your task: identify the route, send a startup message, and report only new,
actionable events with dynamic cadence.

## Skills

Load: `outdoor-tour-assistant` (orchestration), `live-location-nearby` (water/POI),
`maps` (geocoding, OSM), and `komoot` (tour data).

## Workflow

### 1. Route Identification (on first wake or when no tour verified)

If `tour.verified` is `false` or no tour ID set:

1. Read current position from `telegram_live_locations.json`
2. List planned tours via Komoot (`mcp__komoot__list_tours`)
3. Compare position with each tour's geometry (<500 m tolerance)
4. Set `tour.verified = true`, save `tour.id`, `tour.name`, `tour.gpx`
5. Download GPX via `mcp__komoot__download_tour_gpx`
6. Send startup message

### 2. Startup Notice (once per tour)

- **Tour assistant active.**
- **Matched route:** tour name + ID
- **Cadence:** dynamic 3–15 min depending on speed, town proximity, finish
- **Scope:** deviation, hazards, weather, supply, exceptional POIs
- **Correction question:** correct route or scope?
- **No match:** "No route detected — location-based assistance."

### 3. Each Check

- Reverse-geocode for settlement name
- Snap to GPX for route progress
- Forward corridor 3–10 km: deviation, hazards, weather, supply, POIs
- Deduplicate against `reported_facts`
- Nothing new → `[SILENT]`

### 4. Water/Supply Search

Primary: `python3 skills/live-location-nearby/scripts/find_water.py LAT LON --radius 5000 --limit 8`
Fallback: `maps` skill for supermarkets/gas stations.

### 5. Response Format

```markdown
**<Action or place>** – <distance ahead>
<Why it matters> · <on route / detour>
[Google Maps](https://www.google.com/maps/dir/?api=1&origin=LAT,LON&destination=DEST_LAT,DEST_LON&travelmode=bicycling)
```

### 6. Persistence

After each check: atomically save position, weather, progress, reported_facts.

### 7. End Detection

`remaining_km < 2`: safety-only checks, no shutdown message.

### 8. No execute_code in Cron

Use `terminal` with `python3 -c "..."` instead.

## Embedded Tour Context

{{TOUR_EMBED}}
"""


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def parse_gpx_info(gpx_path: Path) -> tuple[list[dict], int]:
    """Parse GPX: return (town_markers, point_count)."""
    if not gpx_path.exists():
        return [], 0
    try:
        text = gpx_path.read_text(encoding="utf-8")
    except OSError:
        return [], 0
    pts = re.findall(r'<trkpt lat="([\d.-]+)" lon="([\d.-]+)"', text)
    if not pts:
        return [], 0

    cum = 0.0
    prev = None
    markers = []
    last_marker_km = -5.0

    for lat_s, lon_s in pts:
        lat, lon = float(lat_s), float(lon_s)
        if prev:
            cum += haversine_km(prev[0], prev[1], lat, lon)
        prev = (lat, lon)
        if cum - last_marker_km >= 5.0:
            markers.append({"lat": round(lat, 6), "lon": round(lon, 6), "km": round(cum, 1)})
            last_marker_km = cum

    return markers, len(pts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare tour assistant for a new tour")
    parser.add_argument("tour_id", type=int, help="Komoot tour ID")
    parser.add_argument("--tour-name", type=str, default="", help="Tour name")
    parser.add_argument("--distance-km", type=float, default=0, help="Tour distance in km")
    parser.add_argument("--elevation", type=int, default=0, help="Elevation gain in meters")
    parser.add_argument("--sport", type=str, default="racebike", help="Sport type")
    parser.add_argument("--gpx-file", type=str, default=None, help="Path to GPX file")
    args = parser.parse_args()

    tour_id = args.tour_id
    gpx_path = args.gpx_file or str(STATE_DIR / f"current-tour-{tour_id}.gpx")

    # Reset assistant state
    state = {
        "chat_id": "CHANGE_ME",  # Will be updated by the agent on first wake
        "share_message_id": None,
        "startup_notified": False,
        "started_at": None,
        "last_position": None,
        "last_check_at": None,
        "tour": {
            "id": tour_id,
            "name": args.tour_name or None,
            "verified": False,
            "gpx": gpx_path,
        },
        "route_progress": None,
        "reported_facts": [],
        "weather": None,
    }

    state_path = STATE_DIR / "live_tour_assistant.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    # Parse GPX
    markers, point_count = parse_gpx_info(Path(gpx_path))
    town_lines = "\n".join(f"    ~{m['km']} km: waypoint" for m in markers)

    # Build embed
    embed_parts = []
    if args.tour_name:
        embed_parts.append(f"**Tour:** {args.tour_name}")
    if args.distance_km:
        embed_parts.append(f"**{round(args.distance_km)} km** · {args.elevation} Hm · {args.sport}")
    embed_parts.append(f"**GPX points:** {point_count}")
    if markers:
        embed_parts.append(f"**Route markers (~every 5 km):**")
        embed_parts.append(town_lines)
    embed_parts.append(f"**GPX:** {gpx_path}")

    tour_embed = "\n".join(embed_parts)
    prompt = TEMPLATE.replace("{{TOUR_NAME}}", args.tour_name or f"Tour #{tour_id}")
    prompt = prompt.replace("{{TOUR_EMBED}}", tour_embed)

    result = {
        "status": "ok",
        "state_path": str(state_path),
        "gpx_path": gpx_path,
        "tour": {
            "id": tour_id,
            "name": args.tour_name,
            "distance_km": args.distance_km,
            "elevation": args.elevation,
            "sport": args.sport,
            "gpx_point_count": point_count,
            "town_markers": len(markers),
        },
        "prompt": prompt,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())