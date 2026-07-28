# Tour-Assistent — Erweiterte Ideen & Optionen (Beyond Backlog)

> **Für Hermes:** Plan-Modus — nur Analyse, keine Umsetzung.

**Goal:** Neue Funktionsideen für den Tour-Assistenten jenseits des bestehenden Backlogs erkunden und bewerten, basierend auf den Fragen: bessere Komoot-GPS-Integration, Alternativen zu Telegram Live-Standort, automatisierte POI-Audioführung.

**Architecture:** Die Analyse betrachtet jede Idee isoliert nach Machbarkeit, Aufwand, Nutzen und Integration in die bestehende v1.4-Architektur (event_engine, providers, tour_runtime).

**Tech Stack:** Hermes Agent v1.4 · Python 3.11 · OpenStreetMap/Overpass · Wikipedia API · ElevenLabs TTS · OpenRouteService · Garmin LiveTrack API · OwnTracks

---

## 1. Bessere Komoot-GPS-Verzahnung

### 1.1 Problem heute

Der Assistent matched die Route beim Start einmalig via Komoot-Geometrie. Danach wird nur noch der GPX-Track verwendet. Was fehlt:

- **Kein Lernen:** Die tatsächlich gefahrene Linie weicht oft vom geplanten GPX ab (Umwege, Abkürzungen, Einkehren)
- **Keine dynamische Neuzuordnung:** Wenn der Fahrer die Route verlässt, erkennt der Assistent nur "off_route", aber nicht *wo* der Fahrer stattdessen ist
- **Keine Richtungsauflösung bei Schleifen:** GPX mit Loops (Hin- und Rückweg) sind mehrdeutig

### 1.2 Vorschlag: GPS-Trace-basierte Routenkorrektur

**Idee:** Statt den GPX-Track starr zu verwenden, wird die tatsächlich gefahrene Linie (die Telegram-Live-Punkte) als sekundärer Track gespeichert. Der Assistent matched die Position dann gegen den *gefahrenen* Pfad, nicht nur den geplanten.

**Vorteil:** Wenn der Fahrer eine Abkürzung nimmt, weiß der Assistent sofort Bescheid und kann die Reststrecke neu berechnen.

**Aufwand:** Mittel (8–12 h)
- Neue Event-Kategorie: `route_adapted` (Routenanpassung erkannt)
- `route_engine.py` erweitern: Matchen gegen historische GPS-Punkte
- `tour_state.py` erweitern: Feld `gps_trace: list[LocationSample]` im Session-State

### 1.3 Vorschlag: Komoot-Planerkennung per KI

**Idee:** Statt nur Geometrie-Matching könnte der Assistent mehrere Kandidaten-Touren aus Komoot mit einer Gewichtung versehen und den besten Treffer wählen:
- Geometrie-Ähnlichkeit (wie heute)
- Tageszeit-basiert (eine 9-Uhr-Tour matched eine 9-Uhr-Startzeit)
- Sportart-basiert (racebike vs mtb_easy)
- Historische Präferenz (welche Touren hat der Fahrer in der Vergangenheit gewählt?)

**Vorteil:** Höhere Trefferquote bei der automatischen Routenidentifikation, besonders bei überlappenden Routen.

**Aufwand:** Gering (4–6 h)
- `tour_runtime.py` anpassen: Scoring-Funktion für Route-Matches
- Bestehende Komoot-API wird bereits genutzt
- Neue Tests in `test_tour_runtime.py`

---

## 2. Alternativen zu Telegram Live-Standort

### 2.1 Telegram ist gut, aber nicht perfekt

| Kriterium | Telegram | Bewertung |
|-----------|----------|-----------|
| Aktualisierungsrate | ~5–15 sec | ⚠️ Langsam für Echtzeit |
| Batterieverbrauch | Mittel | ⚠️ Telegram hält GPS wach |
| Genauigkeit | GPS (iOS) | ✅ Gut |
| Offline-Verhalten | Bricht ab | ❌ Kein GPS bei keinem Netz |
| Zusätzliche Daten | Nur Koordinaten | ❌ Keine Herzfrequenz, Trittfrequenz |
| Adapter-Patch | Nötig (edited→cached) | ⚠️ Fragil |

### 2.2 Option A: Garmin LiveTrack

**Funktion:** Garmin Edge-Geräte können LiveTrack senden – einen öffentlichen Link, der Position, Geschwindigkeit, Herzfrequenz und Trittfrequenz in Echtzeit teilt.

**Integration:**
- Garmin LiveTrack erzeugt einen Public-URL
- Hermes pollt diese URL alle 60 Sekunden
- Parsed die Position + Metriken (HF, Tritt, Geschwindigkeit)

**✅ Vorteile:**
- ✅ Herzfrequenz + Trittfrequenz (echte Rad-Daten!)
- ✅ Batterieeffizient (Garmin statt iPhone-GPS)
- ✅ Kein Telegram-Patch nötig

**❌ Nachteile:**
- LiveTrack-URL ist nicht für Maschinenlesen designed (HTML-Seite)
- Drittanbieter-API nötig für strukturierte Daten
- Garmin-Gerät vorausgesetzt

**Aufwand:** 12–20 h (API-Erkundung + Parser + Provider-Adapter)

### 2.3 Option B: OwnTracks (Open Source)

**Funktion:** [OwnTracks](https://owntracks.org) ist eine Open-Source-App, die GPS-Daten per MQTT an einen eigenen Server sendet. Extrem genaue Positionsdaten mit Batterie-Optionen.

**Integration:**
- OwnTracks-App auf dem iPhone → MQTT-Broker (z.B. `test.mosquitto.org`)
- Hermes subscribed auf den MQTT-Topic
- Erhält JSON mit: `lat`, `lon`, `alt`, `speed`, `bearing`, `battery_level`

**✅ Vorteile:**
- ✅ Strukturierte JSON-Daten (kein HTML-Parsing)
- ✅ Speed + Bearing (Richtung!) direkt enthalten
- ✅ Open Source, keine Vendor-Lock-in
- ✅ Batterieschonend (iOS signifikant location changes)

**❌ Nachteile:**
- MQTT-Broker nötig (öffentlich oder selbst gehostet)
- Zusätzliche App auf dem iPhone
- MQTT-Client-Bibliothek in Python nötig
- Kein Hermes-Adapter vorhanden (muss gebaut werden)

**Aufwand:** 8–12 h (MQTT-Listener + Provider-Adapter + State-Integration)

### 2.4 Option C: iPhone Standortfreigabe (Find My / Apple APNs)

**Idee:** Apples eigene Standortfreigabe via "Wo ist?" / Find My. Könnte direkt auf dem Mac empfangen werden.

**❌ Nicht praktikabel:**
- Keine öffentliche API für Find My Standorte
- Nur über Apple-Geräte und iCloud-Familie
- Keine Drittanbieter-Integration

### 2.5 Bewertung

| Option | Datenqualität | Aufwand | Batterie | Zusatzdaten |
|--------|--------------|---------|----------|-------------|
| **Telegram (heute)** | ⭐⭐⭐ | 0 h | ⭐⭐ | ❌ |
| **Garmin LiveTrack** | ⭐⭐⭐⭐⭐ | 12–20 h | ⭐⭐⭐⭐⭐ | ✅ HF, Tritt |
| **OwnTracks** | ⭐⭐⭐⭐⭐ | 8–12 h | ⭐⭐⭐⭐ | ✅ Speed, Richtung |
| **Find My** | ⭐⭐⭐ | ❌ Keine API | ⭐⭐⭐⭐ | ❌ |

**Empfehlung:** Telegram als primäre Quelle behalten, aber **OwnTracks als optionalen Provider** einbauen. Die Richtungsinformation (bearing) ist besonders wertvoll für die Routenidentifikation.

---

## 3. Automatische POI-Audioführung („Andreaskirche"🎧)

### 3.1 Die Vision

Während der Fahrt: Der Assistent erkennt ein interessantes POI voraus (Kirche, Aussichtspunkt, historisches Gebäude), holt den Wikipedia-Artikel, lässt ihn vom LLM auf 30–60 Sekunden kürzen und liest ihn per TTS (ElevenLabs) vor.

> "In 100 Metern auf der rechten Seite siehst du die Andreaskirche. Sie wurde 1723 erbaut und ist das älteste erhaltene Barockbauwerk der Region. Besonders sehenswert ist das Deckengemälde von Johann Michael Ziegler."

### 3.2 Architektur

```
┌─────────────────────────────────────────────────────┐
│  Event Engine                                       │
│  erkennt: POI voraus, POI-Typ: church, Distanz: 100m│
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│  POI Narration Service                              │
│  1. POI-Koordinaten → OSM/Wikipedia abfragen         │
│  2. Wikipedia-Artikel extrahieren                    │
│  3. LLM: Zusammenfassung auf 30-60 Sekunden kürzen   │
│  4. TTS: ElevenLabs in Sprache umwandeln             │
│  5. Audio-Datei als Voice-Bubble senden              │
└─────────────────────────────────────────────────────┘
```

### 3.3 Datenquellen

| Schritt | Quelle | Beispiel |
|---------|--------|---------|
| POI finden | OSM Overpass (`historic=*`, `tourism=viewpoint`, `building=church`) | +500m entlang der Route |
| POI identifizieren | OSM-Tags + Wikidata-ID | `wikidata=Q12345` |
| Artikel holen | Wikipedia API | `https://de.wikipedia.org/w/api.php?action=query&prop=extracts&titles=Andreaskirche` |
| Zusammenfassen | LLM (DeepSeek, GPT) | 30-60s Skript |
| Vorlesen | ElevenLabs TTS (George) | MP3 als Voice-Bubble |

### 3.4 Trigger-Logik

- POI wird **einmalig** getriggert, wenn der Fahrer **<200 m** vor dem POI ist
- POI muss **auf oder direkt an der Route** sein (<50 m Abstand)
- POI wird nicht wiederholt (Dedup via `reported_facts`)
- Maximal **1 POI pro 10 Minuten** (Überflutung vermeiden)
- Priorität: Safety-Events > Wetter > POI
- **Nicht bei finish_approach** (<2 km Rest) – Konzentration aufs Ziel

### 3.5 LLM-Prompt für die Zusammenfassung

```
Fasse den folgenden Wikipedia-Artikel in 2-3 Sätzen zusammen.
Das wird einem Radfahrer vorgelesen, der sich dem Ort nähert.
Nenne den Namen, das Baujahr/die Epoche, und eine besondere Eigenschaft.
Maximal 60 Wörter. Sprache: Deutsch.

Artikel: {wikitext}
```

### 3.6 Erweiterungen

- **POI-Kategorien:** Kirche, Schloss, Aussichtspunkt, Naturdenkmal, Museum, Brücke
- **Tageszeit-abhängig:** Bäckerei morgens, Biergarten mittags, Aussichtspunkt bei Sonnenuntergang
- **Jahreszeit-abhängig:** Weihnachtsmärkte im Dezember, Kirschblüte im April
- **Fahrertyp-abhängig:** Ultra-Racer will keine POIs, Genuss-Radler will alle

### 3.7 Aufwand & Machbarkeit

| Komponente | Aufwand | Details |
|-----------|---------|---------|
| POI-Suche entlang Route | 4–6 h | Overpass-Queries in `providers.py` |
| Wikipedia-API-Adapter | 2–4 h | Neuer Provider in `providers.py` |
| LLM-Zusammenfassung | 1–2 h | Prompt-Template + Agent-Call |
| TTS-Einbindung | 1 h | ElevenLabs ist bereits integriert |
| Trigger-Logik (Event Engine) | 3–5 h | Neue Event-Kategorie `poi_narration` |
| Tests | 4–6 h | Neue Test-Datei `test_poi_narration.py` |
| **Gesamt** | **15–24 h** | **2–3 Tage @ 8h/Tag** |

### 3.8 Risiken

- **Wikipedia-Abdeckung:** Nicht jedes POI hat einen Wikipedia-Artikel. Fallback: OSM-Tags + LLM-generierte Mini-Beschreibung
- **TTS-Latenz:** ElevenLabs braucht 1–3 Sekunden für 30s Audio. Perfekt – die Ansage wird vor Trigger erzeugt (pre-compute)
- **Netz auf dem Rad:** Wikipedia-API + TTS brauchen Internet. In Funklöchern: POI überspringen
- **Kosten:** ElevenLabs TTS kostet ~$0.30/Stunde bei 5 POIs/Stunde → ~$1.50 pro Tour. Akzeptabel.

---

## 4. Weitere Ideen

### 4.1 Segment-Timing (Strava-Style)

**Idee:** Der Assistent kennt die GPX-Strecke und kann für Abschnitte die Zeit messen. "Letzte 5 km: 12:30 min, 24 km/h. 2 km/h schneller als der Gesamtschnitt."

**Aufwand:** Gering (4–6 h) – benötigt nur GPX-Segment-Erkennung + Zeitmessung im State

### 4.2 Bäckerei-Brötchen-Service 🥐

**Idee:** Morgens um 7:30 Uhr auf der Tour: "In 500 Metern ist eine Bäckerei mit Frühstücksangebot. Soll ich eine Pause einplanen?" – basierend auf OSM `shop=bakery` + `opening_hours`

**Aufwand:** Gering (2–4 h) – Erweiterung des Supply-Gap-Checks um Bäckerei-Früherkennung

### 4.3 Foto-Spot-Erkennung 📸

**Idee:** POIs mit `tourism=viewpoint` + Sonnenstand-Berechnung: "In 2 km kommt ein Aussichtspunkt – bei dem Licht jetzt perfekt für ein Foto."

**Aufwand:** Mittel (6–8 h) – Sonnenstandsberechnung + Golden-Hour-Erkennung

### 4.4 Touren-Tagebuch (Auto-Log)

**Idee:** Nach Tour-Ende schreibt der Assistent automatisch eine Zusammenfassung: gefahrene Strecke, Höhenmeter, Wetter, besondere Ereignisse, POIs. Als verschönertes HTML oder Apple Note.

**Aufwand:** Gering (4–6 h) – `tour_runtime.py` erweitern um `end_session`-Handler

---

## 5. Zusammenfassung & Priorisierung

| Idee | Aufwand | Nutzen | Prio |
|------|---------|--------|------|
| **POI-Audioführung** 🎧 | 15–24 h | ⭐⭐⭐⭐⭐ | **1** |
| **OwnTracks-Provider** 📡 | 8–12 h | ⭐⭐⭐⭐ | **2** |
| **GPS-Trace-Korrektur** 🗺️ | 8–12 h | ⭐⭐⭐⭐ | **3** |
| Segment-Timing ⏱️ | 4–6 h | ⭐⭐⭐ | 4 |
| Bäckerei-Service 🥐 | 2–4 h | ⭐⭐⭐ | 5 |
| Touren-Tagebuch 📓 | 4–6 h | ⭐⭐⭐ | 6 |
| Foto-Spot 📸 | 6–8 h | ⭐⭐ | 7 |
| Komoot-KI-Scoring 🤖 | 4–6 h | ⭐⭐ | 8 |
| Garmin LiveTrack ⌚ | 12–20 h | ⭐⭐⭐⭐ | (nur mit Garmin) |

**Highlight:** Die POI-Audioführung ist der Favorit – sie kombiniert OSM, Wikipedia, LLM und TTS zu einem zusammenhängenden Erlebnis, das auf dem Rad wirklich Spaß macht. Und die Komponenten sind alle bereits vorhanden (OSM-Provider, ElevenLabs TTS, LLM).

---

*Plan erstellt: 28.07.2026 · Basis: Hermes Tour Assistant v1.4*