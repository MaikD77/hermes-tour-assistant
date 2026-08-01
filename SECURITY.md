# Sicherheit und Datenschutz

## Verarbeitete Daten

Der Assistent verarbeitet präzise Telegram-Live-Standorte, private GPX-Dateien,
Routenmetadaten, OpenStreetMap-Daten und Wetterantworten.

Der optionale Kalenderprovider verarbeitet besonders sensible Terminmetadaten ausschließlich
read-only. Google-Rohantworten, vollständige Teilnehmerlisten, Teilnehmeradressen, Anhänge,
Konferenzlinks, Tokens und private Notizen werden nicht persistiert. URLs, E-Mail-Adressen,
Telefonnummern, Meeting-Codes, HTML und Steuerzeichen werden entfernt bzw. maskiert;
Beschreibungen sind standardmäßig leer. Private Titel werden zu `Private event`, ihr Ort nur zu
`remote`, `onsite` oder `unknown`. Fehlertexte sind feste, inhaltsfreie Kategorien.

OAuth-Credentials bleiben außerhalb des Repositorys und werden nur per Pfad referenziert. Der
Google-Adapter akzeptiert allein den Calendar-readonly-Scope und stellt keine Mutation bereit.
Produktiv wird ausschließlich ein Service Account aus einer regulären, nicht verlinkten Datei
mit Modus `0600` verwendet. Die Factory liest oder protokolliert den Credential-Inhalt nicht;
fehlende Abhängigkeiten, unsichere Rechte und Auth-/Netz-/Rate-Limit-Zustände werden lediglich
als feste typisierte Kategorien ausgegeben.
Der Kalendersnapshot enthält nur normalisierte Events und Context-Evidenz, wird atomar mit
privaten Rechten gespeichert und ersetzt stets den vorherigen Snapshot; es gibt keine Historie
oder Rohdaten-Cache. Exporte und Explain-Ausgaben stammen aus demselben sanitisierten Snapshot.

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

Die kanonische Geräte-ID ist eine explizite lokale Konfiguration, keine globale Personen-ID. Unterschiedliche Geräte oder Personen dürfen nicht dieselbe Kennung erhalten. Der private Segmentakkumulator hält genau einen Segmentstartpunkt und konstante Aggregatwerte, keine Koordinatenchronik; weder seine Koordinaten noch präzise Pending-Anker werden von `status` oder `diagnose` ausgegeben.

## Place-State und Löschung

Place-State ist getrennt, weil Cluster räumliche Information dauerhaft
benötigen. Persistiert werden quantisierte Centroids, Aggregate,
aktiver/kandidativer Stay, höchstens 200 Visits ohne GPS-Track und begrenzte
Deduplizierung – niemals Rohpayloads. Events, Logs, CLI und Diagnose enthalten
keine Koordinaten. Atomare Writes, Lock, private Rechte, Symlink-Schutz,
Migration und Quarantäne gelten unverändert.

`place forget <place_id>` löscht Cluster und Visits; `place reset` löscht den
gesamten Place-State, aber keinen Movement-, OwnTracks-, Tour- oder City-State.
Private Backups müssen Betreiber separat löschen.

## Mobility-profile privacy

Das Mobilitätsprofil liegt getrennt unter `HERMES_PROFILE_STATE_DIR`
(Verzeichnis 0700, Dateien/Locks 0600), wird gesperrt und atomar ersetzt,
verweigert Symlinks und quarantänisiert beschädigtes JSON. Gespeichert werden
nur aggregierte Place-Zähler, begrenzte Zeit-/Dauer-Samples, Facts, Evidence,
Transition-Muster und bounded Deduplizierungs-IDs—keine Koordinaten, Rohpayloads
oder vollständigen Routen. Export, Explain und Diagnose sind koordinatenfrei.
Retention begrenzt Evidenz; Staleness allein löscht nichts. Forget und Reset
sind explizite Nutzeraktionen.

## Sensibler aktueller Kontext

Ein `CurrentContext` kann Bewegungsmodus, pseudonyme Place-/Fact-IDs und zeitliche
Gewohnheiten verbinden und ist daher hochsensibel. Das Modell minimiert Daten:
keine Koordinaten, Rohpayloads, Adressen, semantischen Place-Namen oder kompletten
Tracks. Export, Explain, Diagnose und der optionale letzte Snapshot verwenden
dasselbe sanitisierte Modell. Es gibt keine Snapshot-Historie. Der Context-State
nutzt Locking, atomaren Replace, private Rechte, Symlink-Schutz, Quarantäne und
isolierten Reset aus `JsonStateRepository`.

Die Engine läuft im Shadow Mode: Provideraufrufe und Delivery sind `false`.
Evidence verweist nur auf IDs und abstrahierte Zustände; widerrufene Profil-Facts
werden verworfen und stale Facts ausdrücklich abgeschwächt. Ein Debug-Modus mit
Koordinaten existiert absichtlich nicht.
