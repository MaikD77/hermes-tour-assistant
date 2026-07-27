from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repository_root() -> Path:
    return REPOSITORY_ROOT


@pytest.fixture
def gate_module() -> ModuleType:
    return load_module(REPOSITORY_ROOT / "scripts" / "live_tour_gate.py", "live_tour_gate")


@pytest.fixture
def tour_state_module() -> ModuleType:
    return load_module(REPOSITORY_ROOT / "scripts" / "tour_state.py", "tour_state")


@pytest.fixture
def route_engine_module() -> ModuleType:
    return load_module(REPOSITORY_ROOT / "scripts" / "route_engine.py", "route_engine")


@pytest.fixture
def event_engine_module() -> ModuleType:
    return load_module(REPOSITORY_ROOT / "scripts" / "event_engine.py", "event_engine")


@pytest.fixture
def simple_gpx() -> Path:
    return REPOSITORY_ROOT / "tests" / "fixtures" / "simple-route.gpx"
