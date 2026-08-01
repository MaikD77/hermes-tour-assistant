# ADR 0003: Repeated stays form deterministic private places

## Status and context

Accepted for Sprint 3 shadow mode. Canonical observations and Movement answer
where/how, not continued presence or recurrence. Tour/City POIs and settlements
are named public provider/navigation content. Reuse would conflate them with a
private behavioral cluster, so they remain deliberately separate.

## Decision and model

Use the one-way dependency `LocationObservation → Movement Engine → Place &
Stay Engine`. Movement is read-only context and never knows Place. **Stay** is
one continuous presence, **Place** a stable recognizable spatial cluster, and
**PlaceVisit** a completed Stay assignment without GPS track. Place is not POI,
address, building or meaning. No label is inferred.

Arrival is the first sample of a subsequently confirmed Stay. Events carry its
backdated `observed_at` and later `confirmed_at`. For departure,
`departure_observed_at` is the first reliable sample beyond the departure
radius and is initially only pending evidence. `departure_confirmed_at` is the
later sample at which the hysteresis duration is satisfied. Only then is the
departure emitted: its factual `departed_at` and event `observed_at` are
backdated to `departure_observed_at`, while event `confirmed_at` is
`departure_confirmed_at`. Stay and Visit duration end at factual `departed_at`,
not confirmation time. A return or data gap breaks the pending evidence window.

## Detection, hysteresis and gaps

Defaults require three samples, 120 seconds candidacy and 300 seconds
confirmation inside 50 m. Low-speed/stationary evidence supports Stay;
cycling/automotive rejects creation. Departure needs 120 seconds beyond 80 m.
Poor accuracy cannot force departure and returning inside clears pending
departure. A gap beyond 30 minutes marks uncertainty, never departure; active
Stay survives restart/gap.

Stay data quality is a persisted bounded distribution of GOOD, LIMITED and POOR
observation counts; INVALID is never regular evidence. At least 60% GOOD yields
GOOD, at least 50% POOR yields POOR, and other valid mixtures yield LIMITED.
Thus one poor outlier does not dominate good evidence, while predominantly poor
input remains poor. Confidence receives an explicit penalty proportional to the
poor fraction, and POOR stays cannot promote a Place.

## Matching, promotion and identity

Qualified Stays match non-archived Places by centroid distance and spatial
overlap. Candidates sort deterministically. Two plausible distances within 20 m
produce `ambiguous`, never arbitrary assignment. Otherwise the nearest cluster
is incrementally updated. This reproducible offline strategy needs no ML/DBSCAN.

A Place remains `candidate` until at least three completed Visits, 1,800 seconds
total dwell and non-poor evidence. Confirmed Places are not demoted by one weak
Visit. Stay ID hashes device, first time and canonical observation ID. New Place
ID is seeded once from its first qualified Stay and never recomputed from its
moving centroid, making identity stable under drift and deterministic replay.

## Precision minimization and privacy

Calculations use observation precision in memory. Persistence rounds Place and
active/candidate centroids to four decimals (about 11 m latitude), sufficient
for 50–80 m rules. State retains at most 200 track-free Visits and 256
observation/event dedupe IDs; no raw payload or unbounded history. Events,
diagnostics, repr and default CLI contain no coordinates.

Place has a separate schema/file with atomic writes, lock, `0700` directory,
`0600` files, symlink rejection, migration and corrupt-file quarantine.
`forget` removes one Place, Visits and linked active state. `reset` removes only
Place/Stay state. Backup deletion remains the operator's responsibility.

## Alternatives and consequences

POI reuse was rejected due to semantic/provider coupling. Coordinate-derived
IDs were rejected because evolving centroids would change them. ML, DBSCAN and
remote geocoding were rejected for explainability, incrementality and privacy.
MovementSegment reuse was rejected because motion and presence have different
lifecycle/hysteresis. Durable spatial state creates deletion duties, and close
Places may deliberately remain ambiguous.

Schema 1 Place state migrates deterministically to schema 2 by seeding quality
counts from the prior aggregate and retaining pending departure evidence under
its explicit name. An absent/schema-0 empty file initializes schema 2; invalid
files quarantine. Movement/Tour/City state remains untouched.
Rollout: unit tests, synthetic replay, anonymized offline replay, production
shadow mode, several days' comparison, threshold calibration, then later
Context Engine consumption. No notifications are enabled.

## Deliberately deferred

Semantic names, home/work, reverse geocoding, addresses, OSM/POI enrichment,
calendar/mail/profile context, routines/patterns, prediction, weather,
Telegram/Notification Engine, LLM/ML, vector storage and dashboards.
