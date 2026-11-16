"""Filesystem locations shared by the CLI, worker, and desktop client."""

from __future__ import annotations

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
        policy=Path("/etc/user_updater/project.yaml"),
        state=Path("/var/lib/user_updater/state.json"),
        user_state_dir=Path("/var/lib/user_updater/users"),
        builder_home=Path("/var/lib/user_updater/builders"),
        sudoers=Path("/etc/sudoers.d"),
        request=Path(home or Path.home()) / ".local/state/user_updater/requests/user-tools.json",
        log_file=Path("/var/log/user-updater/updater.log"),
    )


def user_paths(home: Path | None = None) -> AppPaths:
    user_home = Path(home or Path.home())
    config = user_home / ".config/user_updater"
    state = user_home / ".local/state/user_updater"
    return AppPaths(
        policy=config / "project.yaml",
        state=state / "user-tools.json",
        user_state_dir=state,
        builder_home=state / "builders",
        sudoers=Path("/etc/sudoers.d"),
        request=state / "requests" / "user-tools.json",
        log_file=state / "updater.log",
    )
