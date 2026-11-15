"""Filesystem locations shared by the CLI, worker, and desktop client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    policy: Path
    state: Path
    builder_home: Path
    sudoers: Path
    request: Path
    log: Path


def system_paths(home: Path | None = None) -> AppPaths:
    return AppPaths(
        policy=Path("/etc/user_updater/project.yaml"),
        state=Path("/var/lib/user_updater/state.json"),
        builder_home=Path("/var/lib/user_updater/builders"),
        sudoers=Path("/etc/sudoers.d"),
        request=Path(home or Path.home()) / ".local/state/user_updater/request.json",
        log=Path("/var/lib/user_updater/update.log"),
    )


def user_paths(home: Path | None = None) -> AppPaths:
    user_home = Path(home or Path.home())
    config = user_home / ".config/user_updater"
    state = user_home / ".local/state/user_updater"
    return AppPaths(
        policy=config / "project.yaml",
        state=state / "state.json",
        builder_home=state / "builders",
        sudoers=Path("/etc/sudoers.d"),
        request=state / "request.json",
        log=state / "update.log",
    )
