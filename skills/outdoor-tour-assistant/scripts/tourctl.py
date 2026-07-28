#!/usr/bin/env python3
"""Operational and agent-facing interface for the canonical tour runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from event_engine import TourEvent  # noqa: E402
from providers import (  # noqa: E402
    ProviderCallError,
    ProviderRegistry,
    ProviderRunner,
    build_default_registry,
)
from tour_runtime import TourRuntime  # noqa: E402

DEFAULT_STATE_DIR = Path(
    os.environ.get(
        "HERMES_TOUR_STATE_DIR",
        str(Path.home() / ".hermes" / "state"),
    )
).expanduser()
SENSITIVE_KEYS = {
    "lat",
    "lon",
    "latitude",
    "longitude",
    "chat_id",
    "message_id",
}
MAX_INPUT_BYTES = 1_000_000
DEFAULT_ROUTE_DIR = os.environ.get("HERMES_TOUR_ROUTE_DIR")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key.lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def diagnose(state_dir: Path) -> dict[str, Any]:
    files = {
        "assistant_state": state_dir / "live_tour_assistant.json",
        "state_lock": state_dir / "live_tour_assistant.json.lock",
        "live_locations": state_dir / "telegram_live_locations.json",
    }
    report: dict[str, Any] = {
        "ok": True,
        "state_dir": str(state_dir),
        "state_dir_private": None,
        "files": {},
    }
    if state_dir.exists():
        report["state_dir_private"] = not bool(state_dir.stat().st_mode & 0o077)
        if not report["state_dir_private"]:
            report["ok"] = False
    for name, path in files.items():
        entry: dict[str, Any] = {"exists": path.exists()}
        if path.exists():
            entry["mode"] = oct(path.stat().st_mode & 0o777)
            if name != "state_lock":
                try:
                    entry["json_valid"] = isinstance(
                        json.loads(path.read_text(encoding="utf-8")),
                        dict,
                    )
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    entry["json_valid"] = False
                    report["ok"] = False
            entry["private_permissions"] = not bool(path.stat().st_mode & 0o077)
            if not entry["private_permissions"]:
                report["ok"] = False
        report["files"][name] = entry
    report["quarantined_state_count"] = len(
        list(state_dir.glob("live_tour_assistant.json.corrupt-*"))
    )
    return report


def cleanup(
    state_dir: Path,
    *,
    older_than_hours: float,
    now: float | None = None,
) -> list[str]:
    if older_than_hours <= 0:
        raise ValueError("retention must be positive")
    current_time = time.time() if now is None else now
    cutoff = current_time - older_than_hours * 3600
    protected: set[Path] = set()
    state_path = state_dir / "live_tour_assistant.json"
    if state_path.exists():
        try:
            active_state = TourRuntime(state_path).repository.load()
            gpx_path = active_state["route"].get("gpx_path")
            if gpx_path:
                protected.add(Path(gpx_path).resolve())
        except (RuntimeError, OSError, ValueError):
            pass
    deleted: list[str] = []
    patterns = (
        "current-tour-*.gpx",
        "*.tmp",
        "*.bak",
        "live_tour_assistant.json.corrupt-*",
    )
    for pattern in patterns:
        for path in state_dir.glob(pattern):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.resolve() not in protected
                and path.stat().st_mtime < cutoff
            ):
                path.unlink()
                deleted.append(str(path))
    return sorted(deleted)


def harden_permissions(state_dir: Path) -> list[str]:
    changed: list[str] = []
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state_dir, 0o700)
    for path in state_dir.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_dir():
            os.chmod(path, 0o700)
        elif path.is_file():
            os.chmod(path, 0o600)
            changed.append(str(path))
    return changed


def _read_json_input(path: Path) -> Any:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("input exceeds the 1 MiB safety limit")
    return json.loads(path.read_text(encoding="utf-8"))


def _runtime(state_dir: Path) -> TourRuntime:
    return TourRuntime(state_dir / "live_tour_assistant.json")


def _weather_current(
    runtime: TourRuntime,
    *,
    now: float,
    registry: ProviderRegistry | None = None,
) -> tuple[int, dict[str, Any]]:
    state = runtime.repository.load()
    position = state.get("position")
    if not position:
        return 2, {"ok": False, "error_code": "no_current_position"}
    providers = build_default_registry() if registry is None else registry
    provider = providers.resolve("weather.current")
    runner = ProviderRunner("open-meteo")
    try:
        snapshot = runner.call(
            lambda: provider.current_conditions(
                float(position["lat"]),
                float(position["lon"]),
            ),
            now=now,
        )
    except ProviderCallError as error:
        def failed(current: dict[str, Any]) -> dict[str, Any]:
            current["provider_health"]["open-meteo"] = runner.health.to_dict()
            return current

        runtime.repository.update(failed)
        return 2, {"ok": False, "error_code": error.code}

    def succeeded(current: dict[str, Any]) -> dict[str, Any]:
        current["weather"] = asdict(snapshot)
        current["provider_health"]["open-meteo"] = runner.health.to_dict()
        return current

    runtime.repository.update(succeeded)
    return 0, {"ok": True, "weather": asdict(snapshot)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes Tour Assistant operations")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("diagnose")
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--older-than-hours", type=float, default=48)
    subparsers.add_parser("harden-permissions")
    context_parser = subparsers.add_parser("context")
    context_parser.add_argument("--include-location", action="store_true")
    attach_parser = subparsers.add_parser("attach-route")
    attach_parser.add_argument("--provider", required=True)
    attach_parser.add_argument("--route-id", required=True)
    attach_parser.add_argument("--name", required=True)
    attach_parser.add_argument("--gpx-path", type=Path, required=True)
    route_status = subparsers.add_parser("route-status")
    route_status.add_argument(
        "status",
        choices=("unknown", "matching", "ambiguous", "unmatched", "failed"),
    )
    route_status.add_argument("--provider")
    route_status.add_argument("--route-id")
    route_status.add_argument("--name")
    settlements = subparsers.add_parser("set-settlements")
    settlements.add_argument("--input", type=Path, required=True)
    event = subparsers.add_parser("record-event")
    event.add_argument("--input", type=Path, required=True)
    next_alert = subparsers.add_parser("next-alert")
    next_alert.add_argument("--now", type=float, default=None)
    weather = subparsers.add_parser("weather-current")
    weather.add_argument("--now", type=float, default=None)
    subparsers.add_parser("capabilities")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    state_dir = args.state_dir.expanduser()
    runtime = _runtime(state_dir)
    redact_output = True
    if args.command == "diagnose":
        output = redact(diagnose(state_dir))
    elif args.command == "cleanup":
        output = {"deleted": cleanup(state_dir, older_than_hours=args.older_than_hours)}
    elif args.command == "harden-permissions":
        output = {"changed": harden_permissions(state_dir)}
    elif args.command == "context":
        output = runtime.agent_context(include_location=args.include_location)
        redact_output = not args.include_location
    elif args.command == "attach-route":
        output = runtime.attach_route(
            provider=args.provider,
            route_id=args.route_id,
            name=args.name,
            gpx_path=args.gpx_path,
        )
    elif args.command == "route-status":
        output = runtime.set_route_status(
            args.status,
            provider=args.provider,
            route_id=args.route_id,
            name=args.name,
        )
    elif args.command == "set-settlements":
        values = _read_json_input(args.input)
        if not isinstance(values, list):
            raise ValueError("settlement input must be a JSON list")
        output = runtime.set_verified_settlements(values)
    elif args.command == "record-event":
        value = _read_json_input(args.input)
        if not isinstance(value, dict):
            raise ValueError("event input must be a JSON object")
        runtime.record_event(TourEvent(**value))
        output = {"ok": True, "event_id": value.get("event_id")}
    elif args.command == "next-alert":
        now = time.time() if args.now is None else args.now
        output = asdict(runtime.next_alert(now=now))
    elif args.command == "weather-current":
        now = time.time() if args.now is None else args.now
        return_code, output = _weather_current(runtime, now=now)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return return_code
    else:
        route_directory = Path(DEFAULT_ROUTE_DIR).expanduser() if DEFAULT_ROUTE_DIR else None
        registry = build_default_registry(route_directory=route_directory)
        output = registry.capabilities()
    print(
        json.dumps(
            redact(output) if redact_output else output,
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
