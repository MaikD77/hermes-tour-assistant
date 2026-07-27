#!/usr/bin/env python3
"""Find potable water and practical water-buying fallbacks via OpenStreetMap Overpass API."""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.parse
import urllib.request
from typing import Any

MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def overpass_query(lat: float, lon: float, radius: int) -> dict[str, Any]:
    """Query Overpass API for water sources and purchase fallbacks."""
    query = f"""[out:json][timeout:25];
(
  nwr(around:{radius},{lat},{lon})[amenity=drinking_water];
  nwr(around:{radius},{lat},{lon})[amenity=fountain];
  nwr(around:{radius},{lat},{lon})[man_made=water_tap];
  nwr(around:{radius},{lat},{lon})[shop=convenience];
  nwr(around:{radius},{lat},{lon})[shop=supermarket];
  nwr(around:{radius},{lat},{lon})[amenity=fuel];
);
out center tags;"""
    body = urllib.parse.urlencode({"data": query}).encode()
    errors: list[str] = []
    for endpoint in MIRRORS:
        try:
            req = urllib.request.Request(
                endpoint,
                data=body,
                headers={"User-Agent": "Hermes-live-location-nearby/1.0"},
            )
            with urllib.request.urlopen(req, timeout=35) as response:
                return json.load(response)
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("; ".join(errors))


def coords(element: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center") or {}
    if "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def classify(tags: dict[str, Any]) -> tuple[int, str, str]:
    """Classify a POI by water potability. Returns (rank, status, label)."""
    amenity = tags.get("amenity", "")
    shop = tags.get("shop", "")
    potable = str(tags.get("potable", "")).lower()
    drinking = str(tags.get("drinking_water", "")).lower()

    if amenity == "drinking_water":
        return 0, "confirmed", "Trinkwasser bestätigt"
    if potable == "yes" or drinking == "yes":
        return 1, "confirmed", "Trinkwasser bestätigt"
    if amenity == "fountain" or tags.get("man_made") == "water_tap":
        return 2, "uncertain", "Trinkbarkeit unklar"
    if shop in {"convenience", "supermarket"} or amenity == "fuel":
        return 3, "purchase", "Kaufmöglichkeit"
    return 9, "other", "Unklar"


def format_name(tags: dict[str, Any], status: str) -> str:
    if tags.get("name"):
        return str(tags["name"])
    if status == "confirmed":
        return "Öffentliche Trinkwasserstelle"
    if tags.get("amenity") == "fountain":
        return "Brunnen"
    if tags.get("man_made") == "water_tap":
        return "Wasserhahn"
    if tags.get("shop") == "supermarket":
        return "Supermarkt"
    if tags.get("shop") == "convenience":
        return "Kiosk/Minimarkt"
    if tags.get("amenity") == "fuel":
        return "Tankstelle"
    return "Wasseroption"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("lat", type=float)
    parser.add_argument("lon", type=float)
    parser.add_argument("--radius", type=int, default=5000)
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()

    try:
        data = overpass_query(args.lat, args.lon, args.radius)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    results: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for element in data.get("elements", []):
        point = coords(element)
        if point is None:
            continue
        lat, lon = point
        tags = element.get("tags") or {}
        rank, status, label = classify(tags)
        if rank == 9:
            continue
        key = (round(lat * 100000), round(lon * 100000), format_name(tags, status))
        if key in seen:
            continue
        seen.add(key)
        distance = round(haversine(args.lat, args.lon, lat, lon))
        destination = f"{lat},{lon}"
        origin = f"{args.lat},{args.lon}"
        results.append({
            "name": format_name(tags, status),
            "status": status,
            "label": label,
            "distance_m": distance,
            "lat": lat,
            "lon": lon,
            "opening_hours": tags.get("opening_hours"),
            "brand": tags.get("brand"),
            "osm_tags": {k: tags[k] for k in ("amenity", "shop", "drinking_water", "potable", "man_made") if k in tags},
            "maps_url": "https://www.google.com/maps/search/?api=1&query=" + destination,
            "directions_url": "https://www.google.com/maps/dir/?api=1&origin=" + origin + "&destination=" + destination + "&travelmode=bicycling",
            "_rank": rank,
        })

    results.sort(key=lambda item: (item["_rank"], item["distance_m"]))

    limit = max(1, args.limit)
    selected = results[:limit]
    # Ensure at least one purchase fallback in results
    if limit >= 2 and not any(item["status"] == "purchase" for item in selected):
        fallback = next((item for item in results if item["status"] == "purchase"), None)
        if fallback is not None:
            selected[-1] = fallback
    for item in results:
        item.pop("_rank", None)

    print(json.dumps({
        "ok": True,
        "origin": {"lat": args.lat, "lon": args.lon},
        "radius_m": args.radius,
        "count": len(results),
        "results": selected,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())