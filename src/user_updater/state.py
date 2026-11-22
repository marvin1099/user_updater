"""Durable, atomic state shared by the system worker and desktop clients."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_PHASES = {"idle", "awaiting_response", "deferred", "running", "succeeded", "failed"}
_OPTIONAL_TEXT_FIELDS = {
    "next_scheduled_at",
    "run_id",
    "scheduled_for",
    "response_deadline",
    "deferred_until",
    "started_at",
    "finished_at",
    "retry_group_id",
    "retry_after",
}


class StateError(ValueError):
    """Raised when persisted update state is malformed."""


@dataclass(frozen=True)
class UpdateState:
    phase: str = "idle"
    next_scheduled_at: str | None = None
    run_id: str | None = None
    retry_group_id: str | None = None
    automatic_retries_used: int = 0
    manual_retries_used: int = 0
    retry_after: str | None = None
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
        phase = values.get("phase", "idle")
        if not isinstance(phase, str) or phase not in _PHASES:
            raise StateError(f"invalid state phase: {phase!r}")
        for name in _OPTIONAL_TEXT_FIELDS:
            value = values.get(name)
            if value is not None and not isinstance(value, str):
                raise StateError(f"{name} must be a string or null")
        if not isinstance(values.get("message", "Waiting for the next scheduled update."), str):
            raise StateError("message must be a string")
        deferrals_used = values.get("deferrals_used", 0)
        if type(deferrals_used) is not int or deferrals_used < 0:
            raise StateError("deferrals_used must be a non-negative integer")
        for name in ("automatic_retries_used", "manual_retries_used"):
            retry_count = values.get(name, 0)
            if type(retry_count) is not int or retry_count < 0:
                raise StateError(f"{name} must be a non-negative integer")
        returncode = values.get("returncode")
        if returncode is not None and type(returncode) is not int:
            raise StateError("returncode must be an integer or null")
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

    def __init__(
        self,
        path: Path,
        file_mode: int = 0o644,
        *,
        owner_uid: int | None = None,
        owner_gid: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.file_mode = file_mode
        self.owner_uid = owner_uid
        self.owner_gid = owner_gid

    def ensure_permissions(self) -> None:
        """Apply this store's owner and mode to an existing state file."""
        try:
            flags = os.O_RDONLY | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StateError(f"cannot secure state {self.path}: {exc}") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise StateError(f"state path is not a regular file: {self.path}")
            self._set_owner(descriptor)
            os.fchmod(descriptor, self.file_mode)
        except OSError as exc:
            raise StateError(f"cannot secure state {self.path}: {exc}") from exc
        finally:
            os.close(descriptor)

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
                self._set_owner(stream.fileno())
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

    def _set_owner(self, descriptor: int) -> None:
        if self.owner_uid is None and self.owner_gid is None:
            return
        os.fchown(
            descriptor,
            -1 if self.owner_uid is None else self.owner_uid,
            -1 if self.owner_gid is None else self.owner_gid,
        )
