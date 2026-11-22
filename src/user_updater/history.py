"""Bounded durable summaries of completed update runs."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .state import UpdateState

_TERMINAL_PHASES = {"succeeded", "failed"}
_OPTIONAL_TEXT_FIELDS = {
    "run_id",
    "scheduled_for",
    "started_at",
    "finished_at",
}


class HistoryError(ValueError):
    """Raised when persisted run history cannot be trusted."""


@dataclass(frozen=True)
class RunRecord:
    task: str
    run_id: str | None
    phase: str
    scheduled_for: str | None
    started_at: str | None
    finished_at: str | None
    returncode: int | None
    message: str


class RunHistoryStore:
    """Atomically persist a bounded, newest-first list of run summaries."""

    def __init__(
        self,
        path: Path,
        *,
        file_mode: int = 0o600,
        limit: int = 20,
        owner_uid: int | None = None,
        owner_gid: int | None = None,
    ) -> None:
        if type(limit) is not int or limit < 1:
            raise ValueError("history limit must be a positive integer")
        self.path = Path(path)
        self.file_mode = file_mode
        self.limit = limit
        self.owner_uid = owner_uid
        self.owner_gid = owner_gid

    def ensure_permissions(self) -> None:
        """Apply this store's owner and mode to an existing history file."""
        try:
            flags = os.O_RDONLY | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise HistoryError(f"cannot secure run history {self.path}: {exc}") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise HistoryError(f"run history path is not a regular file: {self.path}")
            self._set_owner(descriptor)
            os.fchmod(descriptor, self.file_mode)
        except OSError as exc:
            raise HistoryError(f"cannot secure run history {self.path}: {exc}") from exc
        finally:
            os.close(descriptor)

    def load(self) -> tuple[RunRecord, ...]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, json.JSONDecodeError) as exc:
            raise HistoryError(f"cannot read run history {self.path}: {exc}") from exc

        if (
            not isinstance(raw, dict)
            or type(raw.get("version")) is not int
            or raw.get("version") != 1
            or not isinstance(raw.get("runs"), list)
        ):
            raise HistoryError("run history must contain version 1 and a runs list")
        try:
            records = tuple(_record_from_mapping(item) for item in raw["runs"])
            return _retain_latest_per_task(records, self.limit)
        except HistoryError:
            raise
        except (KeyError, TypeError) as exc:
            raise HistoryError(f"invalid run history: {exc}") from exc

    def append(self, task: str, state: UpdateState) -> None:
        if not task or state.phase not in _TERMINAL_PHASES:
            raise ValueError("history accepts only named, completed run states")
        record = RunRecord(
            task=task,
            run_id=state.run_id,
            phase=state.phase,
            scheduled_for=state.scheduled_for,
            started_at=state.started_at,
            finished_at=state.finished_at,
            returncode=state.returncode,
            message=state.message,
        )
        existing = list(self.load())
        identity = _record_identity(record)
        records = [item for item in existing if _record_identity(item) != identity]
        records.insert(0, record)
        self._write(_retain_latest_per_task(records, self.limit))

    def _write(self, records: tuple[RunRecord, ...]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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
                json.dump(
                    {"version": 1, "runs": [asdict(record) for record in records]},
                    stream,
                    ensure_ascii=False,
                    sort_keys=True,
                )
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
            raise HistoryError(f"cannot write run history {self.path}: {exc}") from exc

    def _set_owner(self, descriptor: int) -> None:
        if self.owner_uid is None and self.owner_gid is None:
            return
        os.fchown(
            descriptor,
            -1 if self.owner_uid is None else self.owner_uid,
            -1 if self.owner_gid is None else self.owner_gid,
        )


def _record_from_mapping(raw: Any) -> RunRecord:
    required = {
        "task",
        "run_id",
        "phase",
        "scheduled_for",
        "started_at",
        "finished_at",
        "returncode",
        "message",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise HistoryError("each history record must have the expected fields")
    if not isinstance(raw["task"], str) or not raw["task"]:
        raise HistoryError("history task must be a non-empty string")
    if raw["phase"] not in _TERMINAL_PHASES:
        raise HistoryError("history phase must be succeeded or failed")
    for name in _OPTIONAL_TEXT_FIELDS:
        if raw[name] is not None and not isinstance(raw[name], str):
            raise HistoryError(f"history {name} must be a string or null")
    if not isinstance(raw["message"], str):
        raise HistoryError("history message must be a string")
    if raw["returncode"] is not None and type(raw["returncode"]) is not int:
        raise HistoryError("history returncode must be an integer or null")
    return RunRecord(**raw)


def _record_identity(record: RunRecord) -> tuple[str, str | None, str | None, str | None]:
    return (record.task, record.run_id, record.started_at, record.finished_at)


def _retain_latest_per_task(records: tuple[RunRecord, ...] | list[RunRecord], limit: int):
    counts: dict[str, int] = {}
    retained: list[RunRecord] = []
    for record in records:
        count = counts.get(record.task, 0)
        if count >= limit:
            continue
        retained.append(record)
        counts[record.task] = count + 1
    return tuple(retained)


def history_path_for_state(state_path: Path) -> Path:
    state_path = Path(state_path)
    return state_path.with_name(f"{state_path.stem}.history.json")
