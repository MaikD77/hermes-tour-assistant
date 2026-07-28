# Deterministic Dynamic Gate

## Purpose

`scripts/live_tour_gate.py` runs every minute and decides whether a fresh Hermes
agent session is justified. Quiet ticks emit exactly:

```json
{"wakeAgent": false}
```

Wake output contains no coordinates:

```json
{
  "wakeAgent": true,
  "context": {
    "session_id": "telegram-<hash>",
    "reason": "moved",
    "cadence_minutes": 5,
    "flags": []
  }
}
```

## Input validation

The gate accepts only a location entry that:

- matches `HERMES_TOUR_CHAT_ID`;
- contains a non-empty Telegram `message_id`;
- has finite latitude/longitude in valid ranges;
- has not expired;
- is not in the future;
- is no older than `HERMES_TOUR_LOCATION_MAX_AGE_SECONDS`.

The chat ID is hashed into the runtime session ID and is never written to gate
stdout.

## State v3

The gate and agent-facing commands share one State Repository:

- `session` — share lifecycle;
- `route` — verified GPX and verified settlements;
- `position` — internal precise sample and segment match;
- `schedule` — wake state, Hysteresis, event and error cooldowns;
- `events`, `weather`, `provider_health`.

Every read-modify-write transaction holds a process lock. Invalid state is moved
to a private quarantine file and results in one sanitized operational wake.

## Route and speed

GPX is parsed only by `route_engine.py`. The match projects onto segments and
uses previous progress as continuity evidence. Competing matches at a crossing
reduce confidence below `0.5`, set `ambiguous: true`, and leave direction
`unknown`.

Speed uses two valid observations from the same session. Implausible jumps are
discarded rather than used for cadence.

## Cadence

Cycling defaults:

| Situation | Cadence |
|---|---:|
| verified route, less than 5 km remaining | 2 min |
| above 20 km/h | 3 min |
| 5–20 km/h | 5 min |
| below 5 km/h | 15 min |
| no verified route | 5 min |

Walking uses separate speed thresholds. Maximum regular silence is 15 minutes.

## Wake priority

1. new live-location share;
2. off-route transition or cooldown expiry;
3. finish approach;
4. verified settlement approach;
5. one lunch opportunity per local day;
6. movement of at least 350 m since the last wake;
7. scheduled check-in.

A check-in only asks the agent to inspect new external evidence. Without a
selected event, the agent must return `[SILENT]`.

## Verified settlements

The gate never invents towns from GPX sampling. A settlement needs:

- `verified_place: true`;
- stable ID and non-empty name;
- provider source;
- confidence of at least `0.5`;
- progress position on the verified route.

Only a settlement 200 m to 3 km ahead can trigger `town_approach`.

## Operational errors

Missing configuration, invalid snapshot, stale location and corrupt state use
fixed error codes. They are reported at most once per 24-hour cooldown and
never contain provider text, paths outside the state basename or coordinates.
