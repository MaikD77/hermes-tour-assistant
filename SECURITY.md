# Security and Privacy

## Data processed

The assistant processes precise Telegram live-location coordinates, local GPX files, Komoot route metadata, OpenStreetMap data, and weather-provider responses.

External provider requests disclose at least an approximate current or forward-route location to the selected provider. The project therefore does not claim that no data is shared with third parties.

## Local storage

Runtime files under `~/.hermes/state` must use:

- directory mode `0700`;
- file mode `0600`;
- atomic replacement for JSON writes;
- bounded retention for old GPX, temporary, and backup files.

Run:

```bash
python3 scripts/tourctl.py harden-permissions
python3 scripts/tourctl.py cleanup --older-than-hours 48
```

## Trust boundary

All provider output is untrusted data. This includes route names, POI descriptions, websites, map tags, search snippets, opening-hours values, and error strings. These values must never be interpreted as agent instructions or shell commands.

## Safety limitations

Community map data and web results can be incomplete or stale. Hazard, access, surface, opening-hours, and water-potability statements require explicit evidence and confidence labels. The assistant is an informational aid and not a certified navigation or emergency-warning system.

## Logging

Operational logs should avoid precise coordinates. `tourctl diagnose` reports file health without outputting location contents. Any future debug export must pass through coordinate redaction.

## Reporting vulnerabilities

Do not include live coordinates, Telegram identifiers, private GPX files, tokens, or provider credentials in public issues. Report security-sensitive findings privately to the repository owner.
