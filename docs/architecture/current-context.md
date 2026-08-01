# Current Context architecture

## Purpose and boundary

The deterministic `CurrentContextEngine` composes already abstracted state in one direction: canonical observation, movement state/active segment, place/stay state, then mobility-profile facts/patterns. Lower layers are read-only. The engine has no provider, delivery, notification, calendar, mail, weather, POI, geocoding, LLM or ML dependency.

`context compute` obtains its current observation through the shared
`LocationSourceResolver`. A central factory builds the configured
`HERMES_LOCATION_SOURCE_ORDER` from OwnTracks and Telegram adapters and passes the
same optional `HERMES_LOCATION_CANONICAL_DEVICE_ID` to both. The CLI sees only a
typed `LocationSourceResult`, never either payload schema. Movement, Place and
Profile repositories are loaded independently; corrupt or rebuild-required inputs
become sanitized uncertainties while usable sibling inputs remain available.
Source resolution is an input-loader concern and may contact the configured local
source endpoints; the `CurrentContextEngine` itself remains provider-free.

## Snapshot and subcontexts

A `CurrentContext` is an immutable, bounded snapshot with deterministic ID, subject, compute/validity times, overall status/confidence/freshness and five subcontexts:

* Location: opaque observation/source/device IDs, timestamps, age, accuracy class, quality and available motion-field names—never coordinates.
* Movement: mode/confidence/since, aggregate speed/direction, bounded active segment metrics, quality and last gap—never route points.
* Place: active/candidate stay, opaque place ID, arrival/duration/departure-pending, status, count/confidence/match and quality—never labels or addresses.
* Profile: qualified fact/transition IDs, technical windows and overnight/daytime patterns. Revoked facts are excluded; stale facts are marked and contribute at 35% strength.
* Temporal: timezone-aware local instant, weekday/weekend, period, night window, IANA zone and DST flag.

Every lower timestamp is checked for timezone awareness and excessive future skew
before subtraction, sorting, freshness calculation or timezone conversion. An
invalid component is excluded from further temporal calculation, marked `invalid`,
and yields a deterministic `invalid_input` result. Even naive `computed_at` is
represented by that typed result instead of escaping as an exception.

IDs hash the relevant input IDs/timestamps, facts, traits and uncertainty codes. Equal inputs including `computed_at` yield equal IDs. Validity defaults to 120s.

## Freshness and status

Each component has independent configurable `fresh`, `aging`, `stale`, `expired` boundaries. Inclusive comparisons make exact thresholds deterministic. Defaults are Location 2/5/15 minutes, Movement 3/10/15, Place 5/15/30, and Profile 1/30/60 days. Missing is not silently treated as stale evidence.

Production configuration must set `HERMES_CONTEXT_TIMEZONE`, or explicitly set
`HERMES_PROFILE_TIMEZONE` for reuse. There is no silent UTC fallback in
`ContextConfig.from_env`; direct construction with `timezone="UTC"` remains
available only for explicit programmatic/test use.

`available` requires all four evidence components, sufficient confidence and no uncertainty. `partial` preserves useful subsets, `stale` means most present operational evidence is stale, `unknown` means no usable evidence, and `invalid` means critical structural/time/device inconsistency. Processing statuses distinguish computed, partial, insufficient, stale, conflict and invalid outcomes.

## Confidence, evidence and conflicts

The weighting is Location 35%, Movement 25%, Place/Stay 25%, Profile 15%. Each contribution is multiplied by freshness and data-quality. Up to 7.5 points reward independent source layers. Information uncertainties cost 3.5 points, ordinary conflicts 8 points and critical conflicts 18 points. The result is clamped to `[0,1]`; this is not an unweighted average.

Evidence is immutable and coordinate-free: type, source layer/ID, observation time, freshness, confidence, quality, reason and status. Uncertainties carry code, severity, affected component, reason, detection time, resolvability and evidence IDs. Clock skew, device mismatch, newer derived state, unknown referenced places, moving modes at near-zero speed, active stays during sustained movement and data gaps remain visible. Lower states are never corrected.

## Traits and time

Traits make only non-semantic claims: moving/stationary, known/candidate-place presence, place frequency, overnight match, arrival/departure cyclic-window match, transition presence and completeness. Windows cross midnight, have configurable tolerance and use the context IANA timezone through DST. A transition carries a confirmed previous place and an empty possible-target list unless evidence exists; the engine does not predict destinations. Unusual timing is intentionally conservative and must not be emitted without confirmed patterns, fresh state and gap-free evidence.

## Persistence, privacy and rollout

Computation is stateless. Operationally, `ContextStateRepository` may persist only the latest sanitized snapshot (schema 1), never history. The shared repository provides atomic writes, locking, `0700`/`0600` permissions, symlink defense, quarantine and context-only reset.

Roll out in order: (1) unit tests, (2) synthetic context fixtures, (3) offline replay, (4) Shadow Mode, (5) compare snapshots with the actual situation, (6) calibrate thresholds, and only then (7) design a context provider and separate decision engine.
