"""Durable, atomic state shared by the system worker and desktop clients."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class StateError(ValueError):
    """Raised when persisted update state is malformed."""


@dataclass(frozen=True)
class UpdateState:
    phase: str = "idle"
    next_scheduled_at: str | None = None
    run_id: str | None = None
    scheduled_for: str | None = None
    response_deadline: str | None = None
    deferred_until: str | None = None
    deferrals_used: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    returncode: int | None = None
    message: str = "Waiting for the next scheduled update."
    last_output: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Any) -> UpdateState:
        if not isinstance(raw, dict):
            raise StateError("state must be a JSON object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(raw) - allowed
        if unknown:
            raise StateError(f"unknown state field: {sorted(unknown)[0]}")
        values = dict(raw)
        output = values.get("last_output", [])
        if not isinstance(output, list) or any(not isinstance(line, str) for line in output):
            raise StateError("last_output must be a list of strings")
        values["last_output"] = tuple(output)
        try:
            return cls(**values)
        except TypeError as exc:
            raise StateError(f"invalid state fields: {exc}") from exc


class StateStore:
    """Read and atomically replace one JSON state document."""

    def __init__(self, path: Path, file_mode: int = 0o644) -> None:
        self.path = Path(path)
        self.file_mode = file_mode

    def load(self) -> UpdateState:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return UpdateState()
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"cannot read state {self.path}: {exc}") from exc
        return UpdateState.from_mapping(raw)

    def save(self, state: UpdateState) -> None:
        self.path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        payload = asdict(state)
        payload["last_output"] = list(state.last_output)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                os.fchmod(stream.fileno(), self.file_mode)
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise StateError(f"cannot write state {self.path}: {exc}") from exc
