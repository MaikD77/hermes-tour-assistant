#!/usr/bin/env python3
"""OwnTracks HTTP receiver — minimal, secure, local-only endpoint.

Usage (from Hermes):
    OWNTRACKS_API_KEY=secret OWNTRACKS_PORT=9090 python3 owntracks_receiver.py

Environment variables:
    OWNTRACKS_API_KEY     Required. Bearer token for POST /owntracks.
    OWNTRACKS_PORT        Optional. Local port (default 9090).
    OWNTRACKS_STALE_SECONDS  Optional. Seconds before a location is stale (default 300).
    OWNTRACKS_RATE_LIMIT     Optional. Requests per minute per IP (default 10).
    OWNTRACKS_MAX_BODY      Optional. Maximum request body in bytes (default 4096).

Endpoints:
    POST /owntracks   Receive OwnTracks location/transition payloads.
    GET  /location    Hermes-internal query for current location.
    GET  /health      Health check (no auth required).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
from collections import defaultdict
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from models import LocationStore
from validation import PayloadError, normalize_location, normalize_transition

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_KEY = os.environ.get("OWNTRACKS_API_KEY")
if not API_KEY:
    print("FATAL: OWNTRACKS_API_KEY environment variable is required", file=sys.stderr)
    sys.exit(1)

PORT = int(os.environ.get("OWNTRACKS_PORT", "9090"))
STALE_SECONDS = int(os.environ.get("OWNTRACKS_STALE_SECONDS", "300"))
RATE_LIMIT = int(os.environ.get("OWNTRACKS_RATE_LIMIT", "10"))  # per minute per IP
MAX_BODY = int(os.environ.get("OWNTRACKS_MAX_BODY", "4096"))  # bytes

# ---------------------------------------------------------------------------
# Logging — no coordinates in application logs
# ---------------------------------------------------------------------------

class CoordinateSafeFilter(logging.Filter):
    """Drop log records that contain coordinates or raw payloads."""

    SENSITIVE_WORDS = frozenset({
        "lat", "lon", "latitude", "longitude", "coordinate", "payload",
        "secret", "api_key", "authorization",
    })

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage().lower()
        return not any(word in msg for word in self.SENSITIVE_WORDS)


app_log = logging.getLogger("owntracks")
app_log.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_handler.addFilter(CoordinateSafeFilter())
app_log.addHandler(_handler)
app_log.propagate = False

# ---------------------------------------------------------------------------
# Rate limiter (in-memory token bucket per IP)
# ---------------------------------------------------------------------------

class TokenBucket:
    def __init__(self, rate: int, window: int = 60):
        self.rate = rate
        self.window = window
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window
        timestamps = self._buckets[key]
        # Purge expired entries
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)
        if len(timestamps) >= self.rate:
            return False
        timestamps.append(now)
        return True


_rate_limiter = TokenBucket(rate=RATE_LIMIT)

# ---------------------------------------------------------------------------
# Location store
# ---------------------------------------------------------------------------

_store = LocationStore(stale_seconds=STALE_SECONDS)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OwnTracks Receiver",
    version="1.0.0",
    description="Minimal HTTPS-secured OwnTracks endpoint for Hermes Agent.",
)


# -- Middleware: request size check ------------------------------------------

@app.middleware("http")
async def request_size_middleware(request: Request, call_next):
    if request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY:
            return JSONResponse(
                status_code=413,
                content={"error": f"Request too large (max {MAX_BODY} bytes)"},
            )
    return await call_next(request)


# -- Middleware: authentication -----------------------------------------------

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in ("/health", "/location", "/history"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")

    # OwnTracks HTTP mode sends the token as Basic Auth (UserID:Password),
    # Hermes/curl uses Bearer. Accept both; the secret is always the password
    # (Basic) or the whole token (Bearer).
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
    elif auth_header.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth_header.removeprefix("Basic ").strip()).decode("utf-8")
            _, _, password = decoded.partition(":")
            token = password.strip()
        except (ValueError, UnicodeDecodeError):
            token = ""

    if not token or token != API_KEY:
        return JSONResponse(
            status_code=401,
            content={"error": "Missing or invalid Authorization"},
        )
    return await call_next(request)


# -- Middleware: rate limiting ------------------------------------------------

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Rate-limit only POST /owntracks
    if request.method == "POST" and request.url.path == "/owntracks":
        client_ip = request.client.host if request.client else "unknown"
        if not _rate_limiter.check(client_ip):
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limit exceeded — try again later"},
                headers={"Retry-After": "60"},
            )
    return await call_next(request)


# -- Endpoints ----------------------------------------------------------------

@app.get("/health")
async def health():
    loc = _store.current
    return {
        "status": "ok",
        "store": "populated" if loc else "empty",
        "stale": _store.is_stale if loc else None,
    }


@app.get("/location")
async def get_location():
    data = _store.to_dict()
    if data is None:
        return {"result": "not_found", "stale": True, "age_seconds": None}
    return {"result": "ok", **data}


@app.get("/history")
async def get_history(limit: int = 10):
    """Return the last N locations (newest first) from SQLite."""
    rows = _store.history(limit=max(1, min(limit, 1000)))
    return {"result": "ok", "count": len(rows), "locations": rows}


@app.post("/owntracks")
async def receive_owntracks(request: Request):
    # Read body and enforce size
    body = await request.body()
    if len(body) > MAX_BODY:
        return JSONResponse(
            status_code=413,
            content={"error": f"Request too large (max {MAX_BODY} bytes)"},
        )

    # Parse JSON
    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as exc:
        app_log.warning("Invalid JSON (%d bytes)", len(body))
        return JSONResponse(status_code=400, content={"error": "Invalid JSON", "detail": str(exc)})

    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"error": "Payload must be a JSON object"})

    _type = payload.get("_type")
    if _type == "location":
        return _handle_location(payload, body)
    elif _type == "transition":
        return _handle_transition(payload)
    elif _type == "status":
        app_log.info("Status ping from %s", payload.get("topic", "?"))
        return JSONResponse(content={
            "_type": "status",
            "tst": int(time.time()),
            "topic": payload.get("topic", "owntracks/maik/iphone"),
        })
    else:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported _type: {_type!r}. Supported: 'location', 'transition'"},
        )


def _handle_location(payload: dict, raw_body: bytes) -> Response:
    try:
        location = normalize_location(payload)
    except PayloadError as exc:
        app_log.warning("Validation error: %s", exc)
        return JSONResponse(status_code=400, content={"error": str(exc)})

    _store.update(location)

    # Application log: metadata only, no coordinates
    app_log.info(
        "Location accepted | device=%s acc=%s batt=%s trigger=%s conn=%s age=%.0fs",
        location.device_id,
        f"{int(location.accuracy_m)}m" if location.accuracy_m is not None else "?",
        f"{location.battery_percent}%" if location.battery_percent is not None else "?",
        location.trigger or "?",
        location.connection_type or "?",
        location.age_seconds,
    )

    # Return a valid OwnTracks message so the app doesn't break on schema validation.
    return JSONResponse(content={
        "_type": "status",
        "tst": int(time.time()),
        "topic": f"owntracks/{location.device_id}",
    })


def _handle_transition(payload: dict) -> JSONResponse:
    try:
        transition = normalize_transition(payload)
    except PayloadError as exc:
        app_log.warning("Transition validation error: %s", exc)
        return JSONResponse(status_code=400, content={"error": str(exc)})

    app_log.info("Transition acknowledged | event=%s", transition.get("event") or "?")
    return JSONResponse(content={
        "_type": "status",
        "tst": int(time.time()),
        "topic": "owntracks/maik/iphone",
    })


# -- Main --------------------------------------------------------------------

def main() -> None:
    app_log.info("Starting OwnTracks receiver on port %d", PORT)
    app_log.info("Stale threshold: %ds | Rate limit: %d/min | Max body: %dB", STALE_SECONDS, RATE_LIMIT, MAX_BODY)
    uvicorn.run(
        "owntracks_receiver:app",
        host="0.0.0.0",
        port=PORT,
        log_level="warning",
        access_log=False,
        reload=False,
    )


if __name__ == "__main__":
    main()