---
name: outdoor-tour-assistant
description: "Operate a live, route-aware cycling or walking assistant from Telegram location streams: identify the active route, announce monitoring scope, and surface only useful hazards, supplies, weather, or exceptional places ahead."
version: 1.1.0
author: Hermes Agent Community
license: MIT
metadata:
  hermes:
    tags: [live-location, cycling, hiking, route-awareness, komoot, nearby, monitoring]
    related_skills: [live-location-nearby, maps, komoot]
---

# Outdoor Tour Assistant

## Purpose

Provide quiet, route-aware support during a moving Telegram live-location share. Combine the newest location, a confidently matched planned route, and current external data. Prioritize what lies **ahead on the route**, not what is merely nearby in a circle.

## Activation and Startup Message

When a new live-location share activates the assistant, the **first response must be the startup notice**. Never begin with a generic "What should I find nearby?" question.

The notice must state, in no more than six short bullets:

1. **Tour assistant active.**
2. **Matched route:** verified Komoot tour name and link/ID; otherwise `no route confidently detected — location-based assistance`.
3. **Cadence:** dynamic — every 3–15 minutes depending on speed, with additional triggers for town approach, off-route, lunch time, and finish approach.
4. **Scope:** route deviation/wrong direction, hazards or difficult passages, acute weather, supply gaps, and exceptional places directly ahead.
5. **Trigger types:** automatically at movement, town approach, lunch time, finish approach, or off-route deviation.
6. **Correction option:** invite a short correction of the route or scope.

After startup, silence is the default. Report only new, actionable findings.

## Route Identification

1. Read the newest valid location; latest pin wins.
2. List plausible current planned tours via Komoot API.
3. Compare the pin with actual tour geometry — not title or date alone.
4. Require both:
   - a close geometry match (typically <500 m); and
   - a meaningful forward section from the matched position.
5. Resolve loops and repeated coordinates using movement direction or recent pins. If direction is unresolved, state that rather than inventing it.
6. Cache the verified tour ID, GPX track, and route geometry for subsequent checks.

## Forward-Corridor Search

For requests such as "in den nächsten 5–10 km ein Aussichtspunkt" (a viewpoint in the next 5–10 km) or "a bench with a view to pause":

1. Snap the newest pin to the route and determine forward travel direction.
2. Build the requested **along-route distance window**, then search a narrow corridor around that forward segment.
3. Retrieve candidate POIs from map/community sources — use Komoot highlights, OSM, and web search.
4. For every candidate, verify:
   - it is ahead, not already passed;
   - along-route distance fits the requested window;
   - distance from the route corridor;
   - access type and likely bike/walk suitability;
   - the claimed quality is supported by a source, description, ratings, or photos.
5. **For bench/rest-spot requests specifically:** search for Komoot highlights with `facilities` or `viewpoint` categories, web search for bench-related terms near the target town, and OSM amenities tagged `bench`, `picnic_table`, or `leisure=picnic_table`. The route corridor itself (GPX track) may run on a road set back from the riverbank — verify the bench is on or directly adjacent to the track, not at a nearby coordinate that is on a different road.
6. Rank direct-on-route options above detours. A famous place behind the rider is not a valid answer.
7. If there is no strong direct hit, say so briefly and offer the best detour with its extra distance and ascent/access caveat.
8. Return a tappable Google Maps directions link from the newest pin, using the verified bench/spot coordinates as the destination.

## Route-Hazard Verification

For mapped hazards such as stairs, fords, closures, `bicycle=no`, rough surfaces, or mandatory dismounts, do not alert from a rectangular Overpass hit alone:

1. Query only the forward 3–10 km route bounding box for hazard tags.
2. Snap every candidate to the **forward GPX segment** and compute both along-route distance and perpendicular route offset.
3. Discard candidates behind the rider or clearly outside the corridor. Treat a nearby way center as a lead, not proof that the route uses that way.
4. For close candidates, fetch the exact OSM way geometry and tags. If Overpass is slow or unavailable for a known way ID, use the read-only OSM API endpoint `/api/0.6/way/{id}/full.json` as a targeted fallback.
5. Compare the hazard geometry with adjacent GPX points/segments. Alert only when the track intersects it or passes close enough that the obstacle is operationally credible; otherwise mark uncertainty or stay silent.
6. Put the action first and preserve concrete evidence such as step count, ramp availability, surface, access, and `bicycle=dismount`. Avoid vague danger language.

A reproducible technical recipe is in `references/forward-hazard-verification.md`.

## Monitoring Loop

The assistant wakes via a gate script that checks for an active live location and computes dynamic wake parameters. Each wake delivers a context object with `reason`, `speed_kmh`, `cadence_minutes`, `remaining_km`, `off_route_m`, `next_town`, and `flags`.

At each wake:

- Use the latest non-expired live-location snapshot from `telegram_live_locations.json`.
- Load and update the persistent assistant state at `live_tour_assistant.json`.
- First identify the current settlement via reverse geocoding — this tells you where the rider is *and* where they're heading before any search.
- Check the immediate forward corridor, typically 3–10 km.
- Detect material route deviation or wrong-way travel.
- Check only near-term hazards, difficult passages, weather, supply gaps, and genuinely exceptional POIs.
- Deduplicate previously reported facts.
- If nothing new is important, return exactly `[SILENT]` where the scheduler supports silent delivery.
- Stop automatically when the live-location share ends or expires; do not send a routine shutdown message.

### Dynamic Gate Triggers

The gate script wakes the agent with these reason types:

| reason | When | Agent action |
|--------|------|-------------|
| `live_location_started` | New share detected | Full startup message |
| `moved` | Rider moved >350 m since last wake | Standard monitoring check |
| `off_route` | Position >150 m from nearest GPX trackpoint | Verify and warn if genuinely off |
| `town_approach` | Next town <3 km ahead (or <1 km: `town_imminent` flag) | Mention upcoming town and POIs |
| `lunch_time` | 10:00–14:00, no lunch reminder sent yet | Suggest food in next town |
| `check_in` | Maximum silence interval reached (no movement, but must check) | Brief status summary |
| `finish_approach` | <5 km remaining | Final-approach info, tighten cadence |

The context also carries `cadence_minutes`: 3 min for fast riding (>20 km/h), 5 min for cruising, 15 min for slow/stopped, 2 min in the final 5 km.

See `references/dynamic-gate.md` for the full gate script design.

### Weather Monitoring

At each check, retrieve current conditions for the rider's immediate forward area:

1. Query the nearest town/settlement using web search.
2. Extract temperature, precipitation probability, wind speed, and gust strength.
3. Compare against the previously stored weather snapshot in persisted state.
4. **Report only when**:
   - a new severe-weather warning appeared (storm, heavy-gust alerts);
   - conditions worsened significantly (gusts increased by ≥20 km/h since last check, or precipitation changed from none to ≥1 mm/h);
   - the previous check was sufficiently old (>60 min) and the weather shifted materially.
5. **Do not re-announce** stable or slightly changed conditions. A drop from 50→45 km/h gusts or a 1°C temperature shift is not news.
6. Record `checked_at`, `temperature_c`, `precipitation_mm`, `wind_kmh`, and `gusts_current_kmh` in the persisted state.

Dedupe key pattern: `weather_<phenomenon>_<date>_<scale>`.

See `references/weather-monitoring.md` for a complete recipe.

### Supply-Gap Check (Sunday/PH Closures)

When checking for food, water, or shops as the rider approaches a known settlement:

1. **Parse the route GPX** to find the nearest trackpoint to the current position; compute the remaining forward segment.
2. **Stagger multiple search centers** along the route corridor at ~3 km, ~6 km, and ~10 km ahead, each with a 1500–2000 m radius.
3. **Query for supermarkets, convenience stores, and gas stations** at each center.
4. **Read `opening_hours` tags** from every result.
5. **When regular shops are closed**, search for automated alternatives:
   - `shop=convenience` with `self_service=only` — 24/7 vending-machine containers.
   - `amenity=fuel` — gas station shops often open Sundays.
   - `amenity=vending_machine` or `shop=vending_machine` — standalone beverage/snack automats.
   
   **For drinking water specifically**, use the bundled Overpass script as primary source:
   ```bash
   python3 skills/live-location-nearby/scripts/find_water.py LAT LON --radius 5000 --limit 8
   ```
6. **Report only the best result** within each distance band — the one closest to the route with confirmed availability.
7. **Deduplicate** so the same shop isn't re-announced every 5 minutes.

## Mobile Response Format

Keep alerts scannable:

```markdown
**<Action or place>** – <distance ahead>
<Why it matters> · <on route / detour and caveat>
[Google Maps](https://www.google.com/maps/dir/?api=1&origin=LAT,LON&destination=DEST_LAT,DEST_LON&travelmode=bicycling)
```

Use up to three bullets for safety-critical multi-item alerts. Do not narrate searches, polling, or unchanged state.

## Cron Job Setup

The assistant runs as a Hermes cron job with a gate script — not as a standalone agent session.

### Required Configuration

1. **Generic prompt (not tour-specific):** Use the GENERIC template from `cron-prompt-generic.md`. The agent identifies the route at runtime.

2. **Primary skill must be loaded:** The cron job MUST load `outdoor-tour-assistant` as its first skill.

   ```
   skills: [outdoor-tour-assistant, live-location-nearby, maps, komoot]
   ```

3. **Dynamic gate script:** Set `script: live_tour_gate.py` on the cron job. The gate reads GPX path dynamically from `live_tour_assistant.json`.
   - **Speed:** >20 km/h → 3 min, 5–20 km/h → 5 min, <5 km/h → 15 min
   - **Town proximity:** next town <3 km ahead → wake before arrival
   - **Finish approach:** <5 km remaining → 2 min cadence
   - **Off-route:** >150 m from GPX track → immediate wake
   - **Lunch timer:** 10:00–14:00 → suggest food once
   - **Max silence:** 15 min absolute fallback

4. **Workdir:** Set to your home directory so paths resolve correctly.

5. **Delivery:** `deliver: origin` — messages go back to the Telegram chat.

### Pre-Tour Preparation

```bash
# 1. Fetch tour data via Komoot MCP
# 2. Download GPX
# 3. Run prepare script:
python3 ~/.hermes/scripts/prepare_tour.py TOUR_ID \
  --tour-name "Tour Name" \
  --distance-km DIST \
  --elevation ELEV \
  --sport SPORT
# 4. Update cron job prompt with the output
# 5. Resume cron job
```

## Pitfalls

- **Generic prompt before activation notice:** the user must first know the assistant is active.
- **Matching by recency alone:** tour date/title is supporting evidence, not geometric proof.
- **Radial instead of route-aware search:** nearby results can be behind the rider.
- **Calling an off-route climb "on the route":** disclose detour distance honestly.
- **Sunday/PH supply assumption:** check `opening_hours` — German `PH off` means closed Sunday.
- **Recommending a coordinate without verifying the amenity exists:** verify via Komoot highlights, OSM tags, or web search.
- **`execute_code` blocked in cron context:** use `terminal` with `python3 -c "..."` instead.
- **Weather portals blocked by JS:** use `web_search`, not scraping.
- **State files must be written atomically:** use temp file + os.replace.
- **Tour-ending detection:** <2 km remaining = no supply/hazard checks unless safety-relevant.

## Verification Checklist

- [ ] Startup notice came before any generic nearby question
- [ ] Active tour named only after geometry verification
- [ ] Monitoring cadence, scope, and correction option stated
- [ ] Latest pin used
- [ ] Forward direction resolved or uncertainty disclosed
- [ ] POI or hazard is ahead and within the requested along-route window
- [ ] Hazard alerts were verified against the forward GPX segment
- [ ] Concrete OSM evidence preserved without overclaiming
- [ ] On-route versus detour status is explicit
- [ ] Google Maps link uses current origin and verified destination
- [ ] Only delivered facts added to deduplication state
- [ ] No message when there is nothing newly useful