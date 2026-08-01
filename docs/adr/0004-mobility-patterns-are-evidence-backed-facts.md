# ADR 0004: Mobility patterns are evidence-backed facts, not semantic assumptions

## Context
Location, movement, and recurring private places exist after PRs 14–16. Patterns
are useful but sensitive and do not justify human meaning such as home or work.

## Decision
Add a downward-only deterministic Mobility Profile layer in shadow mode. It
consumes abstract Place/Visit/Stay/Movement contracts, never raw payloads,
external APIs, LLMs, messaging, or lower-state writes.

## Fact model
Immutable facts record ID, non-semantic type, subject, value, confidence,
evidence, first/last/computed timestamps, samples and lifecycle status. The
vocabulary covers frequent place/daytime/overnight, time windows, dwell, and
transition frequency/duration/mode. Semantic roles are forbidden.

## Evidence and confidence
Evidence includes visit/distinct-day/overnight/weekday/transition counts,
window, median dwell, quality and outliers. Confidence weights sample and day
sufficiency, quality, consistency, span and recency, with an outlier penalty.
Same-day samples cannot satisfy independent-day confirmation.

## Time model
An explicit IANA timezone is mandatory. ZoneInfo handles summer/winter time.
Circular quantiles avoid midnight averaging. Overnight means sufficient real
elapsed overlap with the configured local night—not home.

## Transitions
A directed transition requires departure, gap-free covering movement, and
arrival elsewhere without an unknown intermediate stay. Only direction, robust
duration, mode, weekday counts, confidence and status survive; routes do not.

## Staleness and privacy
Age produces stale then revoked without implicit deletion. Separate 0700/0600,
locked, atomic, no-symlink state stores bounded aggregates. Corruption is
quarantined. Explain/export contain no coordinates. Forget removes dependents;
reset is profile-only.

## Alternatives
Semantic rules, ML/LLM classifiers, full-history queries, raw tracks, vector
databases, UTC-only time and arithmetic clock means were rejected as premature,
opaque, privacy-hostile, or incorrect.

## Consequences
Results are reproducible and conservative. Retained PlaceVisits constrain
rebuild, while one configured timezone cannot represent unrecorded travel-zone
changes.

## Migration
Schema v1 starts empty; schema-zero empty state migrates deterministically.
Offline rebuild uses retained abstract visits. Lower states remain unchanged.

Schema v2 replaces lifetime aggregates with coordinate-free, time-stamped visit
evidence. Because schema v1 cannot be losslessly assigned to a retention window,
loading it returns an explicit rebuild-required condition rather than treating
legacy totals as current evidence. Rebuild then uses retained PlaceVisits.

## Deferred intentionally
Home/work and semantic names, geocoding, OSM/Wikipedia, calendar/Gmail,
destination prediction, active context/notification engines, proactive
Telegram, route weather, ML, LLMs, vector stores and dashboards remain deferred.
