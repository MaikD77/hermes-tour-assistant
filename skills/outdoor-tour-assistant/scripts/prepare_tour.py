#!/usr/bin/env python3
"""Safely preconfigure a route through the canonical state-v3 runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from route_engine import parse_gpx  # noqa: E402
from tour_runtime import TourRuntime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a tour for the next live session")
    parser.add_argument("tour_id", help="Route identifier at the configured provider")
    parser.add_argument("--tour-name", default="", help="Human-readable route name")
    parser.add_argument("--provider", default="komoot", help="Route provider name")
    parser.add_argument("--gpx-file", type=Path, help="Validated GPX source to copy privately")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(
            os.environ.get(
                "HERMES_TOUR_STATE_DIR",
                str(Path.home() / ".hermes" / "state"),
            )
        ),
    )
    parser.add_argument("--distance-km", type=float, default=0)
    parser.add_argument("--elevation", type=int, default=0)
    parser.add_argument("--sport", choices=("cycling", "walking"), default="cycling")
    args = parser.parse_args()

    state_path = args.state_dir.expanduser() / "live_tour_assistant.json"
    runtime = TourRuntime(state_path)
    route_name = args.tour_name.strip() or f"Tour #{args.tour_id}"
    point_count = 0
    route_distance_m = 0.0
    if args.gpx_file is not None:
        source_points = parse_gpx(args.gpx_file.expanduser())
        point_count = len(source_points)
        route_distance_m = source_points[-1].cumulative_m
        state = runtime.prepare_route(
            provider=args.provider,
            route_id=args.tour_id,
            name=route_name,
            gpx_path=args.gpx_file,
        )
        status = "prepared"
    else:
        state = runtime.prepare_pending_route(
            provider=args.provider,
            route_id=args.tour_id,
            name=route_name,
        )
        status = "needs_gpx"

    print(
        json.dumps(
            {
                "status": status,
                "state_path": str(state_path),
                "schema_version": state["schema_version"],
                "route": {
                    "id": args.tour_id,
                    "name": route_name,
                    "provider": args.provider,
                    "verified": state["route"]["verified"],
                    "gpx_path": state["route"]["gpx_path"],
                    "gpx_point_count": point_count,
                    "calculated_distance_km": round(route_distance_m / 1000, 1),
                    "declared_distance_km": args.distance_km,
                    "elevation_m": args.elevation,
                    "activity": args.sport,
                },
                "next_step": (
                    "Start the one-minute skill-backed cron job and share a live location."
                    if status == "prepared"
                    else "Download and validate the route GPX before it can be verified."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
