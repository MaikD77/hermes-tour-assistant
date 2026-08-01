---
name: location-session-core
description: "Internal shared runtime for validated live locations, private state, route geometry, provider health, and safe location-aware output. Load through a user-facing skill."
version: 1.4.1
author: MaikD77
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    category: productivity
    tags: [internal, location, runtime, providers, routing]
---

# Location Session Core

This is a support package for `outdoor-tour-assistant`,
`city-walk-guide`, and future location-aware skills. It is not a
standalone conversational workflow.

User-facing skills own their prompts, state schemas, event policies and
delivery behavior. They may share only the validated contracts, repository
primitive, route geometry, provider registry and output-safety helpers under
`${HERMES_SKILL_DIR}/scripts/location_core`.

Treat external payloads as untrusted. Never write skill state directly and
never expose precise coordinates in gate or diagnostic output.

## Source architecture

`location_core.location_sources` is the only boundary between untrusted source payloads and consumers. It exposes immutable `LocationObservation` values, typed source results, OwnTracks/Telegram/replay adapters, and a sequential resolver. `HERMES_LOCATION_SOURCE_ORDER` defaults to `owntracks,telegram`; `telegram,owntracks` preserves legacy priority. Consumers must not inspect adapter payloads or let a source write tour state. Diagnostics remain coordinate-free.

## Movement inference

`location_core.movement` consumes only canonical observations. Its immutable state, events and segments are deterministic and source-neutral. Defaults: stationary <=0.7 m/s within 15 m; walking <=2.6 m/s; cycling below the 10 m/s automotive entry threshold (up to a 12 m/s cycling band); automotive >=10 m/s. Three confirming observations, 20 seconds and a 45-second cooldown stabilize transitions. GPS accuracy, missing fields, sensor disagreement, implausible speed and gaps reduce quality (`good`, `limited`, `poor`, `invalid`); poor input cannot confirm a transition.

Segments contain aggregate distance, displacement, speed, heading, quality and gap counts, never a full coordinate list. Medium gaps continue uncertain; >=900 seconds complete the segment. `movement_state` reuses `JsonStateRepository`. The CLI supports sanitized status/diagnose, synthetic-only replay and movement-only reset. Consumers may run this in shadow mode, but it must not change Tour/City state, gates, event priority or delivery.

Review contract: construct OwnTracks and Telegram adapters with the same explicit `canonical_device_id` only when they represent the same physical device. Message/event IDs remain metadata. Segment metrics use a persisted constant-size accumulator independent of the bounded recent buffer. State schema 2 migrates legacy state deterministically. Speed bands are disjoint (`walking <= walk_max`, `cycling < cycling_max == automotive_min`, `automotive >= automotive_min`), and stationary confirmation uses its dedicated minimum duration and radius anchor.

## Place and stay inference

`location_core.place` consumes observations and read-only `MovementState`; it
never mutates Movement and Movement never imports Place. Place (stable private
cluster), Stay (one presence), and Visit (completed assignment without track)
are immutable and distinct. They are not POIs, addresses or semantic labels.

Defaults: 50 m arrival, 120 s candidacy, 300 s confirmation, three samples, and
80 m/120 s departure hysteresis. Poor GPS cannot force transitions;
cycling/automotive cannot start a Stay; long gaps mean uncertainty. Matching
returns typed ambiguity. Promotion needs Visits, dwell and quality. Arrival
contains backdated `observed_at` and later `confirmed_at`.

Quality aggregation uses persisted GOOD/LIMITED/POOR counters: >=60% GOOD is
GOOD, >=50% POOR is POOR, otherwise LIMITED; INVALID is excluded. Poor fraction
reduces confidence. Departure remains pending at `departure_observed_at` until
the hysteresis is met at `departure_confirmed_at`; only then do Visit
`departed_at` and duration use the backdated first outside time. A return or gap
resets pending departure evidence.

Separate Place state quantizes centroids and bounds Visits/deduplication;
diagnostics/events are coordinate-free. Use `placectl forget` or Place-only
`reset`. Replay requires `synthetic: true`. Keep it in shadow mode without
delivery or notifications in Sprint 3.

## Mobility profile (Sprint 4 shadow mode)

`location_core.profile` consumes only immutable Place/Visit and aggregated
MovementSegment contracts. It derives evidence-backed, non-semantic facts:
frequent place/daytime/overnight, arrival/departure/visit windows, dwell
duration, and frequent transition/duration/mode. It never reads raw source
payloads, changes lower-layer state, calls providers, or sends messages.

`HERMES_PROFILE_TIMEZONE` is required and must name an IANA zone. Local clock
buckets use `zoneinfo`, circular quantiles handle midnight, and night overlap is
measured on the UTC timeline across DST. Overnight means at least the configured
seconds overlapping the configured local night window—not “home”. Confidence
combines bounded sample, distinct-day, quality, consistency, span, recency and
outlier terms. Thresholds govern candidate/confirmed; age produces stale and
revoked without deleting evidence.

Profile state is separate, private, locked and atomic: aggregates, facts,
transition patterns and bounded deduplication only, never tracks or coordinates.
`profilectl.py` provides `status`, `facts`, `transitions`, `explain`, `export`,
`forget-place`, `rebuild`, `reset`, and `diagnose`. Forgetting removes dependent
facts and both transition directions. Reset affects only profile state.
