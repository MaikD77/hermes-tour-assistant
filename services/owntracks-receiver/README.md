# OwnTracks HTTP Receiver für Hermes Tour Assistant

Minimaler, sicherer HTTP-Endpunkt zur Aufnahme von OwnTracks-Standortdaten vom iPhone.

## Architektur

```
iPhone (OwnTracks) ──VPN/Tailscale──► HTTPS ──► tls_proxy (9443)
                                                    │
                                                    ▼
                                            owntracks_receiver (9090)
                                                    │
                                                    ▼
                                             LocationStore (RAM + SQLite)
                                                    │
                                                    ▼
                                     GET /location (Hermes Tour Gate)
```

## Endpunkte

| Methode | Pfad         | Auth        | Beschreibung                                         |
|---------|--------------|-------------|------------------------------------------------------|
| `POST`  | `/owntracks` | Bearer Token | OwnTracks Payload empfangen (location/transition)   |
| `GET`   | `/location`  | Nein        | Aktuellen Standort inkl. Alter und Genauigkeit      |
| `GET`   | `/health`    | Nein        | Health-Check                                         |
| `GET`   | `/history`   | Nein        | Letzte N Standorte (SQLite)                          |

## Installation

```bash
# Abhängigkeiten
pip install -r services/owntracks-receiver/requirements.txt

# API-Key generieren
openssl rand -hex 32 > ~/.hermes/owntracks/.api_key

# Receiver starten
OWNTRACKS_API_KEY="$(cat ~/.hermes/owntracks/.api_key)" \
  python3 services/owntracks-receiver/owntracks_receiver.py
```

Alternative: `bash scripts/owntracks-start.sh`

## Sicherheit

- Koordinaten erscheinen nicht in Logs (`CoordinateSafeFilter`)
- Request-Limit: 10 POSTs/min/IP
- Max Body: 4 KB
- Bearer-Token aus Umgebungsvariable
- SQLite-Persistenz für Wiederherstellung nach Neustart
- Standorte älter als 300s gelten als stale