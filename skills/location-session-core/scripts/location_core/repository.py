"""Private, locked and atomically persisted JSON state."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

StateFactory = Callable[[], dict[str, Any]]
StateTransform = Callable[[dict[str, Any]], dict[str, Any]]
StateValidator = Callable[[dict[str, Any]], None]


class CorruptStateError(RuntimeError):
    """Raised after an invalid state file has been moved to private quarantine."""

    def __init__(self, quarantine_path: Path):
        super().__init__(f"state quarantined as {quarantine_path.name}")
        self.quarantine_path = quarantine_path


class JsonStateRepository:
    """A schema-agnostic repository configured by the owning skill."""

    def __init__(
        self,
        path: Path,
        *,
        empty_factory: StateFactory,
        migrate: StateTransform,
        validate: StateValidator,
    ) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self._empty_factory = empty_factory
        self._migrate = migrate
        self._validate = validate

    def ensure_private_directory(self) -> None:
        if self.path.parent.is_symlink():
            raise OSError("state directory must not be a symbolic link")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.ensure_private_directory()
        if self.lock_path.is_symlink():
            raise OSError("state lock must not be a symbolic link")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.lock_path, flags, 0o600)
        os.chmod(self.lock_path, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _quarantine_unlocked(self) -> Path:
        quarantine = self.path.with_name(
            f"{self.path.name}.corrupt-{int(time.time())}-{os.getpid()}"
        )
        os.replace(self.path, quarantine)
        os.chmod(quarantine, 0o600)
        return quarantine

    def _load_unlocked(self) -> dict[str, Any]:
        if self.path.is_symlink():
            raise OSError("state file must not be a symbolic link")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty_factory()
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            quarantine = self._quarantine_unlocked()
            raise CorruptStateError(quarantine) from error
        if not isinstance(raw, dict):
            quarantine = self._quarantine_unlocked()
            raise CorruptStateError(quarantine)
        try:
            return self._migrate(raw)
        except (TypeError, ValueError) as error:
            quarantine = self._quarantine_unlocked()
            raise CorruptStateError(quarantine) from error

    def _save_unlocked(self, state: dict[str, Any]) -> None:
        self._validate(state)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def load(self) -> dict[str, Any]:
        with self._locked():
            return self._load_unlocked()

    def save(self, state: dict[str, Any]) -> None:
        with self._locked():
            self._save_unlocked(self._migrate(state))

    def update(
        self,
        operation: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        with self._locked():
            state = self._load_unlocked()
            updated = operation(copy.deepcopy(state))
            if updated is None:
                updated = state
            updated = self._migrate(updated)
            self._save_unlocked(updated)
            return copy.deepcopy(updated)

    def recover_empty(self) -> dict[str, Any]:
        recovered = self._empty_factory()
        with self._locked():
            self._save_unlocked(recovered)
        return recovered
