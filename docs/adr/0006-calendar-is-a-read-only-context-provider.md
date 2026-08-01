# ADR 0006: Calendar is a read-only context provider, not a decision source

## Kontext

Hermes kennt fünf lokale Schichten: Location Source, Movement/Segment, Place/Stay, Mobility
Profile und Current Context. Kalender ist die erste externe, besonders sensible Kontextquelle.

## Entscheidung

Ein quellenneutraler `CalendarProvider` liefert typisierte Zustände und immutable,
deterministisch identifizierte `CalendarEvent`s an einen separaten `CalendarContextEngine`.
Der Google-Adapter besitzt ausschließlich `calendar.readonly`; Replay ist der Offline-Port.
CalendarContext modelliert Current/Upcoming/Recent, technische Evidenz, Unsicherheit,
Freshness, Confidence und sanitisierte Busy-Konflikte. Private Sichtbarkeit reduziert Titel,
Beschreibung und Ort. Allowlist-Metadaten, aggregierte Teilnehmerzahl und begrenzte Texte setzen
Datenminimierung durch. Credentials und Tokens bleiben außerhalb von Code, State und Logs.

`CurrentContext` nimmt CalendarContext optional auf. Seine bestehende Confidence bleibt
unverändert; technische Traits sind keine Entscheidungen. Snapshot-Schema 1 migriert
deterministisch zu Schema 2 mit `calendar_context=null`.

## Alternativen

Direkter Google-Zugriff im Context Engine, dauerhafter Rohdaten-Cache und kalenderbasierte
Aktionen wurden verworfen: Sie koppeln Schichten, vergrößern Datenschutzrisiken und vermischen
Beobachtung mit Entscheidung. Eine Titel-/LLM-Interpretation wurde ebenfalls verworfen.

## Folgen und Migration

Providerfehler bleiben als Partial/Unknown isoliert, Paginierung und Zeitzonen liegen am Adapter,
und Tests können vollständig offline laufen. Dafür existiert noch keine semantische Verbindung
zu Location. Rollout und Rollback folgen der Architekturunterlage; bestehende Snapshots werden
neu aufgebaut oder von Schema 1 migriert.

## Bewusst vertagt

Geocoding, Place-Zuordnung, Distanz, ETA, Routing, Pünktlichkeit, Decision/Notification Engine,
Teilnahmeantworten und alle Kalender-Mutationen sind ausdrücklich spätere, eigene Entscheidungen.
