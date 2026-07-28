# Installation und Betrieb

## Voraussetzungen

- Hermes Agent unter Linux oder macOS;
- Python 3.11 oder neuer;
- Telegram-Adapter mit Cache für bearbeitete Live-Standorte;
- Hermes-Toolsets `terminal` und `web`;
- optional Komoot- und Kartenfähigkeiten des jeweiligen Deployments.

## Skills installieren

Die Runtime ist Bestandteil des kanonischen Skills. Es werden keine separaten
Root-Skripte kopiert:

```bash
cp -R skills/outdoor-tour-assistant ~/.hermes/skills/
cp -R skills/live-location-nearby ~/.hermes/skills/
```

Bei einem Update müssen beide Zielverzeichnisse vollständig durch die neue
Version ersetzt oder über den Hermes-Skill-Installer aktualisiert werden.

## Service-Umgebung

```bash
export HERMES_TOUR_CHAT_ID="DEINE_TELEGRAM_CHAT_ID"
export HERMES_TOUR_ACTIVITY="cycling"  # cycling oder walking
export HERMES_TOUR_LOCALE="de-DE"
export HERMES_TOUR_LOCATION_MAX_AGE_SECONDS="300"
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
