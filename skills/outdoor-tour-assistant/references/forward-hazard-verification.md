# Forward Route-Hazard Verification

Use this recipe when a moving rider needs obstacle checks in the next 3–10 km.

## Inputs

- newest valid live-location coordinate
- dense GPX track for the verified tour
- previous snapped GPX index (for direction)
- deduplication state

## 1. Snap and Build the Forward Window

Parse GPX trackpoints, compute cumulative Haversine distance, and snap the newest pin to the nearest plausible point. Resolve loops with the previous index/movement direction. Select points from the snapped index through the requested forward distance and derive their bounding box.

Record:

- snapped index
- off-route distance
- cumulative progress and remaining distance
- direction (`forward`, `reverse`, or unresolved)

## 2. Retrieve Hazard Candidates

Query Overpass within the forward-window bounding box for focused tags, for example:

- `highway=steps`
- `highway=construction`
- `ford=*` or nodes with `ford`
- `bicycle=no` / `bicycle=dismount`
- poor `smoothness`
- surfaces such as mud, sand, rough gravel, cobblestone, or unpaved where relevant to the bike type

Keep focused queries small; broad surface searches often time out and create noise.

## 3. Rank Against the Route

For each candidate, find the nearest point or segment on the **forward GPX slice** and calculate:

- along-route distance ahead
- perpendicular route offset
- candidate tags and geometry confidence

A way's Overpass `center` is only a first-pass locator. It can sit near a route without the route using that way.

## 4. Verify Exact Geometry

For candidates close enough to matter, retrieve the complete OSM way geometry. Preferred: focused Overpass `way(ID); out tags geom;`. Targeted fallback:

```text
https://api.openstreetmap.org/api/0.6/way/WAY_ID/full.json
```

Compare the hazard nodes/segments with adjacent GPX points. A credible crossing or overlap supports an alert; mere proximity across a street, river, railway, or parallel path does not.

## 5. Alert Threshold and Wording

Use context-sensitive corridor tolerances. For discrete obstacles such as stairs, require actual intersection or very close route alignment; do not treat 100–200 m proximity as proof. Preserve concrete tags:

- number of steps
- ramp present/absent
- surface
- access restriction
- `bicycle=dismount`

Mobile alert pattern:

```markdown
- **In ca. X km: <action>.** Die Route führt über/entlang <verified obstacle>; <concrete tags>. [Navigation](...)
```

If exact geometry cannot confirm use of the obstacle, either say `möglicherweise` with the reason or stay silent. Never promote an unverified bounding-box hit into a definite hazard.

## 6. State and Deduplication

After the check, atomically persist the newest position, check time, snapped route progress, refreshed weather snapshot, and only facts actually delivered. Use a stable semantic dedupe key such as `route_<place>_<hazard>_<distance-band>`. Do not mark a fact as reported unless it appears in the delivered alert.
