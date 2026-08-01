import json
import os

import pytest
from location_core.movement import EngineState
from location_core.movement_state import MovementStateRepository
from location_core.repository import CorruptStateError


def test_private_atomic_state_and_reset(tmp_path) -> None:
    repository = MovementStateRepository(tmp_path / "movement")
    repository.save(EngineState())
    path = tmp_path / "movement/movement-state.json"
    assert path.exists()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert repository.load() == EngineState()
    repository.reset()
    assert json.loads(path.read_text())["engine"] is None


def test_corrupt_state_is_quarantined(tmp_path) -> None:
    directory = tmp_path / "movement"
    directory.mkdir()
    path = directory / "movement-state.json"
    path.write_text("not json")
    with pytest.raises(CorruptStateError):
        MovementStateRepository(directory).load()
    assert not path.exists()
    assert list(directory.glob("*.corrupt-*"))


def test_symlink_directory_is_rejected(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        MovementStateRepository(link).save(EngineState())
