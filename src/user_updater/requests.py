"""Safe, per-user requests made during the pre-update response window."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UserRequest:
    run_id: str
    action: str
    hours: int | None = None
    submitted_at: str | None = None


def request_path(home: Path) -> Path:
    return Path(home) / ".local" / "state" / "user-updater" / "request.json"


def task_request_path(home: Path, task_id: str) -> Path:
    """Return an isolated request file for one independently scheduled task."""
    if not task_id or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in task_id
    ):
        raise ValueError("task_id contains unsupported characters")
    return Path(home) / ".local" / "state" / "user-updater" / "requests" / f"{task_id}.json"


def legacy_request_path(home: Path) -> Path:
    """Return the pre-rename request path for reading pending legacy requests."""
    return Path(home) / ".local" / "state" / "user_updater" / "request.json"


def legacy_task_request_path(home: Path, task_id: str) -> Path:
    """Return a pre-rename task request path for compatibility reads."""
    if not task_id or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in task_id
    ):
        raise ValueError("task_id contains unsupported characters")
    return Path(home) / ".local" / "state" / "user_updater" / "requests" / f"{task_id}.json"


def save_request(path: Path, request: UserRequest) -> None:
    """Atomically write a request owned by the desktop user."""
    payload = asdict(request)
    if payload["submitted_at"] is None:
        payload["submitted_at"] = datetime.now().astimezone().isoformat()
    if not _valid_payload(payload):
        raise ValueError("invalid update request")
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".request.", delete=False
        ) as stream:
            temporary_path = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            json.dump(payload, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def load_request(path: Path, expected_uid: int | None, expected_run_id: str) -> UserRequest | None:
    """Read a small regular file and ignore stale, foreign, or malformed requests."""
    path = Path(path)
    try:
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4096:
                return None
            if expected_uid is not None and metadata.st_uid != expected_uid:
                return None
            content = stream.read(4097)
        if len(content) > 4096:
            return None
        raw: Any = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    if not _valid_payload(raw) or raw["run_id"] != expected_run_id:
        return None
    return UserRequest(
        run_id=raw["run_id"],
        action=raw["action"],
        hours=raw["hours"],
        submitted_at=raw["submitted_at"],
    )


def _valid_payload(raw: Any) -> bool:
    if not isinstance(raw, dict) or set(raw) != {
        "run_id",
        "action",
        "hours",
        "submitted_at",
    }:
        return False
    run_id = raw["run_id"]
    action = raw["action"]
    hours = raw["hours"]
    submitted_at = raw["submitted_at"]
    if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128:
        return False
    if not isinstance(submitted_at, str):
        return False
    try:
        timestamp = datetime.fromisoformat(submitted_at)
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        return False
    if action == "start_now":
        return hours is None
    if action == "retry":
        return hours is None
    if action == "defer":
        return isinstance(hours, int) and not isinstance(hours, bool) and 1 <= hours <= 168
    return False
