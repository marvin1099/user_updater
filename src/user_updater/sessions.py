"""Desktop-session discovery through systemd-logind rather than process guessing."""

from __future__ import annotations

import pwd
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DesktopUser:
    username: str
    uid: int
    gid: int
    home: Path
    runtime_dir: Path


CommandExecutor = Callable[..., subprocess.CompletedProcess[str]]
AccountLookup = Callable[[int], pwd.struct_passwd]


def detect_graphical_users(
    executor: CommandExecutor | None = None,
    account_lookup: AccountLookup | None = None,
) -> list[DesktopUser]:
    """Return distinct local users with an active X11 or Wayland logind session."""
    run = executor or subprocess.run
    lookup = account_lookup or pwd.getpwuid
    try:
        result = run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    users: dict[int, DesktopUser] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        session_id = fields[0]
        properties = _session_properties(run, session_id)
        if not _is_graphical_user_session(properties):
            continue
        try:
            uid = int(properties["User"])
            account = lookup(uid)
        except (KeyError, ValueError, LookupError):
            continue
        if uid < 1000 or uid == 65534:
            continue
        home = Path(account.pw_dir)
        if not home.is_absolute() or not home.is_dir():
            continue
        users[uid] = DesktopUser(
            username=account.pw_name,
            uid=uid,
            gid=account.pw_gid,
            home=home,
            runtime_dir=Path(f"/run/user/{uid}"),
        )
    return [users[uid] for uid in sorted(users)]


def _session_properties(run: CommandExecutor, session_id: str) -> dict[str, str]:
    try:
        result = run(
            [
                "loginctl",
                "show-session",
                session_id,
                "-p",
                "User",
                "-p",
                "Class",
                "-p",
                "Type",
                "-p",
                "State",
                "-p",
                "Remote",
                "-p",
                "Active",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    properties: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    return properties


def _is_graphical_user_session(properties: dict[str, str]) -> bool:
    return (
        properties.get("Class") == "user"
        and properties.get("Type", "").lower() in {"x11", "wayland"}
        and properties.get("State") == "active"
        and properties.get("Remote", "no") == "no"
    )
