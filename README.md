# Hermes Tour Assistant 🚴🚶

<p align="center">
  <img src="assets/hero.svg" alt="Hermes Tour Assistant" width="100%">
</p>

**Bau dir deinen persönlichen Tour-Begleiter für Hermes Agent.** Egal ob Radtour, Wanderung oder Stadtbummel — der Assistent überwacht deine Live-Standort, warnt vor Routenabweichungen, findet Wasser- und Versorgungspunkte, checkt das Wetter und erzählt dir an Stationen spannende Geschichten. Und das alles leise: Nur wenn wirklich was los ist, meldet er sich.

- 🚴 **Outdoor Tour Assistant** — GPX-Routenüberwachung mit Echtzeit-Warnungen
- 🏙️ **City Walk Guide** — persönlicher, quellengestützter Stadtrundgang per Telegram
- 💧 **Live Location Nearby** — findet Wasser, Essen, Reparatur, Unterkunft
- 🤫 **Silent by default** — kein Rauschen, nur Signale

Private, standortbewusste Outdoor- und Stadtführungs-Skills für
[Hermes Agent](https://hermes-agent.nousresearch.com). Der Outdoor-Assistent
überwacht GPX-Touren leise und ereignisbasiert. Der City Walk Guide plant einen
persönlichen, belegten Stadtrundgang und erzählt an erreichten Stationen per Text
und optionaler Telegram-Voice-Bubble.

> **Schweigen ist der Normalfall.** Ohne ausgewähltes Ereignis antwortet der
> Cron-Agent ausschließlich mit `[SILENT]`.

## Stand: Version 1.4.1

**Neu in 1.4.1 — Weather Hunter 🌤️ & Mobile-Optimierung 📱 & TTS-Sprachausgabe 🎧**
- Stündliche Niederschlagsvorhersage via Open-Meteo
- Berechnet ob du dem Regen davonfahren kannst ("Noch 15 min bis Regen — bei 28 km/h schaffst du's!")
- Neue Event-Priorität `weather_hunter` im Event-Engine-Cooldown-System
- CLI-Kommando `tourctl.py weather-forecast --hours 3`
- **Mobile-optimierte Alerts** für iPhone Lock Screen:
  - Emoji-Marker als visuelle Kategorie (🌧️⚠️🚴🏘️🍽️📍✅)
  - Aktion + Distanz fett in Zeile 1 = Lock-Screen-Preview
  - Maximal 3 Zeilen pro Alert
  - Beispiel: `🌧️ **Regen in 12 min** – 8 km voraus`
- **TTS-Sprachausgabe** (lokaler qwen3-george Klon 🎧):
  - POI-Ansagen werden als Telegram-Voice-Bubble ausgeliefert
  - Wikipedia-Inhalte vom LLM auf 30–60 Sekunden gekürzt
  - Aktiviert per `[[audio_as_voice]]`-Tag für native Voice-Bubble
  - Läuft lokal – keine Latenz, keine Kosten, offline-fähig
- **Dynamische Cadence (Rhythmus) ⏱️:**
  - Geschwindigkeitsabhängig: >20 km/h → 3 min, 5–20 km/h → 5 min, <5 km/h → 15 min
  - Ziel-Annäherung: <5 km Rest → 2 min-Takt
  - Orts-Annäherung: automatischer Wake bei <3 km zur nächsten Stadt
  - Routenabweichung: sofortiger Wake bei >150 m Abweichung
  - Mittags-Timer: 10–14 Uhr, einmaliger Einkehrtipp
  - Maximal 15 min Stille als Fallback
- **Live-Standort-Frequenz 📡:**
  - Telegram sendet auf dem iPhone alle 2–10 Sekunden
  - Hermes-Adapter cached alle Edits → kein LLM-Call pro Update
  - Gate-Script entscheidet im Sekunden/Minuten-Takt, ob der Agent geweckt wird
  - ≈600 Location-Updates/Stunde → ≈12–20 LLM-Checks/Stunde

**Version 1.4** ergänzt den fachlich getrennten `city-walk-guide` und zieht
gemeinsame Standort-, State-, Routing-, Provider- und Ausgabeprimitive in
`location-session-core`. Der Outdoor-State v3 und seine Migration bleiben
kompatibel.

Der Outdoor-Assistent verwendet weiterhin einen einzigen produktiven Datenfluss:

- ein selbst enthaltenes, installierbares Skill-Paket;
- Session-State v3 mit Migration aus v1 und v2;
- prozessübergreifende Sperre, atomare Writes und private Dateirechte;
- segmentbasiertes GPX-Matching einschließlich Ambiguitätserkennung;
- ein Gate ohne präzise Koordinaten im Cron-stdout;
- priorisierte Events mit Evidenzpflicht, Confidence und Cooldown;
- normalisierte Providerfehler, Backoff, Circuit Breaker und TTL-Cache;
- strukturierte aktuelle Wetterdaten über Open-Meteo;
- strukturierte Karten-, Siedlungs-, Versorgungs- und Wassersuche über
  OpenStreetMap/Nominatim/Overpass;
- ein abgesicherter lokaler GPX-Adapter für exportierte Komoot- oder
  Drittanbieter-Routen;
- sichere Markdown-Labels und ausschließlich lokal erzeugte Navigationslinks;
- Diagnose-, Retention- und Agent-Kommandos über `tourctl.py`.

Der City Walk Guide bietet zusätzlich:

- einen validierten `GuideRequest` für 30–240 Minuten;
- standardmäßig einen 90-minütigen Rundgang mit lokalem Leben, Genuss,
  Geschichte und Architektur;
- OSM-Kandidaten sowie deutsche Wikipedia- und Wikidata-Inhalte mit markiertem
  Englisch-Fallback;
- eine OpenRouteService-Fußroute innerhalb von ±15 Prozent des Zeitbudgets;
- privat vorab geladene, quellengestützte Geschichten;
- Stopauslösung bis 80 Meter und Neuplanung nach zwei Abweichungen ab 120 Meter;
- validierte Betriebs- und Dialogkommandos über `cityctl.py`.

Die Anwendung ist ein Informations- und Entwicklungswerkzeug, kein
zertifiziertes Navigations- oder Warnsystem.

## Architektur

```mermaid
flowchart LR
    T["Telegram-Location-Cache"] --> I["Input Adapter<br/>Schema, Alter, Session"]
    I --> S["State Repository v3<br/>Lock, 0700/0600, atomar"]
    S --> G["Profilspezifische Gate Policy"]
    G --> C["Cron GateDecision<br/>sanitierter Kontext"]
    C --> A["Outdoor- oder City-Skill"]
    A --> P["Gemeinsame Provider Adapter"]
    P --> N["Normalisierung + Evidenz"]
    N --> R["Route Engine"]
    N --> E["Event Engine"]
    R --> E
    E --> O["Ein Alert oder SILENT"]
    E --> S
```

Das LLM editiert den technischen State nicht. Route, Siedlungen, Providerdaten
und Ereignisse werden ausschließlich über die validierten Runtime-Kommandos
geschrieben.

## Benachrichtigungspolitik

Die Event Engine priorisiert:

1. akute Sicherheit;
2. deutliche Routenabweichung;
3. Wetterwarnung;
4. kritische Versorgungslücke;
5. verifizierte Ortsannäherung;
6. Komfort-POI.

Pro Wake wird höchstens ein normales Ereignis geliefert. Bis zu drei Meldungen
sind nur zulässig, wenn alle sicherheitskritisch sind. Externe Ereignisse ohne
Evidenz oder mit Confidence unter `0.5` bleiben stumm.

Neutrale Punkte entlang einer Route heißen `route_checkpoint`. Sie sind keine
Siedlungen und lösen keinen `town_approach` aus.

## Repository-Struktur

```
hermes-tour-assistant/
├── README.md
├── INSTALLATION.md
├── SECURITY.md
├── pyproject.toml
├── .gitignore
├── skills/
│ ├── outdoor-tour-assistant/
│ ├── city-walk-guide/
│ ├── location-session-core/
│ └── live-location-nearby/
├── services/
│ └── owntracks-receiver/     # Optionaler Standort-Infrastruktur-Service
├── scripts/
│ ├── tour-assistant-update.sh # Deployment- und Update-Skript
│ └── owntracks-start.sh       # OwnTracks-Receiver-Startskript
└── tests/`
```

Jeder nutzerseitige Skill besitzt genau einen kanonischen Ordner. Der interne
`location-session-core` enthält die gemeinsam genutzte Implementierung; schmale
Outdoor-Kompatibilitätsmodule erhalten bestehende Installationen und Imports.

## Schnellstart

```bash
git clone https://github.com/MaikD77/hermes-tour-assistant.git
cd hermes-tour-assistant
cp -R skills/outdoor-tour-assistant ~/.hermes/skills/
cp -R skills/live-location-nearby ~/.hermes/skills/
cp -R skills/location-session-core ~/.hermes/skills/
cp -R skills/city-walk-guide ~/.hermes/skills/
```

## Beispiele — was du damit machen kannst

| Skill | Beispiel-Prompt | Ergebnis |
|-------|----------------|----------|
| 🚴 Outdoor | „Ich starte meine Spessart-Tour, überwache mich." | Routen-Matching, Live-Tracking, Wetter + Warnungen |
| 🏙️ City Walk | „Gib mir einen 90-minütigen Stadtrundgang durch Erfurt." | Quellengestützter Rundgang mit Geschichten pro Station |
| 💧 Nearby | „Wo gibt's hier Trinkwasser?" | Nächste Brunnen/Tanks/Raststätten via OSM |
| 🛑 Sicherheit | „Bin ich noch auf der Route?" | Routenabweichung + Off-Route-Alarm |

Die Hermes-Service-Umgebung benötigt mindestens:

```bash
export HERMES_TOUR_CHAT_ID="DEINE_TELEGRAM_CHAT_ID"
export HERMES_TOUR_ACTIVITY="cycling"  # oder walking
export HERMES_TOUR_LOCALE="de-DE"
# optional: Verzeichnis mit vorab exportierten GPX-Routen
export HERMES_TOUR_ROUTE_DIR="/privater/pfad/zu/routen"
# für den City Walk Guide
export HERMES_CITY_GUIDE_CHAT_ID="DEINE_TELEGRAM_CHAT_ID"
export OPENROUTESERVICE_API_KEY="DEIN_ORS_SCHLUESSEL"
```

Das Gate muss jede Minute gestartet werden:

```bash
# Direkter Aufruf des Skill-Gates:
python3 ~/.hermes/skills/outdoor-tour-assistant/scripts/live_tour_gate.py

# Oder via Wrapper (für Cron mit Umgebungsvariablen):
python3 ~/.hermes/scripts/live_tour_gate.py
```

Das Deployment erfolgt über das Update-Skript im Repository:

```bash
bash scripts/tour-assistant-update.sh
```

Für die OwnTracks-Standortquelle muss der Receiver gestartet werden:

```bash
bash scripts/owntracks-start.sh
```

Der vollständige Cronjob, Betriebsbefehle und Rollout stehen in
[INSTALLATION.md](INSTALLATION.md).

## State v3

Der private State liegt standardmäßig unter
`~/.hermes/state/live_tour_assistant.json` und enthält:

- `session` – pseudonyme Share-ID und Lebenszyklus;
- `route` – Matchstatus, Provider, privater GPX-Pfad und verifizierte Orte;
- `position` – interne Position und deterministischer Routenmatch;
- `schedule` – Cadence, Wake-Zeitpunkte, Hysterese und Cooldowns;
- `events`, `weather` und `provider_health`.

Alte v1-/v2-Zustände werden beim ersten Zugriff validiert nach v3 migriert.
Beschädigte Dateien werden privat unter
`live_tour_assistant.json.corrupt-*` quarantänisiert.

## Entwicklung

```bash
python3 -m pip install -e ".[test]"
ruff check skills tests
mypy skills/location-session-core/scripts skills/outdoor-tour-assistant/scripts skills/city-walk-guide/scripts
pytest --cov --cov-fail-under=85
python3 -m build
```

Die Tests verwenden keine echten Standort- oder Providerdaten. Realer Rollout
erfolgt über Replay, Shadow Mode und Canary Delivery.

## Datenschutz

Präzise Standorte bleiben aus Gate-Ausgabe und Diagnoseberichten heraus. City
Walks verwenden den separaten privaten State
`~/.hermes/state/city_guide_state.json` und werden nach Abschluss standardmäßig
nach 24 Stunden bereinigt. Ein
expliziter Provideraufruf kann die aktuelle oder vorausliegende Position an den
konfigurierten Anbieter übertragen und erscheint gegebenenfalls im
Hermes-Tool-Audit. Details und Grenzen stehen in [SECURITY.md](SECURITY.md).

---

> **GitHub Topics (empfohlen):** `hermes-agent`, `hermes-skill`, `outdoor`, `cycling`, `city-walk`, `gpx`, `telegram-bot`, `live-location`, `openstreetmap`, `osm`

## Community & Austausch

| Plattform | Wo posten? | Warum? |
|-----------|-----------|--------|
| 🎮 **Nous Discord** | [#agent](https://discord.gg/nousresearch) | 128k+ Mitglieder, aktivste Community |
| 📱 **Reddit** | [r/hermesagent](https://reddit.com/r/hermesagent) | Offizielles Sub, Nous-Team aktiv |
| 💬 **GitHub Discussions** | [Hermes Agent](https://github.com/nousresearch/hermes-agent/discussions) | Skills teilen & diskutieren |
| 📖 **Skills Hub** | [Hermes Docs](https://hermes-agent.nousresearch.com/docs) | Community-Skills-Katalog |
| 🐛 **Issues** | [Hier](https://github.com/MaikD77/hermes-tour-assistant/issues) | Bug-Reports & Feature-Wünsche

## Quellenneutrale Standortarchitektur

Alle Standortverbraucher arbeiten mit einer unveränderlichen, validierten `LocationObservation`; Telegram-, OwnTracks- und Replay-Payloads bleiben in ihren Adaptern. Die deterministische Standardreihenfolge ist `owntracks,telegram` und kann mit `HERMES_LOCATION_SOURCE_ORDER` geändert werden. Telegram bleibt eine unterstützte Fallback- und Legacy-Quelle. Bestehende Installationen stellen das frühere Verhalten mit `telegram,owntracks` wieder her. `ReplayLocationSource` dient reproduzierbaren Tests und Offline-Demonstrationen und legt keine Historien-Datenbank an.

```mermaid
flowchart LR
  OT[OwnTracks Receiver API] --> OA[OwnTracks adapter]
  TG[Telegram snapshot] --> TA[Telegram adapter]
  RP[Replay observations] --> RA[Replay adapter]
  OA --> R[Deterministic source resolver]
  TA --> R
  RA --> R
  R --> O[LocationObservation]
  O --> OG[Outdoor gate/runtime]
  O --> CG[City gate/runtime]
```

Der Resolver fragt Quellen strikt nacheinander ab; die erste gültige und aktuelle Beobachtung gewinnt. Diagnosen enthalten nur Quellname und Zustand (`not_available`, `stale`, `invalid`, `unreachable`), niemals genaue Koordinaten.

### Beobachtungsidentität und Zeitmodell

`observation_id` ist eine reproduzierbare, koordinatenfreie SHA-256-Kennung über kanonisch serialisierte Identitätsfelder einer einzelnen Quellenbeobachtung. Sie ist keine Personen-, Sitzungs- oder Ortskennung. `observed_at` und `received_at` sind intern ausschließlich timezone-aware UTC-`datetime`-Werte. Quellspezifische Metadaten werden nach Adapter-Allowlist als sortiertes, unveränderliches Tuple gespeichert; Rohpayloads, Koordinatenkopien, Secrets und Credential-URLs sind unzulässig. Die Architekturentscheidung und Migrationsfolgen stehen in [ADR 0001](docs/adr/0001-location-sources-are-adapters.md).

## Deterministische Movement Engine

Der gemeinsame Core leitet aus `LocationObservation` ausschließlich regelbasiert die Modi `unknown`, `stationary`, `walking`, `cycling` und `automotive` ab. Bestätigte Übergänge erzeugen deterministische Ereignisse und kompakte aktive oder abgeschlossene Segmente. Confidence bezeichnet nur technische Klassifikationssicherheit. Hysterese (drei Beobachtungen, 20 s Mindestdauer, 45 s Cooldown), Qualitätsgrenzen und getrennte Eintrittsbänder verhindern Flattern und Fehlklassifikationen durch einzelne GPS-Sprünge. Kurze Lücken bis 180 s bleiben im Segment; ab 900 s wird es abgeschlossen.

```mermaid
flowchart TD
  O[LocationObservation] --> V[Observation validation]
  V --> E[Movement Engine]
  E --> S[MovementState]
  S --> ES[MovementEvents + MovementSegment]
  ES --> C[Outdoor / City / zukünftige Context Consumer]
```

Die Engine ist in Sprint 2 nicht an Versand- oder Gate-Entscheidungen angeschlossen. `movementctl.py status|diagnose|replay|reset` bietet koordinatenfreie Diagnose; Replay akzeptiert nur Dateien mit `synthetic: true`. Details: [ADR 0002](docs/adr/0002-movement-from-canonical-observations.md).

### Präzisierungen aus dem Sprint-2-Review

Aktive Segmente verwenden einen konstant großen privaten Akkumulator für Startpunkt, ungerundete Gesamtdistanz, Maximalgeschwindigkeit und zirkuläre Heading-Summen. Damit bleiben Segmentmetriken nach Ringpufferrotation und State-Neuladen korrekt; der Diagnose-Output gibt den privaten Startpunkt niemals aus. Der `recent`-Puffer bleibt auf `buffer_size` begrenzt.

Die Geschwindigkeitsbereiche sind disjunkt: `stationary <= 0,7 m/s`, `walking <= 2,6 m/s`, `cycling < 10,0 m/s` und `automotive >= 10,0 m/s`. Deshalb müssen `HERMES_MOVEMENT_CYCLING_MAX_MPS` und `HERMES_MOVEMENT_AUTOMOTIVE_MIN_MPS` denselben Übergangswert besitzen. Stationär wird erst nach der vollständigen `stationary_min_seconds`-Dauer innerhalb des Radius bestätigt.

Für einen quellenübergreifenden Stream muss beim Erzeugen sowohl des OwnTracks- als auch des Telegram-Adapters derselbe explizite `canonical_device_id` (z. B. aus `HERMES_LOCATION_CANONICAL_DEVICE_ID=maik-iphone`) übergeben werden. Event- beziehungsweise Message-IDs verbleiben in `source_metadata`; ohne explizite Zuordnung werden Geräte nicht implizit vermischt.
