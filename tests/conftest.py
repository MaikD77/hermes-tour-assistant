from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gate_module() -> ModuleType:
    return load_module(REPOSITORY_ROOT / "scripts" / "live_tour_gate.py", "live_tour_gate")


@pytest.fixture
def tour_state_module() -> ModuleType:
    return load_module(REPOSITORY_ROOT / "scripts" / "tour_state.py", "tour_state")


@pytest.fixture
def simple_gpx() -> Path:
    return REPOSITORY_ROOT / "tests" / "fixtures" / "simple-route.gpx"
