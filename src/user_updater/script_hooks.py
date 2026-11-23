"""Discovery and validation of administrator-managed system update hooks."""

from __future__ import annotations

import os
import stat
from pathlib import Path

_PHASES = ("before", "after")
_CONTEXTS = ("builder", "root")


class ScriptHookError(RuntimeError):
    """Raised when an enabled hook directory is not safe to execute."""


def ensure_hook_directories(root: Path, *, owner_uid: int | None = None) -> tuple[Path, ...]:
    """Create the four hook directories with trusted ownership and safe modes."""
    selected_owner = os.geteuid() if owner_uid is None else owner_uid
    root = Path(root).absolute()
    _check_directory(root.parent, selected_owner)
    directories = [root]
    directories.extend(root / phase for phase in _PHASES)
    directories.extend(root / phase / f"{context}.d" for phase in _PHASES for context in _CONTEXTS)

    for directory in directories:
        if directory.is_symlink():
            raise ScriptHookError(f"hook directory must not be a symbolic link: {directory}")
        directory.mkdir(mode=0o755, exist_ok=True)
        _check_directory(directory, selected_owner)
        directory.chmod(0o755)
    return tuple(root / phase / f"{context}.d" for phase in _PHASES for context in _CONTEXTS)


def hook_scripts(
    root: Path,
    phase: str,
    context: str,
    *,
    owner_uid: int = 0,
) -> tuple[Path, ...]:
    """Return sorted, executable, trusted scripts for one phase and identity."""
    if phase not in _PHASES:
        raise ValueError(f"unknown script hook phase: {phase}")
    if context not in _CONTEXTS:
        raise ValueError(f"unknown script hook execution context: {context}")

    root = Path(root).absolute()
    if root.parent.exists() or root.parent.is_symlink():
        _check_directory(
            root.parent,
            owner_uid,
            builder_access=context == "builder",
        )
    directory = root / phase / f"{context}.d"
    for candidate in (root, root / phase, directory):
        if not candidate.exists() and not candidate.is_symlink():
            return ()
        _check_directory(candidate, owner_uid, builder_access=context == "builder")

    scripts: list[Path] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise ScriptHookError(f"cannot read hook directory {directory}: {exc}") from exc

    for script in entries:
        try:
            metadata = script.lstat()
        except OSError as exc:
            raise ScriptHookError(f"cannot inspect hook {script}: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & 0o111:
            continue
        if metadata.st_uid != owner_uid or metadata.st_mode & 0o022:
            raise ScriptHookError(
                f"executable hook must be owned by UID {owner_uid} and not group/world-writable: "
                f"{script}"
            )
        if metadata.st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise ScriptHookError(f"setuid/setgid hooks are not allowed: {script}")
        if context == "builder" and metadata.st_mode & 0o005 != 0o005:
            raise ScriptHookError(
                f"builder hooks must be readable and executable by the builder: {script}"
            )
        scripts.append(script)
    return tuple(scripts)


def _check_directory(path: Path, owner_uid: int, *, builder_access: bool = False) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ScriptHookError(f"cannot inspect hook directory {path}: {exc}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or metadata.st_mode & 0o022
    ):
        raise ScriptHookError(
            f"hook directory must be owned by UID {owner_uid} and not group/world-writable: "
            f"{path}"
        )
    if builder_access and metadata.st_mode & 0o005 != 0o005:
        raise ScriptHookError(
            f"builder hook directory must be searchable by the builder: {path}"
        )
