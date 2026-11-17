"""Owner-controlled per-tool preferences bounded by the administrator's allow-list."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

_USER_TOOLS = frozenset({"flatpak", "pipx", "pip", "npm", "cargo", "gem"})
_MAX_FILE_BYTES = 4096


class ToolPreferenceError(ValueError):
    """Raised when personal tool preferences cannot be trusted or saved."""


class UserToolPreferenceStore:
    """Read and write one user's selected subset of administrator-enabled tools."""

    def __init__(self, path: Path, *, owner_uid: int) -> None:
        self.path = Path(path)
        self.owner_uid = owner_uid

    def load(self, admin_allowed: tuple[str, ...]) -> tuple[str, ...]:
        allowed = _validate_tool_list(admin_allowed, "administrator tool list")
        try:
            flags = os.O_RDONLY | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != self.owner_uid:
                    raise ToolPreferenceError("tool preferences must be a user-owned regular file")
                if metadata.st_size > _MAX_FILE_BYTES:
                    raise ToolPreferenceError("tool preferences file is too large")
                content = stream.read(_MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return allowed
        except OSError as exc:
            raise ToolPreferenceError(f"cannot read tool preferences {self.path}: {exc}") from exc

        if len(content) > _MAX_FILE_BYTES:
            raise ToolPreferenceError("tool preferences file is too large")
        try:
            raw: Any = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ToolPreferenceError(f"invalid tool preferences: {exc}") from exc
        if (
            not isinstance(raw, dict)
            or type(raw.get("version")) is not int
            or raw.get("version") != 1
            or set(raw) != {"version", "tools"}
        ):
            raise ToolPreferenceError("tool preferences must contain version 1 and a tools list")
        selected = _validate_tool_list(raw["tools"], "user tool list")
        selected_set = set(selected)
        return tuple(tool for tool in allowed if tool in selected_set)

    def save(
        self,
        selected_tools: tuple[str, ...],
        admin_allowed: tuple[str, ...],
    ) -> tuple[str, ...]:
        if os.getuid() != self.owner_uid:
            raise ToolPreferenceError("only the owning user may change personal tool preferences")
        allowed = _validate_tool_list(admin_allowed, "administrator tool list")
        selected = _validate_tool_list(selected_tools, "user tool list")
        if not set(selected).issubset(allowed):
            raise ToolPreferenceError("users cannot enable tools disabled by administrator policy")

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
                os.fchmod(stream.fileno(), 0o600)
                json.dump({"version": 1, "tools": list(selected)}, stream, separators=(",", ":"))
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
            raise ToolPreferenceError(f"cannot save tool preferences {self.path}: {exc}") from exc
        return tuple(tool for tool in allowed if tool in set(selected))


def _validate_tool_list(raw: Any, name: str) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ToolPreferenceError(f"{name} must be a list")
    if any(not isinstance(tool, str) or tool not in _USER_TOOLS for tool in raw):
        raise ToolPreferenceError(f"{name} contains an unsupported tool")
    if len(set(raw)) != len(raw):
        raise ToolPreferenceError(f"{name} contains duplicate tools")
    return tuple(raw)
