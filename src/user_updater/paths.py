"""Filesystem locations shared by the CLI, worker, and desktop client."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    policy: Path
    state: Path
    user_state_dir: Path
    builder_home: Path
    sudoers: Path
    request: Path
    log_file: Path


def system_paths(home: Path | None = None) -> AppPaths:
    return AppPaths(
        policy=Path("/etc/user-updater/project.yaml"),
        state=Path("/var/lib/user-updater/state.json"),
        user_state_dir=Path("/var/lib/user-updater/users"),
        builder_home=Path("/var/lib/user-updater/builders"),
        sudoers=Path("/etc/sudoers.d"),
        request=Path(home or Path.home()) / ".local/state/user-updater/requests/user-tools.json",
        log_file=Path("/var/log/user-updater/updater.log"),
    )


def user_paths(home: Path | None = None) -> AppPaths:
    user_home = Path(home or Path.home())
    migrate_user_data_dirs(user_home)
    config = user_home / ".config/user-updater"
    state = user_home / ".local/state/user-updater"
    return AppPaths(
        policy=config / "project.yaml",
        state=state / "user-tools.json",
        user_state_dir=state,
        builder_home=state / "builders",
        sudoers=Path("/etc/sudoers.d"),
        request=state / "requests" / "user-tools.json",
        log_file=state / "updater.log",
    )


def migrate_user_data_dirs(home: Path, *, owner_uid: int | None = None) -> None:
    """Move legacy underscore-named user data into the canonical hyphenated dirs."""
    home = Path(home)
    uid = os.getuid() if owner_uid is None else owner_uid
    for parent in (".config", ".local/state"):
        legacy = home / parent / "user_updater"
        current = home / parent / "user-updater"
        if not (legacy.exists() or legacy.is_symlink()):
            continue
        if not _safe_user_path(legacy, home, uid) or not _safe_user_path(current, home, uid):
            continue
        _migrate_directory_contents(legacy, current, expected_uid=uid)


def migrate_legacy_directory(legacy: Path, current: Path) -> None:
    """Move and merge a legacy data directory without overwriting current files."""
    _migrate_directory_contents(Path(legacy), Path(current))


def _migrate_directory_contents(
    legacy: Path,
    current: Path,
    *,
    expected_uid: int | None = None,
) -> None:
    if not (legacy.exists() or legacy.is_symlink()):
        return
    if legacy.is_symlink() or not legacy.is_dir():
        raise RuntimeError(f"legacy data path must be a real directory: {legacy}")
    if expected_uid is not None and legacy.stat(follow_symlinks=False).st_uid != expected_uid:
        return
    if current.is_symlink():
        raise RuntimeError(f"current data path must not be a symbolic link: {current}")
    if not (current.exists() or current.is_symlink()):
        legacy.rename(current)
        return
    if not current.is_dir():
        raise RuntimeError(f"current data path must be a directory: {current}")
    if expected_uid is not None and current.stat(follow_symlinks=False).st_uid != expected_uid:
        return

    for entry in legacy.iterdir():
        destination = current / entry.name
        if destination.exists() or destination.is_symlink():
            if (
                entry.is_dir()
                and not entry.is_symlink()
                and destination.is_dir()
                and not destination.is_symlink()
            ):
                _migrate_directory_contents(entry, destination, expected_uid=expected_uid)
            continue
        entry.rename(destination)
    try:
        legacy.rmdir()
    except OSError:
        # Conflicting legacy files remain in place as a recoverable backup.
        pass


def _safe_user_path(path: Path, home: Path, owner_uid: int) -> bool:
    try:
        home_metadata = home.stat(follow_symlinks=False)
        if not stat.S_ISDIR(home_metadata.st_mode):
            return False
        parent = path.parent.relative_to(home)
        current = home
        for component in parent.parts:
            current /= component
            metadata = current.stat(follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner_uid:
                return False
        if path.exists() or path.is_symlink():
            metadata = path.stat(follow_symlinks=False)
            return stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == owner_uid
        return True
    except (OSError, ValueError):
        return False
