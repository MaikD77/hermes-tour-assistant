# iOS Live Widget für den Tour-Assistenten — Machbarkeitsanalyse

> **Für Hermes:** Diese Plan-Datei dokumentiert die Recherche – keine Umsetzung, nur Analyse.

**Goal:** Prüfen, ob und wie ein Live-Widget auf dem iPhone 17 Pro Max (iOS 19+) den Tour-Assistenten-Status während einer aktiven Radtour anzeigen kann.

**Architecture:** Die Analyse untersucht vier Ansätze von »minimalem Aufwand« bis »volle native Integration« und bewertet sie nach Machbarkeit, Kosten und Wartungsaufwand.

**Tech Stack:** iOS 19+ / WidgetKit / Live Activities / Scriptable / APNs / iCloud Drive / Telegram

---

## 1. Anforderungsprofil

### Gewünschte Funktionen
- Aktuelle Geschwindigkeit (km/h)
- Zurückgelegte Strecke (km)
- Verbleibende Strecke (km)
- Nächster Ort / POI
- Tour-Name
- Live-Status: aktiv / pausiert / beendet
- Optional: Herzfrequenz (via Apple Watch / HealthKit)

### Technische Randbedingungen
- Hermes läuft auf **macOS**, nicht auf dem iPhone
- Daten müssen vom Mac → iPhone synchronisiert werden
- Widget soll sich »live« aktualisieren (wie Sport-Ticker oder Navigation)
- Kein ständiges Öffnen der Telegram-App nötig

---

## 2. Ansatz A: Telegram-Based (Minimal, kein natives Widget)

**Idee:** Telegram-Nachricht als Live-Update im Chat-Verlauf. Der Nutzer öffnet Telegram.

**✅ Vorteile:**
- Kostet nichts, kein zusätzlicher Code
- Funktioniert sofort

**❌ Nachteile:**
- Kein echtes Widget auf dem Homescreen
- Kein Live-Activity-Erlebnis
- Erfordert Öffnen der Telegram-App
- Keine Interaktion (Tap zum Starten/Stoppen)

**🔴 Fazit:** Erfüllt die Anforderung nicht.

---

## 3. Ansatz B: Scriptable Widget (Empfohlen für Prototyp)

[Scriptable](https://scriptable.app) ist eine kostenlose iOS-App, mit der man eigene Widgets in JavaScript programmieren kann. Sie unterstützt iOS-Widgets in allen Größen.

### Architektur

```
Hermes (macOS)                         iPhone
┌─────────────────┐                   ┌──────────────────┐
│ tour_state.py   │─── iCloud ──────▶ │ Scriptable       │
│ schreibt JSON   │    Drive          │ Widget liest     │
│ nach iCloud     │                   │ JSON, rendert UI │
└─────────────────┘                   └──────────────────┘
```

### Datenfluss
1. `tourctl.py` schreibt nach jedem Check ein kompaktes Widget-Update nach `~/Library/Mobile Documents/com~apple~CloudDocs/HermesWidget/status.json`
2. Scriptable-Widget auf dem iPhone liest diese Datei (iCloud synchronisiert automatisch)
3. Widget rendert Geschwindigkeit, Strecke, Ort
4. Aktualisierung: alle 1–5 Minuten (iOS-Widget-Refresh-Takt)

### Scriptable-Code (Beispiel)
```javascript
// Hermes Tour Widget — Scriptable
const fm = FileManager.iCloud()
const path = fm.joinPath(fm.documentsDirectory(), "HermesWidget/status.json")

if (!fm.fileExists(path)) {
  return "🚴 Keine aktive Tour"
}

const data = JSON.parse(fm.readString(path))

const widget = new ListWidget()
widget.addText(`🚴 ${data.speed_kmh} km/h`)
widget.addText(`${data.done_km} km · ${data.remaining_km} km noch`)
widget.addText(`📍 ${data.next_town || "—"}`)

if (data.status === "active") {
  widget.backgroundColor = Color.green()
} else {
  widget.backgroundColor = Color.gray()
}

Script.setWidget(widget)
Script.complete()
```

### ✅ Vorteile
- Kein Xcode, kein Apple-Developer-Account nötig
- In 1–2 Stunden umsetzbar
- Funktioniert sofort auf dem iPhone
- iCloud sync ist kostenlos und passiv

### ❌ Nachteile
- Keine Live Activities (nur Widget mit 1–30 min Refresh)
- Keine Dynamic Island Unterstützung
- Scriptable-App muss installiert sein
- Keine Lock-Screen-Unterstützung (nur Homescreen-Widget)

### ⏱️ Aufwand: 2–4 Stunden
### 💰 Kosten: 0 € (Scriptable ist kostenlos)

---

## 4. Ansatz C: Eigene iOS App mit WidgetKit (Professionell)

Eine native iOS-App mit WidgetKit-Extension und Live Activities (iOS 16.1+). Dies ist der Weg, den offizielle Apps wie Uber, Nike Run Club und Fußball-Apps gehen.

### Architektur

```
Hermes (macOS)                         iPhone
┌─────────────────┐                   ┌──────────────────────┐
│ tourctl.py      │─── HTTP/WebSocket │ iOS App mit         │
│ pusht Updates   │    oder APNs      │ WidgetKit + Live    │
│ an Server/APNs  │                   │ Activities +        │
└─────────────────┘                   │ Dynamic Island      │
                                      └──────────────────────┘
```

### Datenfluss (Live Activities)
1. iOS-App startet eine Live Activity mit Tour-Start-Daten
2. Hermes sendet Updates per APNs (Apple Push Notification service)
3. Live Activity aktualisiert sich auf Lock Screen + Dynamic Island
4. Bei Tour-Ende wird die Live Activity beendet

### Was wird benötigt
- **Apple Developer Program:** $99/Jahr (für Push Notifications + Live Activities)
- **Xcode-Projekt:** iOS App + Widget Extension + Live Activity Target
- **Server-Komponente:** Einfacher HTTP-Server auf dem Mac (oder Cloud-Webhook), der APNs-Pushes auslöst
- **APNs-Zertifikat/Key:** Für Push-Berechtigung

### Code-Skizze (Swift, Widget)
```swift
struct TourLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: TourAttributes.self) { context in
            TourLockScreenView(
                speed: context.state.speed,
                distance: context.state.distance,
                remaining: context.state.remaining
            )
        }
    }
}
```

### ✅ Vorteile
- Echte Live Activities mit Dynamic Island
- Lock-Screen + Homescreen + Standby-Modus
- Professionell, erweiterbar (HealthKit, Apple Watch, Komoot-Integration)
- App-Store-Release möglich

### ❌ Nachteile
- $99/Jahr für Developer Account
- Erheblicher Entwicklungsaufwand (SwiftUI, WidgetKit, APNs)
- Wartung: iOS-Updates können API-Änderungen bringen
- Muss alle 8–12 Stunden im App Store reviewed werden

### ⏱️ Aufwand: 40–80 Stunden
### 💰 Kosten: $99/Jahr + Entwicklungszeit

---

## 5. Ansatz D: Hybrid — Shortcuts + Scriptable (Low-Code)

**Idee:** iOS Shortcuts (die eingebaute Automatisierungs-App) kombiniert mit Scriptable-Widget. Shortcuts können auf dem Mac via SSH oder iCloud-Dateien getriggert werden.

### Architektur
```
Hermes ──iCloud──▶ JSON-Datei ──▶ Scriptable Widget ──▶ Homescreen
                        │
Shortcuts-Automation ───┘ (liest Datei, zeigt Notification)
```

### ✅ Vorteile
- Kein zusätzlicher Code
- Shortcuts kann auch Lock-Screen-Notifications

### ❌ Nachteile
- Shortcuts-UI ist unflexibel
- Kein Live-Activity-Feeling
- Nicht wirklich ein Widget

### ⏱️ Aufwand: 1 Stunde
### 💰 Kosten: 0 €

---

## 6. Bewertung & Empfehlung

| Ansatz | Aufwand | Kosten | Live-Gefühl | Wartung |
|--------|---------|--------|-------------|---------|
| **A: Telegram** | 0 h | 0 € | ❌ | ⭐ |
| **B: Scriptable** | **2–4 h** | **0 €** | ⚠️ (1–5 min Refresh) | ⭐⭐ |
| **C: Native App** | 40–80 h | $99/Jahr | ✅✅ | ⭐⭐⭐ |
| **D: Shortcuts** | 1 h | 0 € | ❌ | ⭐ |

### Empfohlene Strategie

**Phase 1 — Scriptable Prototyp (2–4 h, 0 €)**
- Schnellster Weg zu einem echten iOS-Widget
- Zeigt, ob das Widget在日常gebrauch taugt
- Liefert Erfahrung, was wirklich wichtig ist (welche Daten, wie oft aktualisieren)

**Phase 2 — Native App (nur wenn Phase 1 überzeugt)**
- $99/Jahr investieren, wenn der Nutzen klar ist
- Live Activities + Dynamic Island als echtes Premium-Feature
- App-Store-Veröffentlichung für andere Hermes-Nutzer möglich

---

## 7. Risiken & Offene Fragen

### Technische Risiken
- **iCloud-Sync-Latenz:** iCloud Drive synchronisiert nicht in Echtzeit – Verzögerung von 2–30 Sekunden möglich
- **Scriptable-Refresh:** iOS erzwingt minimum Refresh-Intervalle (oft 15–30 min im Hintergrund)
- **APNs-Zuverlässigkeit:** Push-basierte Live Activities können bei schlechtem Empfang ausfallen (auf dem Rad relevant!)
- **Batterie:** Live Activities haben minimalen Batterieverbrauch, aber ständiges iCloud-Pollen ggf. nicht

### Offene Fragen
1. **Widget-Refresh-Takt:** Wie schnell muss das Widget aktualisieren? 1 min? 5 min? 15 min?
2. **Welche Daten sind wirklich wichtig?** Speed + Strecke + Ort reichen, oder auch Herzfrequenz / Höhe?
3. **Soll das Widget interaktiv sein?** Tap öffnet Telegram / Komoot?
4. **Dynamic Island gewünscht?** (Nur mit nativem App-Ansatz möglich)
5. **Standby-Mode?** iOS 17+ hat einen Standby-Modus – Live Activities funktionieren dort besonders gut

---

## 8. Nächste Schritte

1. Entscheidung: Reicht **Scriptable-Prototyp** (Phase 1) für den Start?
2. Wenn ja:
   - Scriptable-Code für das Widget schreiben
   - Hermes-seitiges Export-Script (`tourctl.py widget-update`) bauen
   - Test auf der nächsten Tour
3. Wenn nein → Phase-2-Plan mit SwiftUI + WidgetKit ausarbeiten

---

*Plan erstellt: 28.07.2026*