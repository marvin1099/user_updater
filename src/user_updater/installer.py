"""Python-only installation of system and per-user systemd integrations."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from xml.sax.saxutils import escape

from .paths import migrate_legacy_directory, migrate_user_data_dirs
from .policy import default_policy_text, load_policy


class SystemdAdapter(Protocol):
    def run(self, arguments: Sequence[str], *, check: bool = True) -> None: ...


class LinuxSystemdAdapter:
    def run(self, arguments: Sequence[str], *, check: bool = True) -> None:
        subprocess.run(
            ["systemctl", *arguments],
            check=check,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )


@dataclass(frozen=True)
class InstallPaths:
    policy_file: Path = Path("/etc/user-updater/project.yaml")
    state_dir: Path = Path("/var/lib/user-updater")
    unit_dir: Path = Path("/etc/systemd/system")
    autostart_dir: Path = Path("/etc/xdg/autostart")
    log_file: Path = Path("/var/log/user-updater/updater.log")
    executable: Path = Path("/usr/local/bin/user-updater")
    polkit_action_file: Path = Path("/usr/share/polkit-1/actions/org.userupdater.policy")
    legacy_policy_dir: Path | None = None
    legacy_state_dir: Path | None = None

    @property
    def admin_helper(self) -> Path:
        return self.executable.with_name("user-updater-admin")


@dataclass(frozen=True)
class UserInstallPaths:
    home: Path
    executable: Path

    @property
    def policy_file(self) -> Path:
        return self.home / ".config" / "user-updater" / "project.yaml"

    @property
    def state_dir(self) -> Path:
        return self.home / ".local" / "state" / "user-updater"

    @property
    def unit_dir(self) -> Path:
        return self.home / ".config" / "systemd" / "user"

    @property
    def autostart_dir(self) -> Path:
        return self.home / ".config" / "autostart"


class SystemInstaller:
    """Install the machine-wide service and autostart the GUI for every user."""

    def __init__(
        self,
        paths: InstallPaths | None = None,
        systemd: SystemdAdapter | None = None,
    ) -> None:
        self.paths = paths or InstallPaths()
        self.systemd = systemd or LinuxSystemdAdapter()

    def install(self) -> None:
        paths = self.paths
        legacy_policy_dir = paths.legacy_policy_dir
        legacy_state_dir = paths.legacy_state_dir
        if paths.policy_file == Path("/etc/user-updater/project.yaml"):
            legacy_policy_dir = legacy_policy_dir or Path("/etc/user_updater")
        if paths.state_dir == Path("/var/lib/user-updater"):
            legacy_state_dir = legacy_state_dir or Path("/var/lib/user_updater")
        if legacy_policy_dir is not None:
            migrate_legacy_directory(legacy_policy_dir, paths.policy_file.parent)
        if legacy_state_dir is not None:
            migrate_legacy_directory(legacy_state_dir, paths.state_dir)
        paths.policy_file.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        paths.state_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        paths.state_dir.chmod(0o755)
        (paths.state_dir / "users").mkdir(mode=0o755, exist_ok=True)
        (paths.state_dir / "users").chmod(0o755)
        (paths.state_dir / "locks").mkdir(mode=0o700, exist_ok=True)
        (paths.state_dir / "locks").chmod(0o700)
        paths.log_file.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        paths.log_file.parent.chmod(0o755)
        if not paths.policy_file.exists():
            _atomic_write(paths.policy_file, default_policy_text(), 0o644)
        policy = load_policy(paths.policy_file)
        paths.unit_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        paths.autostart_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        _atomic_write(
            paths.unit_dir / "user-updater.service",
            _system_service_unit(paths.executable),
            0o644,
        )
        _atomic_write(
            paths.unit_dir / "user-updater.timer",
            _timer_unit(policy.schedule.check_interval_minutes),
            0o644,
        )
        _atomic_write(
            paths.autostart_dir / "user-updater.desktop",
            _desktop_entry(paths.executable, "gui", "--system"),
            0o644,
        )
        _atomic_write(
            paths.polkit_action_file,
            _admin_polkit_action(paths.admin_helper),
            0o644,
        )
        self.systemd.run(["daemon-reload"])
        self.systemd.run(["enable", "--now", "user-updater.timer"])

    def uninstall(self, purge: bool = False) -> None:
        self.systemd.run(["stop", "user-updater.timer"], check=False)
        self.systemd.run(["stop", "user-updater.service"], check=False)
        self.systemd.run(["disable", "user-updater.timer"], check=False)
        (self.paths.unit_dir / "user-updater.service").unlink(missing_ok=True)
        (self.paths.unit_dir / "user-updater.timer").unlink(missing_ok=True)
        (self.paths.autostart_dir / "user-updater.desktop").unlink(missing_ok=True)
        self.paths.polkit_action_file.unlink(missing_ok=True)
        self.systemd.run(["daemon-reload"], check=False)
        if purge:
            shutil.rmtree(self.paths.state_dir, ignore_errors=True)
            shutil.rmtree(self.paths.log_file.parent, ignore_errors=True)
            self.paths.policy_file.unlink(missing_ok=True)


class UserInstaller:
    """Install a safe user-only timer and GUI autostart without root privileges."""

    def __init__(
        self,
        paths: UserInstallPaths,
        systemd: SystemdAdapter | None = None,
    ) -> None:
        self.paths = paths
        self.systemd = systemd or LinuxSystemdAdapter()

    def install(self) -> None:
        paths = self.paths
        migrate_user_data_dirs(paths.home, owner_uid=os.getuid())
        paths.policy_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        paths.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not paths.policy_file.exists():
            _atomic_write(paths.policy_file, default_policy_text(user_mode=True), 0o600)
        policy = load_policy(paths.policy_file)
        if policy.updates.run_system:
            raise ValueError("user-mode policy cannot enable machine-wide updates")
        paths.unit_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        paths.autostart_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        _atomic_write(
            paths.unit_dir / "user-updater.service",
            _user_service_unit(paths.executable),
            0o600,
        )
        _atomic_write(
            paths.unit_dir / "user-updater.timer",
            _timer_unit(policy.schedule.check_interval_minutes, user=True),
            0o600,
        )
        _atomic_write(
            paths.autostart_dir / "user-updater.desktop",
            _desktop_entry(paths.executable, "gui", "--user"),
            0o600,
        )
        self.systemd.run(["--user", "daemon-reload"])
        self.systemd.run(["--user", "enable", "--now", "user-updater.timer"])

    def uninstall(self, purge: bool = False) -> None:
        self.systemd.run(["--user", "stop", "user-updater.timer"], check=False)
        self.systemd.run(["--user", "disable", "user-updater.timer"], check=False)
        (self.paths.unit_dir / "user-updater.service").unlink(missing_ok=True)
        (self.paths.unit_dir / "user-updater.timer").unlink(missing_ok=True)
        (self.paths.autostart_dir / "user-updater.desktop").unlink(missing_ok=True)
        self.systemd.run(["--user", "daemon-reload"], check=False)
        if purge:
            shutil.rmtree(self.paths.state_dir, ignore_errors=True)
            self.paths.policy_file.unlink(missing_ok=True)


def _system_service_unit(executable: Path) -> str:
    binary = _systemd_path(executable)
    return f"""[Unit]
Description=Admin-managed user updater
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
ExecStart={binary} tick --system
TimeoutStartSec=infinity
KillMode=control-group
UMask=0022
"""


def _user_service_unit(executable: Path) -> str:
    binary = _systemd_path(executable)
    return f"""[Unit]
Description=User-scoped user updater

[Service]
Type=oneshot
ExecStart={binary} tick --user
TimeoutStartSec=infinity
KillMode=control-group
"""


def _timer_unit(interval_minutes: int, user: bool = False) -> str:
    target = "default.target" if user else "timers.target"
    return f"""[Unit]
Description=Check the user-updater schedule

[Timer]
OnBootSec=1min
OnUnitActiveSec={interval_minutes}min
Persistent=true
AccuracySec=10s
Unit=user-updater.service

[Install]
WantedBy={target}
"""


def _desktop_entry(executable: Path, command: str, mode: str) -> str:
    binary = _desktop_quote(str(executable))
    return f"""[Desktop Entry]
Type=Application
Name=User Updater
Comment=Update status and rescheduling controls
Exec={binary} {command} {mode}
Icon=system-software-update
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
"""


def _admin_polkit_action(helper: Path) -> str:
    """Authorize only the dedicated admin helper and cache its admin grant briefly."""
    helper_path = escape(str(helper))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>User Updater</vendor>
  <action id="org.userupdater.admin">
    <description>Manage User Updater system updates</description>
    <message>Authentication is required to manage system updates</message>
    <defaults>
      <allow_any>no</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>auth_admin_keep</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">{helper_path}</annotate>
  </action>
</policyconfig>
"""


def _desktop_quote(value: str) -> str:
    if any(character in value for character in "\n\r\x00"):
        raise ValueError("executable path contains an invalid desktop-entry character")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if any(character.isspace() for character in value) or any(
        character in value for character in '\\"%'
    ):
        return f'"{escaped}"'
    return escaped


def _systemd_path(path: Path) -> str:
    value = str(path)
    if not path.is_absolute() or any(character in value for character in "\n\r\x00"):
        raise ValueError("systemd executable path must be absolute and valid")
    escaped: list[str] = []
    for character in value:
        if character == " ":
            escaped.append(r"\x20")
        elif character == "\t":
            escaped.append(r"\x09")
        elif character == "\\":
            escaped.append(r"\\")
        elif character == '"':
            escaped.append(r"\x22")
        elif character == "%":
            escaped.append("%%")
        elif character == "$":
            escaped.append(r"\$")
        else:
            escaped.append(character)
    return "".join(escaped)


def _atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary_path = Path(stream.name)
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, mode)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
