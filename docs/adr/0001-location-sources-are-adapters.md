# ADR 0001: Location sources are adapters; consumers use canonical observations

- Status: Accepted
- Date: 2026-07-31

## Context

Telegram snapshots, the OwnTracks receiver API, and deterministic replays expose different field names, timestamps, freshness signals, and source-specific identifiers. Allowing gates or runtimes to interpret those payloads couples product behavior to infrastructure and risks inconsistent validation or accidental coordinate logging.

## Decision

Every source implements the narrow `LocationSource` port and returns a typed result. Adapters validate untrusted input and create one immutable `LocationObservation`. Its timestamps are timezone-aware UTC `datetime` values. `observation_id` is `loc_` plus SHA-256 of canonical JSON containing source, device, UTC observation time, normalized latitude, longitude, accuracy, and an allowlisted stable event identifier. It identifies an individual source observation, not a person, journey, or physical place.

`source_metadata` is stored as a sorted immutable tuple. The global schema and each adapter apply explicit allowlists. Raw payloads, coordinate copies, secrets, and credential-bearing URLs are prohibited. Consumers receive only canonical observations. The resolver calls sources sequentially in configured order and logs only source and typed status.

Existing float-based public runtime calls are converted once by `adapt_legacy_sample`. The canonical model deliberately has no `session_id`, `message_id`, or `expires_at` aliases. The current state schemas obtain an opaque session key through the explicitly named compatibility helper `observation_session_id`; session expiry is maintained by the runtime rather than misrepresented as an observation property.

## Alternatives

- **Keep source payload unions in consumers:** rejected because every consumer would duplicate parsing, validation, and privacy controls.
- **Use UUIDs:** rejected because replay and duplicate detection require reproducible identity.
- **Hash raw payloads:** rejected because key ordering and irrelevant or sensitive fields would affect identity.
- **Retain float timestamps in the canonical model:** rejected because timezone meaning remains implicit.
- **Use a mutable metadata dictionary:** rejected because callers could change identity-related context after validation.

## Consequences

Adapters contain slightly more normalization code, but consumer logic is source-neutral and deterministic. Canonical values cannot be serialized directly to legacy JSON state without an explicit UTC timestamp conversion. Observation identity changes whenever an identity field changes; received time and non-identity metadata intentionally do not affect it.

## Security impact

SHA-256 IDs do not expose coordinates in clear text, while logs remain coordinate-free. Hashing is pseudonymization, not anonymization: an attacker with a narrow candidate set could attempt enumeration, so IDs must not be treated as public secrets. Metadata allowlists prevent raw payload retention and reject URLs or credential-like values. Existing receiver permissions, retention, local binding, TLS, and Tailscale configuration are unchanged.

## Migration strategy

New gates convert Unix timestamps only inside Telegram adapters and convert gate `now` at the compatibility boundary. OwnTracks ISO timestamps must carry a timezone. Public CLI and gate payloads remain unchanged. Deployments that need historical source priority set `HERMES_LOCATION_SOURCE_ORDER=telegram,owntracks`; the new default remains `owntracks,telegram`.

## Deliberately deferred

Mobility profiles, known places, clustering, prediction, calendar/Gmail access, proactive daily messages, new weather providers, location history, and profile-platform schema work remain out of scope.
