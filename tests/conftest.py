from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = (
    REPOSITORY_ROOT
    / "skills"
    / "outdoor-tour-assistant"
    / "scripts"
)


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
    return load_module(RUNTIME_DIR / "live_tour_gate.py", "live_tour_gate")


@pytest.fixture
def tour_state_module() -> ModuleType:
    return load_module(RUNTIME_DIR / "tour_state.py", "tour_state")


@pytest.fixture
def route_engine_module() -> ModuleType:
    return load_module(RUNTIME_DIR / "route_engine.py", "route_engine")


@pytest.fixture
def event_engine_module() -> ModuleType:
    return load_module(RUNTIME_DIR / "event_engine.py", "event_engine")


@pytest.fixture
def providers_module() -> ModuleType:
    return load_module(RUNTIME_DIR / "providers.py", "providers")


@pytest.fixture
def tour_runtime_module() -> ModuleType:
    return load_module(RUNTIME_DIR / "tour_runtime.py", "tour_runtime")


@pytest.fixture
def tourctl_module() -> ModuleType:
    return load_module(RUNTIME_DIR / "tourctl.py", "tourctl")


@pytest.fixture
def contracts_module() -> ModuleType:
    return load_module(RUNTIME_DIR / "contracts.py", "contracts")


@pytest.fixture
def output_safety_module() -> ModuleType:
    return load_module(RUNTIME_DIR / "output_safety.py", "output_safety")


@pytest.fixture
def prepare_tour_module() -> ModuleType:
    return load_module(RUNTIME_DIR / "prepare_tour.py", "prepare_tour")


@pytest.fixture
def simple_gpx() -> Path:
    return REPOSITORY_ROOT / "tests" / "fixtures" / "simple-route.gpx"
