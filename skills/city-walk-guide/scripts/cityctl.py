#!/usr/bin/env python3
"""Validated operational interface for the personal city-walk guide."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

CORE_SCRIPTS = Path(
    os.environ.get(
        "HERMES_LOCATION_CORE_DIR",
        str(Path(__file__).resolve().parents[2] / "location-session-core" / "scripts"),
    )
).expanduser()
if str(CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CORE_SCRIPTS))

from city_contracts import GuideRequest  # noqa: E402
from city_runtime import CityRuntime  # noqa: E402
from live_city_gate import (  # noqa: E402
    MAX_LOCATION_AGE_SECONDS,
    SNAPSHOT,
    SnapshotError,
    read_snapshot,
    select_location,
)
from location_core.providers import (  # noqa: E402
    ProviderCallError,
    build_city_registry,
)

DEFAULT_STATE_DIR = Path(
    os.environ.get(
        "HERMES_CITY_GUIDE_STATE_DIR",
        str(Path.home() / ".hermes" / "state"),
    )
).expanduser()
MAX_INPUT_BYTES = 1_000_000
DEFAULT_RETENTION_HOURS = 24.0
SENSITIVE_KEYS = {
    "lat",
    "lon",
    "latitude",
    "longitude",
    "chat_id",
    "message_id",
    "start",
    "destination",
    "route_points",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key.lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return value


def _read_json_input(path: Path) -> Any:
    if not path.is_file() or path.is_symlink():
        raise ValueError("request file must be a regular file")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("request exceeds the 1 MiB safety limit")
    return json.loads(path.read_text(encoding="utf-8"))


def _chat_id() -> str:
    return os.environ.get(
        "HERMES_CITY_GUIDE_CHAT_ID",
        os.environ.get("HERMES_TOUR_CHAT_ID", ""),
    ).strip()


def latest_location(*, now: float, state_dir: Path | None = None) -> Any:
    chat_id = _chat_id()
    if not chat_id:
        raise ValueError("missing_chat_id")
    sample = select_location(
        read_snapshot(
            SNAPSHOT
            if state_dir is None
            else state_dir / "telegram_live_locations.json"
        ),
        chat_id=chat_id,
        now=now,
        max_age_seconds=MAX_LOCATION_AGE_SECONDS,
    )
    if sample is None:
        raise ValueError("no_live_location")
    return sample


def _registry(request: GuideRequest):
    return build_city_registry(
        ors_api_key=os.environ.get("OPENROUTESERVICE_API_KEY"),
        language=request.language,
        fallback_language=request.fallback_language,
    )


def diagnose(state_dir: Path) -> dict[str, Any]:
    state_path = state_dir / "city_guide_state.json"
    files = {
        "guide_state": state_path,
        "state_lock": state_path.with_suffix(".json.lock"),
        "live_locations": state_dir / "telegram_live_locations.json",
    }
    report: dict[str, Any] = {
        "ok": True,
        "state_directory_exists": state_dir.exists(),
        "state_directory_private": None,
        "chat_configured": bool(_chat_id()),
        "openrouteservice_configured": bool(
            os.environ.get("OPENROUTESERVICE_API_KEY", "").strip()
        ),
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "files": {},
    }
    if state_dir.exists():
        report["state_directory_private"] = not bool(state_dir.stat().st_mode & 0o077)
    for name, path in files.items():
        entry: dict[str, Any] = {"exists": path.exists()}
        if path.exists():
            entry["private_permissions"] = not bool(path.stat().st_mode & 0o077)
            if name != "state_lock":
                try:
                    entry["json_valid"] = isinstance(
                        json.loads(path.read_text(encoding="utf-8")),
                        dict,
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    entry["json_valid"] = False
        report["files"][name] = entry
    required_checks = (
        report["chat_configured"],
        report["openrouteservice_configured"],
        report["ffmpeg_available"],
        report["state_directory_private"] is not False,
        all(
            entry.get("private_permissions", True)
            and entry.get("json_valid", True)
            for entry in report["files"].values()
        ),
    )
    report["ok"] = all(required_checks)
    report["quarantined_state_count"] = len(
        list(state_dir.glob("city_guide_state.json.corrupt-*"))
    )
    return report


def cleanup(
    state_dir: Path,
    *,
    older_than_hours: float = DEFAULT_RETENTION_HOURS,
    now: float | None = None,
) -> list[str]:
    if older_than_hours <= 0:
        raise ValueError("retention must be positive")
    timestamp = time.time() if now is None else now
    cutoff = timestamp - older_than_hours * 3600
    runtime = CityRuntime(state_dir / "city_guide_state.json")
    state = runtime.repository.load()
    ended_at = state["session"].get("ended_at")
    deleted: list[str] = []
    if (
        state["session"]["status"] in {"completed", "failed"}
        and ended_at is not None
        and float(ended_at) < cutoff
    ):
        runtime.repository.recover_empty()
        deleted.append("completed_city_guide_state")
    for pattern in ("*.tmp", "city_guide_state.json.corrupt-*"):
        for path in state_dir.glob(pattern):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.stat().st_mtime < cutoff
            ):
                path.unlink()
                deleted.append(path.name)
    return sorted(deleted)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes City Walk Guide operations")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument("--request", type=Path, required=True)
    subparsers.add_parser("context")
    subparsers.add_parser("next-story")
    subparsers.add_parser("more")
    skip = subparsers.add_parser("skip-stop")
    skip.add_argument("--stop-id")
    subparsers.add_parser("pause")
    subparsers.add_parser("resume")
    replan = subparsers.add_parser("replan")
    replan.add_argument("--request", type=Path)
    subparsers.add_parser("end")
    subparsers.add_parser("diagnose")
    subparsers.add_parser("capabilities")
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument(
        "--older-than-hours",
        type=float,
        default=float(
            os.environ.get(
                "HERMES_CITY_GUIDE_RETENTION_HOURS",
                str(DEFAULT_RETENTION_HOURS),
            )
        ),
    )
    return parser


def _summary(state: dict[str, Any]) -> dict[str, Any]:
    itinerary = state["itinerary"]
    return {
        "ok": True,
        "session_status": state["session"]["status"],
        "itinerary_status": itinerary["status"],
        "revision": itinerary["revision"],
        "stop_count": len(itinerary["stops"]),
        "walking_minutes": round(float(itinerary["walking_seconds"]) / 60),
        "dwell_minutes": round(float(itinerary["dwell_seconds"]) / 60),
    }


def run(args: argparse.Namespace, *, now: float | None = None) -> dict[str, Any]:
    timestamp = time.time() if now is None else now
    state_dir = args.state_dir.expanduser()
    runtime = CityRuntime(state_dir / "city_guide_state.json")
    if args.command == "diagnose":
        return diagnose(state_dir)
    if args.command == "cleanup":
        return {
            "deleted": cleanup(
                state_dir,
                older_than_hours=args.older_than_hours,
                now=timestamp,
            )
        }
    if args.command == "context":
        return runtime.agent_context()
    if args.command == "next-story":
        return runtime.next_story().to_dict()
    if args.command == "more":
        return runtime.more().to_dict()
    if args.command == "skip-stop":
        runtime.skip_stop(args.stop_id)
        return {"ok": True, "status": "skipped"}
    if args.command == "pause":
        runtime.pause()
        return {"ok": True, "status": "paused"}
    if args.command == "resume":
        runtime.resume()
        return {"ok": True, "status": "active"}
    if args.command == "end":
        runtime.end(now=timestamp)
        return {"ok": True, "status": "completed"}
    if args.command == "capabilities":
        request = GuideRequest()
        return _registry(request).capabilities()
    if args.command == "start":
        value = _read_json_input(args.request)
        if not isinstance(value, dict):
            raise ValueError("request must be a JSON object")
        request = GuideRequest.from_mapping(value)
        sample = latest_location(now=timestamp, state_dir=state_dir)
        state = runtime.plan_and_start(
            sample,
            request,
            _registry(request),
            now=timestamp,
        )
        return _summary(state)
    state = runtime.repository.load()
    request_override = None
    replan_request_path = getattr(args, "request", None)
    if replan_request_path is not None:
        value = _read_json_input(replan_request_path)
        if not isinstance(value, dict):
            raise ValueError("request must be a JSON object")
        request_override = GuideRequest.from_mapping(value)
    request = request_override or GuideRequest.from_mapping(state["preferences"])
    sample = latest_location(now=timestamp, state_dir=state_dir)
    replanned = runtime.replan(
        sample,
        _registry(request),
        request_override=request_override,
        now=timestamp,
    )
    return _summary(replanned)


def main() -> int:
    args = build_parser().parse_args()
    try:
        output = run(args)
    except (OSError, ValueError, RuntimeError, SnapshotError, ProviderCallError) as error:
        code = getattr(error, "code", None) or str(error)
        print(
            json.dumps(
                {"ok": False, "error_code": code},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 2
    print(json.dumps(redact(output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
