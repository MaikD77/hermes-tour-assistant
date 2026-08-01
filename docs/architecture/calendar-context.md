# Calendar Context Provider

## Grenze und Modelle

`CalendarProvider` ist ein kleiner synchroner Read-Port (`provider_id`, `list_events`). Typisierte
Ergebnisse modellieren Available, Partial, Unavailable, Unauthorized, RateLimited, Invalid und
ProviderError ohne Exceptions als erwartbaren Kontrollfluss. Google kennt nur der Adapter;
Replay arbeitet offline mit synthetischen kanonischen Events. Paginierung wird vollständig
verfolgt und `singleEvents=true` lässt Google Serieninstanzen expandieren.

`CalendarEvent` ist immutable, timezone-aware, providerneutral und deterministisch identifiziert.
Es enthält Status (confirmed/tentative/cancelled), Sichtbarkeit (private/public/default),
Transparenz (busy/free), aggregierte Teilnehmerzahl und normalisierte Nutzerantwort. Cancelled
und declined sind inaktiv, wobei ein selbst organisierter Termin trotz declined berücksichtigt
wird. All-day-Enddaten folgen der exklusiven API-Semantik. Ort wird nicht geocodiert; eine rein
technische Klassifikation erkennt remote über Conference-Felder/bekannte Domains, onsite über
eine physische Ortsangabe, sonst unknown.

## Minimierung und Relevanz

Titel, Beschreibung und Ort haben feste Maximallängen. Sanitization entfernt HTML,
Steuerzeichen, URLs, E-Mail-Adressen, Telefonnummern und Zugangs-/Meeting-Codes.
Beschreibungen sind standardmäßig deaktiviert. Private Events heißen `Private event`, haben
keine Beschreibung und offenbaren nur remote/onsite/unknown. Source metadata besitzt eine
Allowlist; Rohpayloads, Listen, Links und Credentials überschreiten nie die Adaptergrenze.

Aktuell bedeutet exakt `start_at <= computed_at < end_at`. Upcoming ist strikt zukünftig und
sortiert nach Start, Busy vor Free, Confirmed vor Tentative, Event-ID. Recent ist auf den
konfigurierten Lookback begrenzt. Der Score berücksichtigt Provider-/Paginierungsstatus,
Freshness, tentative/response/private und Konflikte, nicht die Eventanzahl. All-day, Dauer,
Ort/remote, busy/free und Nähe werden deterministisch über Auswahl, Reihenfolge, Evidenz und
Traits repräsentiert; Titel werden niemals als Absicht interpretiert.

## Konflikte, Freshness und Integration

Busy-Überlappung, gleicher Start und unmittelbare Folge ohne Puffer erzeugen deterministische,
sanitisierte `CalendarConflict`s. Free erzeugt keinen harten Konflikt. Freshness misst allein das
Abrufalter: fresh ≤5, aging ≤15, stale ≤60 Minuten, danach expired (konfigurierbar).
`CalendarEvidence` und `CalendarUncertainty` enthalten nur IDs und technische Gründe.

`CurrentContext.calendar_context` ist optional (Snapshot-Schema 2). Kalenderconfidence verändert
die vier bisherigen Component-Scores nicht; technische Calendar-Traits werden nur separat
übernommen. Deaktiviert entsteht keine Warnung. Ein aktivierter, nicht verfügbarer Provider
liefert einen Unknown-Teilkontext statt Gesamtausfall.

## State, Betrieb und Rollout

Persistiert wird höchstens der letzte sanitisierten CalendarContext-Snapshot, atomar, privat,
gelockt und symlink-sicher über die bestehende State-Repository-Grenze. Keine Historie, Tokens
oder Rohantworten. `calendar reset` berührt keinen anderen State. Shadow Mode und Delivery=false
sind diagnostizierbar.

Rollout: (1) Unit Tests, (2) Replay, (3) Google-Testkalender, (4) read-only Scope prüfen,
(5) Shadow Mode, (6) Context mit realen Terminen vergleichen, (7) Privacy Review, (8) erst in
einem späteren Sprint Ort/ETA/Decision verknüpfen. Rollback deaktiviert die Integration und setzt
nur Calendar-State zurück.
