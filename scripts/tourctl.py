#!/usr/bin/env python3
"""Operational diagnostics and local-data retention controls."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_STATE_DIR = Path.home() / ".hermes/state"
SENSITIVE_KEYS = {"lat", "lon", "latitude", "longitude"}


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
        "gate_state": state_dir / "live_tour_gate.json",
        "live_locations": state_dir / "telegram_live_locations.json",
    }
    report: dict[str, Any] = {"ok": True, "state_dir": str(state_dir), "files": {}}
    for name, path in files.items():
        entry: dict[str, Any] = {"exists": path.exists()}
        if path.exists():
            entry["mode"] = oct(path.stat().st_mode & 0o777)
            try:
                entry["json_valid"] = isinstance(
                    json.loads(path.read_text(encoding="utf-8")), dict
                )
            except (OSError, json.JSONDecodeError):
                entry["json_valid"] = False
                report["ok"] = False
            if path.stat().st_mode & 0o077:
                entry["private_permissions"] = False
                report["ok"] = False
            else:
                entry["private_permissions"] = True
        report["files"][name] = entry
    return report


def cleanup(state_dir: Path, *, older_than_hours: float, now: float | None = None) -> list[str]:
    current_time = time.time() if now is None else now
    cutoff = current_time - older_than_hours * 3600
    deleted: list[str] = []
    patterns = ("current-tour-*.gpx", "*.tmp", "*.bak")
    for pattern in patterns:
        for path in state_dir.glob(pattern):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                deleted.append(str(path))
    return sorted(deleted)


def harden_permissions(state_dir: Path) -> list[str]:
    changed: list[str] = []
    if not state_dir.exists():
        return changed
    os.chmod(state_dir, 0o700)
    for path in state_dir.iterdir():
        if path.is_file():
            os.chmod(path, 0o600)
            changed.append(str(path))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes Tour Assistant operations")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("diagnose")
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--older-than-hours", type=float, default=48)
    subparsers.add_parser("harden-permissions")
    args = parser.parse_args()

    if args.command == "diagnose":
        print(json.dumps(redact(diagnose(args.state_dir)), ensure_ascii=False, indent=2))
        return 0
    if args.command == "cleanup":
        print(
            json.dumps(
                {"deleted": cleanup(args.state_dir, older_than_hours=args.older_than_hours)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(json.dumps({"changed": harden_permissions(args.state_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
