"""Command-line interface for install, status, desktop, and scheduled worker."""

from __future__ import annotations

import argparse
import dataclasses
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

from .accounts import BuilderAccountManager
from .coordinator import TickAction
from .installer import InstallPaths, SystemInstaller, UserInstaller, UserInstallPaths
from .paths import system_paths, user_paths
from .policy import PolicyError, load_policy
from .service import RequestHome, UpdateService, local_request_homes
from .sessions import DesktopUser, detect_graphical_users
from .state import StateError, StateStore
from .workflow import UpdateWorkflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="user-updater",
        description="Admin-managed scheduled updates with per-user response windows.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    install = commands.add_parser("install", help="Install for all users or only this user")
    _add_scope(install)

    uninstall = commands.add_parser("uninstall", help="Remove service integration")
    _add_scope(uninstall)
    uninstall.add_argument(
        "--purge", action="store_true", help="Also remove policy and local state"
    )

    tick = commands.add_parser("tick", help="Run one scheduler check (normally called by systemd)")
    _add_scope(tick)

    gui = commands.add_parser("gui", help="Start the desktop status client")
    _add_scope(gui)

    status = commands.add_parser("status", help="Show the most recent update state")
    _add_scope(status)

    commands.add_parser("demo", help="Preview the Qt windows with a simulated update")
    commands.add_parser("doctor", help="Check runtime dependencies without changing the system")
    policy = commands.add_parser("validate-policy", help="Validate an administrator YAML policy")
    policy.add_argument("path", nargs="?", type=Path)
    return parser


def _add_scope(parser: argparse.ArgumentParser) -> None:
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--system", action="store_true", help="Use machine-wide admin-managed mode")
    scope.add_argument("--user", action="store_true", help="Use only the current user's tools")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    system_mode = _system_mode(args)

    try:
        if args.command == "install":
            return _install(system_mode)
        if args.command == "uninstall":
            return _uninstall(system_mode, args.purge)
        if args.command == "tick":
            return _tick(system_mode)
        if args.command == "gui":
            from .gui.app import run_gui

            return run_gui(system_mode)
        if args.command == "status":
            return _status(system_mode)
        if args.command == "demo":
            from .gui.demo import run_demo

            return run_demo()
        if args.command == "doctor":
            return _doctor()
        if args.command == "validate-policy":
            loaded = load_policy(args.path)
            print(
                f"Policy valid: {', '.join(loaded.schedule.days)} at "
                f"{loaded.schedule.time}; response window "
                f"{loaded.schedule.response_minutes} minutes."
            )
            return 0
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
        print(
            f"user-updater: command failed with status {exc.returncode}"
            + (f": {detail}" if detail else ""),
            file=sys.stderr,
        )
        return 1
    except (OSError, RuntimeError, ValueError, PolicyError) as exc:
        print(f"user-updater: {exc}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2


def _system_mode(args: argparse.Namespace) -> bool:
    if getattr(args, "system", False):
        return True
    if getattr(args, "user", False):
        return False
    return os.geteuid() == 0


def _install(system_mode: bool) -> int:
    executable = _executable_path()
    if system_mode:
        _require_root("machine-wide installation")
        _require_trusted_system_install(executable)
        SystemInstaller(InstallPaths(executable=executable)).install()
        print("Installed machine-wide service and all-user desktop autostart.")
    else:
        home = Path.home()
        UserInstaller(UserInstallPaths(home=home, executable=executable)).install()
        print("Installed user-scoped timer and desktop autostart (no system updates).")
    return 0


def _uninstall(system_mode: bool, purge: bool) -> int:
    if system_mode:
        _require_root("machine-wide uninstall")
        SystemInstaller().uninstall(purge=purge)
    else:
        UserInstaller(UserInstallPaths(Path.home(), _executable_path())).uninstall(purge=purge)
    print(
        "Updater integration removed."
        if not purge
        else "Updater integration and local data removed."
    )
    return 0


def _tick(system_mode: bool) -> int:
    paths = system_paths() if system_mode else user_paths()
    if system_mode:
        _require_root("machine-wide update service")
    policy = load_policy(paths.policy if paths.policy.exists() else None)
    if not system_mode:
        policy = dataclasses.replace(
            policy,
            updates=dataclasses.replace(policy.updates, run_system=False),
        )
    state_store = StateStore(paths.state, file_mode=0o644 if system_mode else 0o600)
    builder = (
        BuilderAccountManager(paths.builder_home, paths.sudoers, policy) if system_mode else None
    )
    if system_mode:
        session_detector = detect_graphical_users
        request_homes = local_request_homes
    else:
        current_user = _current_desktop_user()

        def session_detector() -> list[DesktopUser]:
            return [current_user]

        def request_homes() -> list[RequestHome]:
            return [RequestHome(current_user.home, current_user.uid)]

    workflow = UpdateWorkflow(
        policy=policy,
        builder_manager=builder,
        session_detector=session_detector,
    )
    service = UpdateService(
        policy,
        state_store,
        workflow,
        request_homes=request_homes,
        lock_file=paths.state.parent / "worker.lock",
    )
    action = service.tick()
    state = state_store.load()
    print(f"Scheduler: {action.value} — {state.message}")
    if action is TickAction.RECOVERED_INTERRUPTED_RUN:
        return 1
    if state.phase == "failed" and state.returncode not in (None, 0):
        return 1
    return 0


def _status(system_mode: bool) -> int:
    paths = system_paths() if system_mode else user_paths()
    try:
        state = StateStore(paths.state).load()
    except StateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Status: {state.phase}\n{state.message}")
    if state.next_scheduled_at:
        print(f"Next scheduled time: {state.next_scheduled_at}")
    if state.last_output:
        print("\n".join(state.last_output[-10:]))
    return 0


def _doctor() -> int:
    checks = {
        "systemd": shutil.which("systemctl"),
        "topgrade": shutil.which("topgrade"),
        "visudo": shutil.which("visudo"),
        "Qt desktop": "PySide6" if _pyside_available() else None,
    }
    failed = False
    for name, value in checks.items():
        status = "available" if value else "missing"
        print(f"{name}: {status}{f' ({value})' if value else ''}")
        if name in {"systemd", "Qt desktop"} and not value:
            failed = True
    if not checks["topgrade"]:
        print(
            "Install topgrade with your distribution's package manager before "
            "enabling system updates."
        )
    return int(failed)


def _current_desktop_user() -> DesktopUser:
    account = pwd.getpwuid(os.getuid())
    home = Path(account.pw_dir)
    return DesktopUser(
        username=account.pw_name,
        uid=account.pw_uid,
        gid=account.pw_gid,
        home=home,
        runtime_dir=Path(f"/run/user/{account.pw_uid}"),
    )


def _executable_path() -> Path:
    executable = shutil.which("user-updater") or sys.argv[0]
    path = Path(executable)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.absolute()


def _require_trusted_system_install(executable: Path) -> None:
    for path in (executable, Path(__file__).resolve()):
        metadata = path.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise RuntimeError(
                "system mode requires a root-owned, non-writable installation; "
                "install with `sudo uv tool install --system .` first"
            )


def _require_root(action: str) -> None:
    if os.geteuid() != 0:
        raise RuntimeError(f"{action} requires administrator privileges; rerun with sudo")


def _pyside_available() -> bool:
    try:
        import PySide6  # noqa: F401

        return True
    except ImportError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
