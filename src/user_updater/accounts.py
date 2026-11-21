"""Short-lived update-builder account provisioning."""

from __future__ import annotations

import os
import pwd
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .policy import Policy

_ACCOUNT_NAME = re.compile(r"^uupd-[a-f0-9]{10}$")
_PACKAGE_MANAGERS = (
    # Garuda's Topgrade integration invokes this wrapper instead of pacman.
    "/usr/bin/garuda-update",
    "/usr/bin/pacman",
    "/usr/bin/apt-get",
    "/usr/bin/dnf",
    "/usr/bin/zypper",
    "/usr/bin/xbps-install",
    "/usr/bin/emerge",
    "/usr/bin/apk",
    "/sbin/apk",
)


@dataclass(frozen=True)
class BuilderAccount:
    name: str
    uid: int
    gid: int
    home: Path
    sudoers_path: Path


class AccountBackend(Protocol):
    @property
    def package_manager(self) -> str | None: ...

    @property
    def flatpak(self) -> str | None: ...

    def create_user(self, name: str, home: Path) -> None: ...

    def user_ids(self, name: str) -> tuple[int, int]: ...

    def managed_accounts(self, home_root: Path, sudoers_root: Path) -> list[BuilderAccount]: ...

    def write_sudoers(self, path: Path, content: str) -> None: ...

    def delete_user(self, name: str) -> None: ...

    def remove_sudoers(self, path: Path) -> None: ...


class LinuxAccountBackend:
    """The small set of privileged OS operations used by the builder manager."""

    @property
    def package_manager(self) -> str | None:
        return next((path for path in _PACKAGE_MANAGERS if Path(path).is_file()), None)

    @property
    def flatpak(self) -> str | None:
        return shutil.which(
            "flatpak", path="/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"
        )

    def create_user(self, name: str, home: Path) -> None:
        home.mkdir(mode=0o700, parents=True, exist_ok=False)
        subprocess.run(
            [
                "useradd",
                "--system",
                "--user-group",
                "--no-create-home",
                "--home-dir",
                str(home),
                "--shell",
                "/usr/sbin/nologin",
                "--comment",
                "Temporary user-updater builder",
                name,
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        try:
            account = pwd.getpwnam(name)
            os.chown(home, account.pw_uid, account.pw_gid)
        except BaseException:
            subprocess.run(
                ["userdel", "--force", "--remove", name],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
            shutil.rmtree(home, ignore_errors=True)
            raise
        try:
            subprocess.run(
                ["passwd", "--lock", name],
                check=True,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except BaseException:
            subprocess.run(
                ["userdel", "--force", "--remove", name],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
            shutil.rmtree(home, ignore_errors=True)
            raise

    def user_ids(self, name: str) -> tuple[int, int]:
        account = pwd.getpwnam(name)
        return account.pw_uid, account.pw_gid

    def managed_accounts(self, home_root: Path, sudoers_root: Path) -> list[BuilderAccount]:
        managed: list[BuilderAccount] = []
        for account in pwd.getpwall():
            if not _ACCOUNT_NAME.fullmatch(account.pw_name):
                continue
            expected_home = home_root / account.pw_name
            if (
                Path(account.pw_dir) != expected_home
                or account.pw_gecos != "Temporary user-updater builder"
                or Path(account.pw_shell).name != "nologin"
            ):
                continue
            managed.append(
                BuilderAccount(
                    name=account.pw_name,
                    uid=account.pw_uid,
                    gid=account.pw_gid,
                    home=expected_home,
                    sudoers_path=sudoers_root / f"user-updater-{account.pw_name}",
                )
            )
        return managed

    def write_sudoers(self, path: Path, content: str) -> None:
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        visudo = shutil.which("visudo")
        if visudo is None:
            raise RuntimeError("visudo is required to validate temporary sudo policy")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=".user-updater-", delete=False
            ) as stream:
                temporary_path = Path(stream.name)
                os.fchmod(stream.fileno(), 0o440)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            subprocess.run(
                [visudo, "-cf", str(temporary_path)],
                check=True,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
            os.replace(temporary_path, path)
            os.chmod(path, 0o440)
        except (OSError, subprocess.CalledProcessError):
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    def delete_user(self, name: str) -> None:
        try:
            pwd.getpwnam(name)
        except KeyError:
            return
        subprocess.run(
            ["userdel", "--force", "--remove", name],
            check=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )

    def remove_sudoers(self, path: Path) -> None:
        path.unlink(missing_ok=True)


class BuilderAccountManager:
    """Create an isolated build user and remove it after the update run."""

    def __init__(
        self,
        home_root: Path,
        sudoers_root: Path,
        policy: Policy,
        backend: AccountBackend | None = None,
    ) -> None:
        self.home_root = Path(home_root)
        self.sudoers_root = Path(sudoers_root)
        self.policy = policy
        self.backend = backend or LinuxAccountBackend()

    def create(self) -> BuilderAccount:
        self.home_root.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.sudoers_root.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.cleanup_stale()
        name = f"uupd-{os.urandom(5).hex()}"
        home = self.home_root / name
        sudoers_path = self.sudoers_root / f"user-updater-{name}"
        created = False
        try:
            self.backend.create_user(name, home)
            created = True
            self.backend.write_sudoers(sudoers_path, self._sudoers_content(name))
            uid, gid = self.backend.user_ids(name)
            return BuilderAccount(
                name=name,
                uid=uid,
                gid=gid,
                home=home,
                sudoers_path=sudoers_path,
            )
        except BaseException:
            try:
                self.backend.remove_sudoers(sudoers_path)
            except OSError:
                pass
            if created:
                try:
                    self.backend.delete_user(name)
                except (OSError, subprocess.CalledProcessError):
                    pass
            shutil.rmtree(home, ignore_errors=True)
            raise

    def remove(self, account: BuilderAccount) -> None:
        expected_home = self.home_root / account.name
        expected_sudoers = self.sudoers_root / f"user-updater-{account.name}"
        if (
            not _ACCOUNT_NAME.fullmatch(account.name)
            or account.home != expected_home
            or account.sudoers_path != expected_sudoers
        ):
            raise ValueError("refusing to remove an account outside the managed builder area")
        errors: list[Exception] = []
        try:
            self.backend.remove_sudoers(account.sudoers_path)
        except Exception as exc:
            errors.append(exc)
        try:
            self.backend.delete_user(account.name)
        except Exception as exc:
            errors.append(exc)
        try:
            if account.home.is_symlink():
                account.home.unlink(missing_ok=True)
            else:
                shutil.rmtree(account.home, ignore_errors=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            errors.append(exc)
        if errors:
            raise RuntimeError(
                f"builder cleanup was incomplete for {account.name}: "
                + "; ".join(str(error) for error in errors)
            ) from errors[0]

    def cleanup_stale(self) -> None:
        """Remove abandoned builders and sudoers files from interrupted runs."""
        self.home_root.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.sudoers_root.mkdir(mode=0o755, parents=True, exist_ok=True)
        if self.home_root.is_symlink() or self.sudoers_root.is_symlink():
            raise RuntimeError("managed builder and sudoers roots must not be symlinks")
        errors: list[Exception] = []
        for account in self.backend.managed_accounts(self.home_root, self.sudoers_root):
            # The account list is verified against the marker, shell, and home path.
            account = BuilderAccount(
                name=account.name,
                uid=account.uid,
                gid=account.gid,
                home=self.home_root / account.name,
                sudoers_path=self.sudoers_root / f"user-updater-{account.name}",
            )
            try:
                self.remove(account)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(exc)

        for path in self.sudoers_root.glob("user-updater-uupd-*"):
            name = path.name.removeprefix("user-updater-")
            if not _ACCOUNT_NAME.fullmatch(name):
                continue
            try:
                self.backend.remove_sudoers(path)
            except OSError as exc:
                errors.append(exc)

        for path in self.home_root.iterdir():
            if not _ACCOUNT_NAME.fullmatch(path.name):
                continue
            try:
                if path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:
                errors.append(exc)

        if errors:
            raise RuntimeError(
                "stale builder cleanup was incomplete: " + "; ".join(str(error) for error in errors)
            ) from errors[0]

    def _sudoers_content(self, name: str) -> str:
        if self.policy.security.builder_sudo_mode == "unrestricted":
            return f"{name} ALL=(ALL:ALL) NOPASSWD: ALL\n"
        manager = self.backend.package_manager
        if manager is None:
            raise RuntimeError("no supported system package manager was detected")
        commands = [manager]
        flatpak = self.backend.flatpak
        if flatpak is not None and flatpak not in commands:
            commands.append(flatpak)
        allowed_commands = ", ".join(
            spec for command in commands for spec in (command, f"{command} *")
        )
        environment_policy = ""
        if manager == "/usr/bin/garuda-update":
            # Topgrade sets these exact flags when invoking Garuda's root wrapper.
            # Keep the exception command-scoped and value-scoped; SETENV would
            # permit arbitrary environment overrides for a privileged command.
            environment_policy = (
                'Defaults!/usr/bin/garuda-update env_check += '
                '"SKIP_MIRRORLIST=1 UPDATE_AUR=1"\n'
            )
        return environment_policy + f"{name} ALL=(root) NOPASSWD: {allowed_commands}\n"
