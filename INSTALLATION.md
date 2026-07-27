# Installation and Operations

## Requirements

- Hermes Agent with Telegram live-location cache support
- Python 3.11 or newer
- terminal and web toolsets
- optional Komoot and maps integrations

## Install skills

Copy the two skill directories into the Hermes skill root:

```bash
cp -R skills/outdoor-tour-assistant ~/.hermes/skills/
cp -R skills/live-location-nearby ~/.hermes/skills/
```

Copy runtime scripts into one private directory:

```bash
install -d -m 700 ~/.hermes/scripts/tour-assistant
install -m 700 scripts/*.py ~/.hermes/scripts/tour-assistant/
```

Set the Telegram chat ID through the service environment:

```bash
export HERMES_TOUR_CHAT_ID="YOUR_TELEGRAM_CHAT_ID"
```

Do not hard-code identifiers or credentials in repository files.

## Cron configuration

The gate must run every minute. The internal gate selects the effective 2, 3, 5, or 15 minute wake cadence.

```yaml
job_id: tour-assistant
name: Live Tour Assistant
schedule: every 1m
script: /home/USER/.hermes/scripts/tour-assistant/live_tour_gate.py
workdir: /home/USER
skills:
  - outdoor-tour-assistant
  - live-location-nearby
  - maps
  - komoot
deliver: origin
```

A five-minute cron interval cannot provide the documented two- or three-minute cadence.

## Initial hardening

```bash
python3 ~/.hermes/scripts/tour-assistant/tourctl.py harden-permissions
python3 ~/.hermes/scripts/tour-assistant/tourctl.py diagnose
```

## Retention

Remove expired GPX, temporary, and backup files regularly:

```bash
python3 ~/.hermes/scripts/tour-assistant/tourctl.py cleanup --older-than-hours 48
```

## Verification

Repository checks:

```bash
python3 -m pip install pytest ruff
ruff check scripts tests
pytest
```

Operational checks:

1. Start live-location share A and attach route A.
2. End share A.
3. Start share B.
4. Verify that route, progress, weather, and event data from A are absent.
5. Replay positions around a route crossing and confirm stable segment continuity.
6. Simulate a provider outage and confirm degraded health without unsupported alerts.

## Rollout

Use three stages:

1. Replay mode with stored location points and no delivery.
2. Shadow mode on a real tour with local decisions only.
3. Canary delivery limited initially to startup, clear off-route events, and severe warnings.
