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
python3 skills/city-walk-guide/scripts/cityctl.py diagnose
python3 skills/city-walk-guide/scripts/cityctl.py cleanup
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

Der City Walk Guide sendet präzise Koordinaten nur an die konfigurierten OSM-,
Wikimedia- und OpenRouteService-Adapter, die für Suche und Fußroute erforderlich
sind. Telegram-IDs werden nicht mitgesendet. Zugangsdaten kommen ausschließlich
aus der Prozessumgebung. Der getrennte `city_guide_state.json` und der vorab
geladene Story-Cache sind privat; abgeschlossene Sessions werden nach der
konfigurierten Retention (standardmäßig 24 Stunden) zurückgesetzt.

## Ausgehende Verbindungen

Der mitgelieferte Wetteradapter verwendet ausschließlich den fest definierten
Open-Meteo-Endpunkt. Die Wasser-Suche verwendet eine feste Liste von
Overpass-Endpunkten. Der City Walk Guide verwendet feste Endpunkte für
OpenStreetMap/Overpass, die gewählte Wikipedia-Sprachversion, Wikidata und
OpenRouteService. Weitere Provider müssen durch die Registry konfiguriert und
wie untrusted behandelt werden.

## Sicherheitsgrenzen

Community-Karten und Wetterdaten können falsch, veraltet oder unvollständig
sein. Aussagen zu Gefahr, Zugang, Oberfläche, Öffnungszeiten und Trinkbarkeit
benötigen explizite Evidenz. Der Assistent ersetzt weder Navigation,
Wetterwarnsysteme noch Notfalldienste.

## Schwachstellen melden

Keine Live-Koordinaten, Telegram-IDs, privaten GPX-Dateien, Tokens oder
Provider-Credentials in öffentliche Issues schreiben. Sicherheitsrelevante
Funde privat an den Repository-Eigentümer melden.

## Standort-Datengrenze

Standortantworten und Snapshots gelten als nicht vertrauenswürdig und werden strikt ohne stillschweigende String-zu-Zahl-Konvertierung validiert. Der Resolver protokolliert ausschließlich Quelle und Ergebniszustand; Koordinaten gehören weder in normale Logs noch in Gate-stdout. Adapter verändern keinen Tour-State. OwnTracks-Persistenz, Retention und Dateiberechtigungen bleiben Eigentum des lokalen Receivers; der Core nutzt nur dessen schmale HTTP-/Repository-Schnittstelle. Es werden keine Ports geöffnet und weder Tailscale- noch TLS-Einstellungen geändert.

## Movement-Datenschutzgrenze

Die Movement Engine arbeitet offline, ruft weder Provider noch LLM auf und sendet keine Nachrichten. Status und Diagnose enthalten keine Koordinaten oder Rohpayloads. Der private State enthält nur einen begrenzten Puffer normalisierter, für Segmentmetriken benötigter Merkmale; es entsteht keine unbegrenzte Route oder Personenbewertung. Deterministische IDs sind fachliche Deduplizierungsschlüssel. Symlinks werden abgewiesen, Writes sind atomar und gesperrt, beschädigte Dateien werden nicht überschrieben. Movement Confidence ist technische Klassifikationssicherheit, keine Aussage über den Nutzer.
