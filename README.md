# Hermes Tour Assistant 🚴‍♂️

A **quiet, route-aware, intelligent cycling/walking assistant** for the [Hermes Agent](https://hermes-agent.nousresearch.com) platform.  
Uses Telegram live-location streams to provide real-time tour support — without spamming you.

> **Silence is the default.** Only new, relevant events trigger notifications.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| **🧭 Route Identification** | Automatically matches your live location against Komoot planned tours by geometry — no manual setup per ride |
| **⏱️ Dynamic Cadence** | 3 min at >20 km/h · 5 min cruising · 15 min during breaks · 2 min in the final 5 km |
| **🏘️ Town Approach** | Gets woken when you approach a settlement <3 km ahead — perfect for supply/lunch tips |
| **⚠️ Hazard Verification** | Doesn't just flag every Overpass hit — verifies obstacles against actual GPX route geometry |
| **🌤️ Smart Weather** | Only reports on ≥20 km/h gust increase, new rain, or severe warnings — not boring stable weather |
| **💧 Supply-Gap (Sunday/PH)** | Knows German stores close Sundays, searches for 24/7 vending automats and gas stations |
| **🔇 [SILENT] Mode** | No message = nothing new to report. No chat spam. |
| **🧠 Deduplication** | `reported_facts` key system prevents repeating the same alert every 5 minutes |
| **📱 Mobile-Optimized** | Short bullets + tappable Google Maps navigation links |
| **🎯 On-Route Focus** | Searches *forward along the route*, not radially — results are ahead, not behind you |

---

## 🏗️ Architecture

```
Telegram Live Location (every few seconds)
       │
       ▼
  Telegram Adapter (caches edited live-location updates)
       │
       ▼
  Cron (every 5 min) ──► live_tour_gate.py
       │                        │
       │              ┌─────────┴──────────┐
       │              │                    │
       │       telegram_live_        current-tour-
       │       locations.json        {TOUR_ID}.gpx
       │
       ├─ wakeAgent: false → Silence (no LLM call)
       └─ wakeAgent: true  → LLM with Context Object
                │
                ▼
     outdoor-tour-assistant (Skill)
     live-location-nearby + maps + komoot (sub-skills)
                │
                ├─ Startup notice (once per tour)
                ├─ Forward corridor 3–10 km scan
                ├─ Weather check (web_search)
                ├─ Hazard verification (GPX + OSM)
                ├─ Supply-gap check (Sunday/PH logic)
                ├─ Dedup against reported_facts
                │
                ├─ Nothing new → [SILENT]
                └─ Something new → Short alert + Maps link
```

---

## 📦 Files

```
hermes-tour-assistant/
├── README.md                       # This file
├── LICENSE                         # MIT
├── SKILL.md                        # Main orchestrating skill
├── cron-prompt-generic.md          # Generic cron job prompt (no per-tour editing)
│
├── scripts/
│   ├── live_tour_gate.py           # Gate script — decides WHEN to wake the LLM
│   └── prepare_tour.py             # Tour preparation — resets state, generates prompt
│
├── references/
│   ├── dynamic-gate.md             # Full gate script architecture & design
│   ├── weather-monitoring.md       # Weather search templates & dedup patterns
│   └── forward-hazard-verification.md  # OSM hazard verification recipe
│
└── skills/
    └── live-location-nearby/
        ├── SKILL.md                # Live-location + nearby POI search sub-skill
        └── scripts/
            └── find_water.py       # Overpass-based drinking water finder
```

---

## 🔧 Installation

### Prerequisites

- [Hermes Agent](https://hermes-agent.nousresearch.com) (running on macOS/Linux)
- Telegram account with Hermes Telegram adapter configured
- Komoot account with some planned tours

### 1. Clone or copy

```bash
git clone https://github.com/YOUR_USER/hermes-tour-assistant.git ~/.hermes/skills/productivity/hermes-tour-assistant
# OR manually copy the files to ~/.hermes/skills/productivity/outdoor-tour-assistant/
```

### 2. Install gate script

```bash
cp scripts/live_tour_gate.py ~/.hermes/scripts/
cp scripts/prepare_tour.py ~/.hermes/scripts/
```

### 3. Configure environment

Set your Telegram chat ID in the gate script or as an environment variable:

```bash
# Option A: Edit ~/.hermes/scripts/live_tour_gate.py
# Change CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"

# Option B: Set environment variable
export HERMES_TOUR_CHAT_ID="YOUR_TELEGRAM_CHAT_ID"
```

### 4. Create cron job

```yaml
job_id: tour-assistant
name: Live Tour Assistant
schedule: every 5m
script: live_tour_gate.py
workdir: /home/you
deliver: origin
skills:
  - outdoor-tour-assistant
  - live-location-nearby
  - maps
  - komoot
prompt: |
  # Live-Tourassistent — Generischer Modus
  ... (paste contents of cron-prompt-generic.md)
```

### 5. Create state files

```bash
mkdir -p ~/.hermes/state
cat > ~/.hermes/state/live_tour_assistant.json <<'EOF'
{
  "chat_id": "YOUR_TELEGRAM_CHAT_ID",
  "share_message_id": null,
  "startup_notified": false,
  "started_at": null,
  "last_position": null,
  "last_check_at": null,
  "tour": {
    "id": null,
    "name": null,
    "verified": false,
    "gpx": null
  },
  "route_progress": null,
  "reported_facts": [],
  "weather": null
}
EOF

echo '{"active": false}' > ~/.hermes/state/live_tour_gate.json
```

---

## 🚀 Quick Start

1. **Plan a tour in Komoot** (any route, any distance)
2. **Start Telegram live location** in your chat with Hermes
3. The gate script detects the new share and wakes the LLM
4. The LLM scans Komoot planned tours, matches your position by geometry, downloads the GPX
5. You receive a **startup notice** with route confirmation + a correction question
6. Then **silence** — until something worth reporting happens

**That's it.** No per-tour configuration. No script editing. No prompt rewriting.

---

## 📋 How It Works In Detail

### Gate Script (`live_tour_gate.py`)

Pure Python — no LLM tokens burned on wake decisions. Runs every 5 minutes via cron.

**Dynamic cadence:**

| Speed | Cadence | Why |
|-------|---------|-----|
| >20 km/h | 3 min | Fast riding, you cover ground quickly |
| 5–20 km/h | 5 min | Normal cruising |
| <5 km/h | 15 min | Paused, walking, in town |
| <5 km remaining | 2 min | Final approach |

**Trigger types:**
- `live_location_started` — new share detected
- `moved` — movement >350 m since last wake
- `off_route` — position >150 m from GPX track
- `town_approach` — next town <3 km ahead
- `lunch_time` — 10:00–14:00, no lunch reminder sent
- `check_in` — maximum silence interval (15 min) reached
- `finish_approach` — <5 km remaining

**Graceful degradation:** If no GPX is configured, the gate falls back to a simple 5-minute timer. The agent will then announce "no route detected" and provide location-based assistance.

### Main Skill (`SKILL.md`)

Orchestrates everything the agent does after being woken:

1. **Route Identification** — scans Komoot planned tours, matches by geometry (<500 m tolerance), downloads GPX
2. **Forward Corridor Search** — searches along the route, not radially. Verifies bench/viewpoint/POI quality.
3. **Hazard Verification** — doesn't trust Overpass bounding-box hits alone. Verifies against GPX route geometry.
4. **Weather Monitoring** — `web_search` based (no JS-heavy portal scraping). Only reports significant changes.
5. **Supply-Gap Check** — German Sunday/PH logic with vending machine fallback. `find_water.py` for drinking water.
6. **Mobile Response Format** — short bullets + tappable Google Maps navigation links.

### Preparation Script (`prepare_tour.py`)

Automates tour setup when you want to pre-configure:

```bash
python3 ~/.hermes/scripts/prepare_tour.py 123456789 \
  --tour-name "My Awesome Ride" \
  --distance-km 85 \
  --elevation 1200 \
  --sport racebike
```

Outputs a complete cron prompt template ready to paste.

---

## 🧠 State Files

### `live_tour_assistant.json` (main state)

```json
{
  "chat_id": "YOUR_CHAT_ID",
  "startup_notified": false,
  "tour": {
    "id": 123456789,
    "name": "Alps Epic 2026",
    "verified": false,
    "gpx": "/home/you/.hermes/state/current-tour-123456789.gpx"
  },
  "route_progress": {
    "km_from_start": 22.7,
    "remaining_km": 54.7
  },
  "reported_facts": ["startup_2026-07-27", "weather_gusts_...", "town_approach_..."],
  "weather": {
    "checked_at": 1785163116,
    "temperature_c": "18-31",
    "precipitation_mm": 0,
    "wind_kmh": 5,
    "gusts_current_kmh": 34
  }
}
```

### `telegram_live_locations.json` (location cache)

Cached by the Telegram adapter — one entry per active live share:

```json
{
  "locations": [{
    "chat_id": "YOUR_CHAT_ID",
    "lat": 49.7012,
    "lon": 9.2569,
    "updated_at": 1785140000.0,
    "expires_at": 3932552535.0
  }]
}
```

### `live_tour_gate.json` (gate internal state)

```json
{
  "active": true,
  "last_wake_lat": 49.7012,
  "last_wake_lon": 9.2569,
  "last_speed_kmh": 12.4,
  "cadence_minutes": 5,
  "remaining_km": 54.7
}
```

---

## ⚠️ Common Pitfalls

- **Don't scrape weather portals** — wetter.com, DWD, and AccuWeather use JS/cookie walls. Use `web_search` instead.
- **No `execute_code` in cron context** — cron can't call `execute_code`. Use `terminal` with `python3 -c "..."`.
- **Verify amenities before recommending coordinates** — a GPX track coordinate may be a road, not a bench. Check via Komoot highlights or OSM tags.
- **Sunday supply checks** — German `opening_hours` with `PH off` means closed Sunday. Always check before recommending.
- **State files must be written atomically** — use `tempfile` + `os.replace` to prevent corruption.

---

## 🔒 Privacy

This assistant:
- Uses your **Telegram live location** (shared by you, only while riding)
- Reads your **Komoot planned tours** (to match routes)
- Queries **OpenStreetMap Overpass API** (for POIs, hazards, water sources)
- Queries **web search** (for weather data)

No data is stored long-term. State files are local only. No analytics, no tracking, no third-party sharing.

---

## 📄 License

MIT — do whatever you want with it. Attribution appreciated but not required.

---

## 🤝 Contributing

PRs welcome! Ideas for improvement:
- Add hiking/walking mode (different cadence defaults)
- Add rain radar image integration
- Support for other live-location platforms (WhatsApp, GPS trackers)
- Multi-language support

---

*Built with ❤️ for long days in the saddle.*