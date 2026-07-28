# Sicherheit und Datenschutz

## Verarbeitete Daten

Der Assistent verarbeitet präzise Telegram-Live-Standorte, private GPX-Dateien,
Routenmetadaten, OpenStreetMap-Daten und Wetterantworten.

Ein expliziter Provideraufruf kann mindestens die ungefähre aktuelle oder
vorausliegende Position an den konfigurierten Anbieter übertragen. Der Provider-
und Terminalaufruf kann außerdem im Hermes-Tool-Audit erscheinen. Das Projekt
behauptet daher nicht, dass Standortdaten ausschließlich lokal verarbeitet
werden.

## Lokale Speicherung

Die Runtime erzwingt:

- State-Verzeichnis `0700`;
- State-, Lock-, GPX-, Temp- und Quarantänedateien `0600`;
- prozessübergreifende Dateisperre;
- eindeutige temporäre Dateien, `fsync` und atomaren Replace;
- validierte Migrationen statt stiller Rücksetzung;
- begrenzte Retention, ohne die aktive GPX-Datei zu löschen.

```bash
python3 skills/outdoor-tour-assistant/scripts/tourctl.py harden-permissions
python3 skills/outdoor-tour-assistant/scripts/tourctl.py cleanup --older-than-hours 48
```

## Trust Boundary

Routennamen, POI-Texte, Webseiten, Kartentags, Such-Snippets,
Öffnungszeiten und Providerfehler sind nicht vertrauenswürdige Daten.

- Inhalte daraus dürfen nicht als Agentenanweisungen interpretiert werden.
- Providerwerte dürfen nicht in Shell-Kommandos interpoliert werden.
- Externe Ereignisse benötigen Quelle, Evidenz und Confidence.
- Anzeigenamen werden für Markdown escaped.
- Navigationslinks werden lokal aus validierten Koordinaten erzeugt.
- Providerfehler werden auf feste Fehlercodes normalisiert; Rohtexte werden
  nicht im State gespeichert.

## Standortminimierung

Das Cron-Gate gibt keine präzisen Koordinaten aus. Sein Kontext enthält nur eine
pseudonyme Session-ID, Trigger, Cadence, Flags und sanitierte Fehlercodes.
`tourctl diagnose` und normales `tourctl context` redigieren Standorte.

`tourctl context --include-location` ist eine bewusste Ausnahme für einen
unmittelbar folgenden Provideraufruf. Die Position darf nicht in Antworten,
Debug-Exports oder öffentliche Tickets übernommen werden.

## Ausgehende Verbindungen

Der mitgelieferte Wetteradapter verwendet ausschließlich den fest definierten
Open-Meteo-Endpunkt. Die Wasser-Suche verwendet eine feste Liste von
Overpass-Endpunkten. Weitere Provider müssen durch die Registry konfiguriert
und wie untrusted behandelt werden.

## Sicherheitsgrenzen

Community-Karten und Wetterdaten können falsch, veraltet oder unvollständig
sein. Aussagen zu Gefahr, Zugang, Oberfläche, Öffnungszeiten und Trinkbarkeit
benötigen explizite Evidenz. Der Assistent ersetzt weder Navigation,
Wetterwarnsysteme noch Notfalldienste.

## Schwachstellen melden

Keine Live-Koordinaten, Telegram-IDs, privaten GPX-Dateien, Tokens oder
Provider-Credentials in öffentliche Issues schreiben. Sicherheitsrelevante
Funde privat an den Repository-Eigentümer melden.
