# ADR 0002: Movement is inferred deterministically from canonical location observations

- Status: Accepted
- Date: 2026-07-31

## Context

Tour and City runtimes already calculate haversine distances, route progress, cadence and direction locally. OwnTracks can supply speed, course and trigger; Telegram normally supplies only snapshot coordinates and time. This logic was skill-local and did not answer source-neutrally what motion is occurring. Replay already provides canonical observations.

## Decision

A shared offline `MovementEngine` accepts only immutable `LocationObservation` values. It reuses the core haversine calculation and adds spherical initial bearing, angular difference and circular heading aggregation. Adapters, resolver, gate/runtime contracts, event priority, weather and messaging remain unchanged.

### State and segments

The immutable read model exposes unknown, stationary, walking, cycling and automotive with confidence, UTC interval, smoothed feature values, evidence and quality. An immutable segment stores aggregates and identifiers, not a route. IDs are SHA-256 over canonical domain inputs. Processing returns typed accepted, duplicate, out-of-order, invalid, gap or insufficient-evidence results.

### Hysteresis and data quality

Transitions require three confirming observations, minimum duration and cooldown. Entry bands differ from continued state through pending evidence. Brief stops therefore do not immediately end cycling or automotive segments. Accuracy, cadence, missing sensor values, disagreement, jumps, density and gaps lower `good/limited/poor/invalid` quality; poor or invalid evidence cannot confirm transitions. Heading events require meaningful motion, quality, angle and circular stability.

### Gaps

Up to 180 seconds is normal cadence. Medium gaps continue the segment as uncertain. At 900 seconds a deterministic gap event completes the segment. The event records duration, impact and decision without coordinates.

## Alternatives

- LLM or learned classifier: rejected as non-deterministic, unnecessary and privacy-expanding.
- Source-specific rules: rejected because source name is not movement evidence.
- SQLite or complete route history: rejected because bounded state is sufficient.
- Immediate speed thresholds: rejected because GPS noise and traffic stops cause flapping.

## Consequences

Classification is explainable and replayable but threshold-based and intentionally conservative. Some slow cycling or dense-city driving remains unknown longer. No existing consumer behavior changes in Sprint 2.

## Security impact

No external call, notification, secret, public port or raw payload is introduced. Diagnostics are coordinate-free. Persistence reuses locked atomic JSON, private permissions, symlink defenses and corruption quarantine. The normalized feature buffer is bounded to 64 entries and must not become a long-term history.

## Migration strategy

Movement state begins at schema 1; schema 0 is deterministically promoted. It is independent from Tour, City and receiver state and can be reset or rolled back alone. Deploy via tests, anonymized replay, notification-free shadow mode, manual comparison and diagnosis-only canary. Decision-making is deferred.

## Deliberately deferred

Known places, home/work, clustering, routines, mobility profiles, prediction, calendar/Gmail, directional weather, POIs, notifications, UI, health data, machine learning, vector storage and all new transport modes are outside this decision.

## Review amendments

Segment aggregation is independent from the bounded recent-observation ring: schema 2 persists one private start coordinate plus unrounded distance, maximum-speed and circular-heading accumulators. This is constant-size state, not a route history, and it is excluded from diagnostics. Schema 0/1 migration reconstructs the accumulator only from an available bounded legacy origin; an impossible active-state migration is quarantined.

Speed bands are deliberately disjoint: walking includes its maximum, cycling ends exclusively at `cycling_max_mps`, and automotive starts inclusively at the identical `automotive_min_mps`. Structural validation requires both boundary values to match. Stationary transitions additionally require the complete stationary duration around a pending radius anchor.

Source adapters accept an explicit canonical device ID. Deployments may map OwnTracks and Telegram to the same value only for the same physical device. Source event/message IDs remain allowlisted metadata and never establish device identity. No implicit cross-person or cross-device mapping is performed.
