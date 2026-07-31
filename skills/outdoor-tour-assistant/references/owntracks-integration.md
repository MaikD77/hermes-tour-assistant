# OwnTracks Location Integration

The `live_tour_gate.py` (cron gate) now supports two location sources:

## Location priority

1. **Telegram live-location** (primary) — sourced from `telegram_live_locations.json` snapshot
2. **OwnTracks** (fallback) — sourced from `GET http://127.0.0.1:9090/location`

When no Telegram live-location is active, the gate queries the OwnTracks receiver and creates a `LocationSample` with `source="owntracks"`. The runtime treats it identically to a Telegram location.

## Gate behavior

```python
# In live_tour_gate.py main():
sample = select_location(...)  # try Telegram first
if sample is None:
    sample = fetch_owntracks_location(...)  # fallback to OwnTracks
```

## OwnTracks receiver

- Runs on `localhost:9090` (FastAPI, started by `owntracks-start.sh`)
- `GET /location` returns `{latitude, longitude, observed_at, stale, ...}`
- Stale threshold: 300 seconds (configurable via `HERMES_OWNTRACKS_STALE_SECONDS`)
- Location ages > 300s are ignored by the gate
- Requires iPhone to be connected via VPN (Fritzbox) or Tailscale

## Configuration

```bash
# Optional env vars for the cron job:
HERMES_OWNTRACKS_URL=http://127.0.0.1:9090/location  # default
```

## Verification

```bash
curl -s http://127.0.0.1:9090/location
# → {"result":"ok","latitude":50.987,"longitude":11.023,"stale":false,...}
```
## Source abstraction (v1.5)

OwnTracks is accessed only through `OwnTracksLocationSource` and the receiver's local HTTP repository interface. The shared core has no knowledge of the SQLite schema. Resolver default is `owntracks,telegram`; use `HERMES_LOCATION_SOURCE_ORDER=telegram,owntracks` during legacy migration.
