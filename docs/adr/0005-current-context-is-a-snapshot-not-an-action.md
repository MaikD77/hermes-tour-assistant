# ADR 0005: Current context is a deterministic snapshot of evidence, not an action

## Context
Location, Movement/Segment, Place/Stay and Mobility Profile independently describe the user. Sprint 5 needs one explainable answer to “what is reliably known now?” without deciding what Hermes should say or do.

## Decision
Add a deterministic, immutable, provider-free Current Context layer above the four existing layers. It reads only typed abstractions. Output is evidence, not action, with typed processing and context statuses.

## Snapshot model and subcontexts
One bounded snapshot has a content-derived ID, subject, timezone-aware compute/validity interval, confidence/freshness/status, five typed subcontexts, evidence, uncertainties and traits. It contains no unbounded history. Location, movement, place, profile and temporal concerns remain inspectable.

## Evidence and uncertainty
Evidence uses opaque source IDs plus times, freshness, confidence, quality and deterministic reason. It never copies payloads or coordinates. Missing, ambiguous, candidate, stale, gap, skew, mismatch and conflict conditions are first-class uncertainties with severity and evidence links.

## Freshness and confidence
Freshness has component-specific configured boundaries. Confidence uses 35% Location, 25% Movement, 25% Place and 15% Profile, multiplied by freshness and quality. Independent layers add a bounded bonus; uncertainties/conflicts subtract documented penalties. Values are clamped to `[0,1]`.

## Conflict handling
Cross-layer temporal/device mismatches, impossible speed/mode combinations, active-stay/movement disagreement and unknown references are reported. Critical structural conflicts make the snapshot invalid. Lower states are never repaired.

## Time, traits and privacy
IANA timezone conversion, DST and cyclic midnight-crossing windows are mandatory. Traits are technical, non-semantic derivations. Coordinates, payloads, addresses, semantic place names and full tracks are absent from model, diagnostics, explain, export and storage.

## Persistence
The engine is stateless. An optional repository retains exactly the latest sanitized snapshot with schema version, atomic locking, private permissions, symlink defense, quarantine and isolated reset. History is prohibited.

## Alternatives
An action engine was rejected because it mixes knowledge with policy. LLM summaries were rejected as non-deterministic and privacy-expanding. One universal TTL and snapshot history were rejected. Coordinate debug output was rejected.

## Consequences
Consumers gain repeatable context and must handle partial, stale, unknown and invalid results. Conservative confidence can initially under-classify situations.

## Migration
Existing state schemas do not change. Deployment enables offline replay and Shadow Mode, optionally creating a separate schema-1 last-snapshot file.

## Deliberately deferred
Context providers, Decision/Notification Engine, messages/proactivity, semantic home/work roles, unusualness expansion, destination prediction, ETA, calendar, mail, weather, radar, Wikipedia, POI/geocoding, LLM/ML/vector storage, dashboards, Apple Watch and CarPlay belong to later decisions.
