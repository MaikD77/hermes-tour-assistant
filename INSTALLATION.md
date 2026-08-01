# Installation und Betrieb

## Voraussetzungen

- Hermes Agent unter Linux oder macOS;
- Python 3.11 oder neuer;
- Telegram-Adapter mit Cache für bearbeitete Live-Standorte;
- Hermes-Toolsets `terminal` und `web`;
- optional Komoot- und Kartenfähigkeiten des jeweiligen Deployments.

## Skills installieren

Die Runtimes sind Bestandteil der kanonischen Skills. Es werden keine separaten
Root-Skripte kopiert:

```bash
cp -R skills/outdoor-tour-assistant ~/.hermes/skills/
cp -R skills/live-location-nearby ~/.hermes/skills/
cp -R skills/location-session-core ~/.hermes/skills/
cp -R skills/city-walk-guide ~/.hermes/skills/
```

Bei einem Update müssen die Zielverzeichnisse vollständig durch die neue
Version ersetzt oder über den Hermes-Skill-Installer aktualisiert werden.
`location-session-core` ist ein interner Support-Skill: Er wird nicht direkt
aufgerufen, muss aber neben den nutzerseitigen Skills installiert sein.

Das bereitgestellte `scripts/tour-assistant-update.sh` automatisiert den
Update-Vorgang inklusive Skill-Backup und Gate-Wrapper-Erstellung.

## Service-Umgebung

```bash
export HERMES_TOUR_CHAT_ID="DEINE_TELEGRAM_CHAT_ID"
export HERMES_TOUR_ACTIVITY="cycling"  # cycling oder walking
export HERMES_TOUR_LOCALE="de-DE"
export HERMES_TOUR_LOCATION_MAX_AGE_SECONDS="300"
export HERMES_OWNTRACKS_URL="http://127.0.0.1:9090/location"  # OwnTracks-Fallback
```

Optional kann `HERMES_TOUR_STATE_DIR` den privaten State-Pfad überschreiben.
`HERMES_TOUR_ROUTE_DIR` aktiviert die lokale `route.planned`-Fähigkeit für
zuvor exportierte GPX-Dateien. Die produktiven strukturierten Fähigkeiten
`weather.current`, `map.reverse`, `map.corridor` und `water.search` werden über
die Registry bereitgestellt.
Identifikatoren und Zugangsdaten dürfen nicht in Repository- oder Skill-Dateien
eingetragen werden.

Bei Bedarf lassen sich die profilspezifischen Distanzen über
`HERMES_TOUR_MOVE_THRESHOLD_M`, `HERMES_TOUR_OFF_ROUTE_ENTER_M`,
`HERMES_TOUR_OFF_ROUTE_EXIT_M`, `HERMES_TOUR_SETTLEMENT_APPROACH_M` und
`HERMES_TOUR_FINISH_APPROACH_M` anpassen. Werte müssen positiv sein; die
Off-Route-Austrittsschwelle muss unter der Eintrittsschwelle liegen.

## Cron-Konfiguration

Der Grundtakt muss eine Minute betragen. Die Gate Policy wählt intern die
effektive 2-, 3-, 5- oder 15-Minuten-Cadence.

```yaml
job_id: tour-assistant
name: Live Tour Assistant
schedule: every 1m
script: /home/USER/.hermes/skills/outdoor-tour-assistant/scripts/live_tour_gate.py
workdir: /home/USER
skills:
  - outdoor-tour-assistant
  - live-location-nearby
  - maps
  - komoot
deliver: origin
prompt: |
  Use the loaded outdoor-tour-assistant skill and the pre-run gate context.
  Return exactly [SILENT] when no evidenced event is selected.
```

Die vollständige Minimalaufgabe liegt unter
`references/cron-prompt.md` im installierten Skill.

### OwnTracks Receiver (alternative Standortquelle)

Zusätzlich zur Telegram-Live-Location kann der Outdoor-Assistent OwnTracks als
Standortquelle nutzen. Der Receiver wird als separater Service betrieben:

```bash
# Abhängigkeiten installieren
pip install -r services/owntracks-receiver/requirements.txt

# API-Key generieren
openssl rand -hex 32 > ~/.hermes/owntracks/.api_key

# Receiver starten (über das bereitgestellte Skript)
bash scripts/owntracks-start.sh
```

Umgebung für den Receiver:

```bash
export OWNTRACKS_PORT=9090
export OWNTRACKS_STALE_SECONDS=300
export OWNTRACKS_RATE_LIMIT=10
export OWNTRACKS_MAX_BODY=4096
```

Der Gate-Wrapper (`live_tour_gate.py`) fragt zunächst die Telegram-Live-Location
ab und fällt bei fehlender Aktualität auf OwnTracks (`GET /location` auf
`localhost:9090`) zurück. Weitere Details in
`services/owntracks-receiver/README.md`.

### City Walk Guide

Zusätzliche Umgebung:

```bash
export HERMES_CITY_GUIDE_CHAT_ID="123456"
export OPENROUTESERVICE_API_KEY="..."
# optional:
export HERMES_CITY_GUIDE_STATE_DIR="$HOME/.hermes/state"
export HERMES_CITY_GUIDE_RETENTION_HOURS="24"
# optional bei zusätzlichen Registry-Adaptern:
# export HERMES_CITY_GUIDE_ROUTE_PROVIDER="openrouteservice"
# export HERMES_CITY_GUIDE_MAP_PROVIDER="openstreetmap"
```

Der City-Cronjob läuft ebenfalls jede Minute und verwendet:

```text
~/.hermes/skills/city-walk-guide/scripts/live_city_gate.py
```

Als Task-Text dient
`skills/city-walk-guide/references/cron-prompt.md`. Für Telegram-Voice-Bubbles
aktiviert der Nutzer im Chat `/voice tts`; Edge TTS benötigt keinen eigenen
Schlüssel. `ffmpeg` muss auf dem Hermes-System installiert sein.

Eine Tour wird aus einer privaten Request-Datei gestartet:

```bash
python3 ~/.hermes/skills/city-walk-guide/scripts/cityctl.py \
  start --request /privater/pfad/city-request.json
```

Minimaler Inhalt ist `{}`; dann gelten 90 Minuten, Rundtour, Deutsch mit
Englisch-Fallback und die Standardinteressen. Die Request-Datei anschließend
löschen. Diagnose und Retention:

```bash
python3 ~/.hermes/skills/city-walk-guide/scripts/cityctl.py diagnose
python3 ~/.hermes/skills/city-walk-guide/scripts/cityctl.py cleanup
```

## Erstprüfung

```bash
python3 ~/.hermes/skills/outdoor-tour-assistant/scripts/tourctl.py \
  harden-permissions
python3 ~/.hermes/skills/outdoor-tour-assistant/scripts/tourctl.py \
  diagnose
python3 ~/.hermes/skills/outdoor-tour-assistant/scripts/tourctl.py \
  capabilities
```

`diagnose` gibt keine Standortinhalte aus.

## Route optional vorbereiten

Nur eine erfolgreich geparste GPX-Datei darf als verifiziert gespeichert werden:

```bash
python3 ~/.hermes/skills/outdoor-tour-assistant/scripts/prepare_tour.py 123456 \
  --tour-name "Meine Tour" \
  --provider komoot \
  --gpx-file /privater/pfad/tour.gpx
```

Die Runtime kopiert die Datei mit Modus `0600` in den State-Ordner. Ohne
`--gpx-file` bleibt die Route im Status `matching` und wird nicht als
verifiziert behandelt.

## Wichtige Betriebskommandos

```bash
# Sanitierter Kontext
python3 ~/.hermes/skills/outdoor-tour-assistant/scripts/tourctl.py context

# Präzise Position nur unmittelbar für einen Provideraufruf
python3 ~/.hermes/skills/outdoor-tour-assistant/scripts/tourctl.py \
  context --include-location

# Strukturierte aktuelle Wetterdaten
python3 ~/.hermes/skills/outdoor-tour-assistant/scripts/tourctl.py weather-current

# Abgelaufene GPX-, Temp-, Backup- und Quarantänedateien entfernen
python3 ~/.hermes/skills/outdoor-tour-assistant/scripts/tourctl.py \
  cleanup --older-than-hours 48
```

Eine aktuell referenzierte GPX-Datei wird von `cleanup` nicht gelöscht.

## Upgrade von 1.1 oder 1.2

1. Cronjob pausieren.
2. Beide Skill-Verzeichnisse aktualisieren.
3. `harden-permissions` und `diagnose` ausführen.
4. Gate einmal manuell ausführen.
5. Prüfen, dass `schema_version` den Wert `3` hat.
6. Cronjob im Shadow Mode reaktivieren.

Legacy-State wird migriert. Ungültiger State wird nicht überschrieben, sondern
privat quarantänisiert und einmalig als Betriebsfehler gemeldet.

## Rollout

1. **Replay:** gespeicherte, anonymisierte Standortpunkte; keine Auslieferung.
2. **Shadow:** reale Tour, Entscheidungen nur lokal protokollieren.
3. **Canary:** nur Start, eindeutiges Off-Route und schwere Warnungen.
4. **Normalbetrieb:** POI- und Versorgungshinweise erst nach erfolgreicher
   Canary-Tour aktivieren.

## Standortquellen konfigurieren

Neue Installationen verwenden `HERMES_LOCATION_SOURCE_ORDER=owntracks,telegram`. Für eine bestehende Installation ohne Verhaltensänderung zunächst `HERMES_LOCATION_SOURCE_ORDER=telegram,owntracks` setzen. Nach Prüfung des lokalen OwnTracks-Receivers kann auf den neuen Standard gewechselt werden. `HERMES_OWNTRACKS_URL` zeigt weiter auf dessen lokale `/location`-Schnittstelle; der Core öffnet SQLite nie direkt. Replay wird programmgesteuert über `ReplayLocationSource` für Tests oder Offline-Abläufe eingespeist und benötigt keine zusätzliche Persistenz.

## Movement Engine (Shadow Mode)

`HERMES_MOVEMENT_STATE_DIR` legt den privaten, gesperrten Movement-State fest (Standard `~/.local/state/hermes/movement`). Der State besitzt Schema-Version 1, wird atomar mit `0600` geschrieben und speichert nur einen begrenzten Feature-Puffer. Bei beschädigtem JSON wird die Datei quarantänisiert. Rollback: Shadow-Prozess stoppen und nur `movement reset` ausführen; Tour-, City- und OwnTracks-State bleiben unverändert.

```bash
python3 skills/location-session-core/scripts/movementctl.py status
python3 skills/location-session-core/scripts/movementctl.py diagnose
python3 skills/location-session-core/scripts/movementctl.py replay /path/to/synthetic.json
python3 skills/location-session-core/scripts/movementctl.py reset
```

Rollout: (1) Unit- und synthetische Replay-Tests, (2) anonymisierter Offline-Replay, (3) produktiver Shadow Mode ohne Benachrichtigung, (4) manueller Aktivitätsvergleich, (5) Canary nur in interner Diagnose, (6) Entscheidungsnutzung erst in einem späteren Sprint.

### Kanonische Geräteidentität konfigurieren

Für Shadow-Mode-Aufrufer, die zwischen OwnTracks und Telegram wechseln, eine nicht-personenübergreifende Kennung konfigurieren:

```bash
HERMES_LOCATION_CANONICAL_DEVICE_ID=maik-iphone
```

Diese Kennung beim Aufbau beider Adapter als `canonical_device_id` übergeben. Je physischem Gerät eine andere Kennung verwenden; niemals mehrere Personen oder Geräte auf denselben Wert abbilden. Ohne diese explizite Abbildung bleiben die bisherigen quellspezifischen Gerätekennungen erhalten und die Movement Engine lehnt einen Streamwechsel als anderes Gerät typisiert ab.

Movement-State-Schema 2 ergänzt einen konstant großen Segmentakkumulator. Schema 0/1 wird deterministisch migriert, sofern der alte aktive State noch einen Ursprung im begrenzten Puffer enthält; andernfalls wird er wie anderer beschädigter State quarantänisiert statt mit erfundenen Koordinaten fortgeführt.

## Place Engine (getrennter Shadow State)

`HERMES_PLACE_STATE_DIR` ist standardmäßig `~/.local/state/hermes/places`.
Ordner/Dateien werden `0700`/`0600`; Symlinks werden abgewiesen. Der State hält
quantisierte Cluster, höchstens 200 trackfreie Visits und 256 Deduplizierungs-IDs.
Alle betrieblichen Schwellen sind mit den in `.env.example` aufgeführten
`HERMES_PLACE_*`-Variablen konfigurierbar. Zählwerte und Centroid-Präzision
werden strikt als Integer gelesen; ungültige oder widersprüchliche Radius-,
Zeit-, Retention- und Gap-Beziehungen verhindern den Start.

```bash
python3 skills/location-session-core/scripts/placectl.py status
python3 skills/location-session-core/scripts/placectl.py replay tests/fixtures/place-replay.json
python3 skills/location-session-core/scripts/placectl.py list
python3 skills/location-session-core/scripts/placectl.py visits
python3 skills/location-session-core/scripts/placectl.py forget place_...
python3 skills/location-session-core/scripts/placectl.py reset
```

`forget` entfernt einen Place und Visits; `reset` leert ausschließlich
Place-/Stay-State. Rollout: Unit Tests, synthetische/anonymisierte Replays,
Shadow Mode, mehrtägiger Vergleich, Kalibrierung, später Context-Engine-Nutzung.
Es werden keine Nachrichten aktiviert.

## Mobility Profile (Shadow Mode)

Setze `HERMES_PROFILE_TIMEZONE=Europe/Berlin` (oder eine andere IANA-Zone); ohne
explizite Zone startet die Engine absichtlich nicht. Alle Schwellen stehen in
`.env.example`. `profilectl.py rebuild` liest nur abstrahierte PlaceVisits.
`profilectl.py reset` löscht ausschließlich den Profil-State. Mit
`forget-place PLACE_ID` verschwinden Place-Facts und Transitionen beider
Richtungen; Location-, Movement-, Place-, Tour- und City-State bleiben erhalten.
Ein späterer `profilectl.py rebuild` darf einen nur im Profil vergessenen Place
aus retained PlaceVisits wieder lernen. Für dauerhaftes Vergessen zuerst
`placectl.py forget PLACE_ID` ausführen und anschließend das Profil rebuilden.

## Current Context Shadow Mode

Setze mindestens `HERMES_PROFILE_TIMEZONE` oder explizit
`HERMES_CONTEXT_TIMEZONE` auf eine IANA-Zeitzone. Alle Schwellen sind in
`.env.example` dokumentiert. Der Kontext-CLI liest ausschließlich bestehende
untere States und führt keine Provideraufrufe aus:

```bash
python3 skills/location-session-core/scripts/contextctl.py compute
python3 skills/location-session-core/scripts/contextctl.py status
python3 skills/location-session-core/scripts/contextctl.py explain
python3 skills/location-session-core/scripts/contextctl.py export
python3 skills/location-session-core/scripts/contextctl.py diagnose
python3 skills/location-session-core/scripts/contextctl.py reset
```

`reset` entfernt nur den letzten Context-Snapshot. Location-, Movement-, Place-
und Profile-State bleiben unverändert. Das Context-Verzeichnis wird mit `0700`,
State und Lock mit `0600` angelegt; Writes sind gesperrt und atomar.
