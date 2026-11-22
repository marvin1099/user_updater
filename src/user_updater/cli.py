"""Command-line interface for install, status, desktop, and scheduled worker."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pwd
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .accounts import BuilderAccountManager
from .coordinator import TickAction
from .history import HistoryError, RunHistoryStore, RunRecord, history_path_for_state
from .installer import InstallPaths, SystemInstaller, UserInstaller, UserInstallPaths
from .logging_config import configure_logging
from .paths import system_paths, user_paths
from .policy import PolicyError, consume_test_outcome, load_policy, write_policy
from .requests import UserRequest, save_request, task_request_path
from .service import RequestHome, UpdateService, local_request_homes
from .sessions import DesktopUser, detect_graphical_users
from .state import StateError, StateStore
from .tool_preferences import ToolPreferenceError, UserToolPreferenceStore
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

    retry = commands.add_parser("retry", help="Manually retry a failed update")
    _add_scope(retry)

    run_now = commands.add_parser(
        "run-now", help="Start a fresh machine-wide system update immediately"
    )
    run_now.add_argument(
        "--system",
        action="store_true",
        required=True,
        help="Run machine-wide updates (administrator authorization required)",
    )

    history = commands.add_parser("history", help="Show recent update run summaries")
    _add_scope(history)
    history.add_argument("--limit", type=int, default=10, help="Number of recent runs (1-20)")

    logs = commands.add_parser("logs", help="Inspect or export updater logs")
    _add_scope(logs)
    logs.add_argument("--lines", type=int, default=200, help="Number of lines to print (1-10000)")
    logs.add_argument("--output", type=Path, help="Export the current log to a new file")

    commands.add_parser("demo", help="Preview the Qt windows with a simulated update")
    commands.add_parser("doctor", help="Check runtime dependencies without changing the system")
    policy = commands.add_parser("validate-policy", help="Validate an administrator YAML policy")
    policy.add_argument("path", nargs="?", type=Path)
    commands.add_parser("admin-auth-check", help="Authenticate the administrator settings action")
    commands.add_parser("admin-policy-write", help="Internal administrator policy writer")
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
        if args.command == "retry":
            return _retry(system_mode)
        if args.command == "run-now":
            if not getattr(args, "system", False):
                raise ValueError("run-now requires the explicit --system option")
            return _run_system_now(system_mode)
        if args.command == "history":
            return _history(system_mode, args.limit)
        if args.command == "logs":
            return _logs(system_mode, args.lines, args.output)
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
        if args.command == "admin-auth-check":
            _require_root("administrator settings")
            return 0
        if args.command == "admin-policy-write":
            _require_root("administrator policy update")
            return _write_admin_policy()
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
    logger = configure_logging(paths.log_file, private=not system_mode)
    policy = load_policy(paths.policy if paths.policy.exists() else None)
    jobs: list[tuple[str, UpdateService, StateStore]] = []
    state_mode = 0o644 if system_mode else 0o600

    if system_mode and policy.updates.run_system:
        system_policy = dataclasses.replace(
            policy,
            updates=dataclasses.replace(
                policy.updates,
                run_user_tools=False,
                allow_custom_user_tools=False,
            ),
        )
        system_store = StateStore(paths.state, file_mode=state_mode)
        builder = BuilderAccountManager(paths.builder_home, paths.sudoers, system_policy)
        system_workflow = UpdateWorkflow(
            system_policy,
            builder_manager=builder,
            session_detector=lambda: [],
            test_outcome=lambda: consume_test_outcome(paths.policy, "system"),
        )
        jobs.append(
            (
                "system",
                UpdateService(
                    system_policy,
                    system_store,
                    system_workflow,
                    request_homes=local_request_homes,
                    on_log=logger.info,
                    task_id="system",
                    lock_file=paths.state.parent / "locks" / "system.lock",
                ),
                system_store,
            )
        )

    desktop_users: list[DesktopUser] = []
    if policy.updates.run_user_tools:
        desktop_users = detect_graphical_users() if system_mode else [_current_desktop_user()]
    if desktop_users:
        for user in desktop_users:
            user_policy = _user_policy_for(user, policy, logger)
            user_state_path = (
                paths.user_state_dir / f"{user.uid}.json" if system_mode else paths.state
            )
            user_store = StateStore(user_state_path, file_mode=state_mode)

            def sessions_for_user(selected: DesktopUser = user) -> list[DesktopUser]:
                return [selected]

            def request_homes_for_user(selected: DesktopUser = user) -> list[RequestHome]:
                return [RequestHome(selected.home, selected.uid)]

            user_workflow = UpdateWorkflow(
                user_policy,
                builder_manager=None,
                session_detector=sessions_for_user,
                test_outcome=lambda: consume_test_outcome(paths.policy, "user_tools"),
            )
            jobs.append(
                (
                    f"user-tools:{user.username}",
                    UpdateService(
                        user_policy,
                        user_store,
                        user_workflow,
                        request_homes=request_homes_for_user,
                        on_log=logger.info,
                        task_id="user-tools",
                        interval_hours=policy.schedule.user_tools_interval_hours,
                        response_minutes=policy.schedule.user_tools_response_minutes,
                        lock_file=(paths.state.parent / "locks" / f"user-tools-{user.uid}.lock"),
                    ),
                    user_store,
                )
            )

    if not jobs:
        logger.info("No update tasks are enabled or due for a logged-in user.")
        return 0

    failed = False
    with ThreadPoolExecutor(max_workers=min(16, len(jobs))) as pool:
        futures = {
            pool.submit(service.tick): (name, state_store) for name, service, state_store in jobs
        }
        for future in as_completed(futures):
            name, state_store = futures[future]
            action = future.result()
            state = state_store.load()
            logger.info(
                "task=%s scheduler=%s phase=%s message=%s",
                name,
                action.value,
                state.phase,
                state.message,
            )
            failed = failed or action is TickAction.RECOVERED_INTERRUPTED_RUN
            failed = failed or (state.phase == "failed" and state.returncode not in (None, 0))
    return int(failed)


def _retry(system_mode: bool) -> int:
    if not system_mode:
        return _retry_user()

    paths = system_paths()
    policy = load_policy(paths.policy if paths.policy.exists() else None)
    if not policy.updates.run_system:
        raise ValueError("machine-wide updates are disabled by administrator policy")
    state_store = StateStore(paths.state, file_mode=0o644)
    state = state_store.load()
    if state.phase != "failed":
        raise ValueError("only a failed machine-wide update can be retried")
    if state.manual_retries_used >= policy.retry.system.user_manual_attempts:
        raise ValueError(
            "user-requested system retries are disabled or exhausted by administrator policy"
        )

    executable = _executable_path()
    if os.geteuid() != 0:
        _require_trusted_system_install(executable)
        pkexec = shutil.which("pkexec")
        if pkexec is None:
            raise RuntimeError("pkexec is required to authorize a machine-wide retry")
        result = subprocess.run(
            [pkexec, str(executable), "retry", "--system"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
        return result.returncode

    _require_trusted_system_install(executable)
    logger = configure_logging(paths.log_file)
    system_policy = dataclasses.replace(
        policy,
        updates=dataclasses.replace(
            policy.updates,
            run_user_tools=False,
            allow_custom_user_tools=False,
        ),
    )
    builder = BuilderAccountManager(paths.builder_home, paths.sudoers, system_policy)
    workflow = UpdateWorkflow(
        system_policy,
        builder_manager=builder,
        session_detector=lambda: [],
        test_outcome=lambda: consume_test_outcome(paths.policy, "system"),
    )
    service = UpdateService(
        system_policy,
        state_store,
        workflow,
        request_homes=local_request_homes,
        on_log=logger.info,
        task_id="system",
        lock_file=paths.state.parent / "locks" / "system.lock",
    )
    action = service.retry_failed()
    if action is TickAction.WORKER_ALREADY_ACTIVE:
        raise RuntimeError("an update worker is already active")
    final_state = state_store.load()
    print(final_state.message)
    return int(final_state.phase == "failed")


def _run_system_now(system_mode: bool) -> int:
    if not system_mode:
        raise ValueError("immediate machine-wide updates require --system")
    _require_root("immediate machine-wide updates")
    executable = _executable_path()
    _require_trusted_system_install(executable)
    paths = system_paths()
    policy = load_policy(paths.policy if paths.policy.exists() else None)
    if not policy.updates.run_system:
        raise ValueError("machine-wide updates are disabled by administrator policy")

    system_policy = dataclasses.replace(
        policy,
        updates=dataclasses.replace(
            policy.updates,
            run_user_tools=False,
            allow_custom_user_tools=False,
        ),
    )
    logger = configure_logging(paths.log_file)
    state_store = StateStore(paths.state, file_mode=0o644)
    builder = BuilderAccountManager(paths.builder_home, paths.sudoers, system_policy)
    workflow = UpdateWorkflow(
        system_policy,
        builder_manager=builder,
        session_detector=lambda: [],
        test_outcome=lambda: consume_test_outcome(paths.policy, "system"),
    )
    service = UpdateService(
        system_policy,
        state_store,
        workflow,
        request_homes=local_request_homes,
        on_log=logger.info,
        task_id="system",
        lock_file=paths.state.parent / "locks" / "system.lock",
    )
    action = service.run_now()
    if action is TickAction.WORKER_ALREADY_ACTIVE:
        raise RuntimeError("a machine-wide update is already running")
    if action is TickAction.RECOVERED_INTERRUPTED_RUN:
        print(state_store.load().message)
        return 1
    final_state = state_store.load()
    print(final_state.message)
    return int(final_state.phase == "failed")


def _run_user_tools_now(system_mode: bool) -> int:
    """Start personal-tool updates for every active local desktop user."""
    if not system_mode:
        raise ValueError("immediate user-tool updates require --system")
    _require_root("immediate personal-tool updates")
    executable = _executable_path()
    _require_trusted_system_install(executable)
    paths = system_paths()
    policy = load_policy(paths.policy if paths.policy.exists() else None)
    if not policy.updates.run_user_tools:
        raise ValueError("personal-tool updates are disabled by administrator policy")

    logger = configure_logging(paths.log_file)
    desktop_users = detect_graphical_users()
    if not desktop_users:
        logger.info("No active local desktop session; skipping immediate personal-tool updates.")
        return 0

    def run_user(user: DesktopUser) -> tuple[DesktopUser, TickAction, str]:
        user_policy = _user_policy_for(user, policy, logger)
        state_store = StateStore(paths.user_state_dir / f"{user.uid}.json", file_mode=0o644)
        workflow = UpdateWorkflow(
            user_policy,
            builder_manager=None,
            session_detector=lambda selected=user: [selected],
            test_outcome=lambda: consume_test_outcome(paths.policy, "user_tools"),
        )
        service = UpdateService(
            user_policy,
            state_store,
            workflow,
            request_homes=lambda selected=user: [RequestHome(selected.home, selected.uid)],
            on_log=logger.info,
            task_id="user-tools",
            interval_hours=policy.schedule.user_tools_interval_hours,
            response_minutes=policy.schedule.user_tools_response_minutes,
            lock_file=paths.state.parent / "locks" / f"user-tools-{user.uid}.lock",
        )
        action = service.run_now()
        state = state_store.load()
        logger.info(
            "task=user-tools:%s scheduler=%s phase=%s message=%s",
            user.username,
            action.value,
            state.phase,
            state.message,
        )
        return user, action, state.phase

    failed = False
    with ThreadPoolExecutor(max_workers=min(16, len(desktop_users))) as pool:
        futures = {pool.submit(run_user, user): user for user in desktop_users}
        for future in as_completed(futures):
            user, action, phase = future.result()
            if action is TickAction.WORKER_ALREADY_ACTIVE:
                logger.info("Personal-tool updates are already running for %s.", user.username)
                continue
            failed = failed or action is TickAction.RECOVERED_INTERRUPTED_RUN or phase == "failed"
    return int(failed)


def _retry_user() -> int:
    managed_paths = system_paths()
    if managed_paths.policy.is_file():
        if os.getuid() == 0:
            raise ValueError("personal-tool retries must be requested by the owning user")
        policy = load_policy(managed_paths.policy)
        if not policy.updates.run_user_tools:
            raise ValueError("personal tool updates are disabled by administrator policy")
        state_path = managed_paths.user_state_dir / f"{os.getuid()}.json"
        state = StateStore(state_path, file_mode=0o644).load()
        if state.phase != "failed" or state.run_id is None:
            raise ValueError("only a failed personal-tool update can be retried")
        if state.manual_retries_used >= policy.retry.user_tools.user_manual_attempts:
            raise ValueError(
                "user-requested personal-tool retries are disabled or exhausted by policy"
            )
        save_request(
            task_request_path(Path.home(), "user-tools"),
            UserRequest(state.run_id, "retry"),
        )
        print("Personal-tool retry request submitted to the system updater.")
        return 0

    paths = user_paths()
    policy = load_policy(paths.policy if paths.policy.exists() else None)
    if not policy.updates.run_user_tools:
        raise ValueError("personal tool updates are disabled by policy")
    state = StateStore(paths.state, file_mode=0o600).load()
    if state.phase != "failed" or state.run_id is None:
        raise ValueError("only a failed personal-tool update can be retried")
    if state.manual_retries_used >= policy.retry.user_tools.user_manual_attempts:
        raise ValueError("personal-tool retries are disabled or exhausted by administrator policy")
    save_request(
        task_request_path(Path.home(), "user-tools"),
        UserRequest(state.run_id, "retry"),
    )
    # The request is owner-written; the normal worker consumes it under its lock.
    return _tick(system_mode=False)


def _user_policy_for(user: DesktopUser, policy, logger):
    preferences = UserToolPreferenceStore(
        user.home / ".config" / "user_updater" / "tool_preferences.json",
        owner_uid=user.uid,
    )
    try:
        selected = preferences.load_all(
            policy.updates.user_tools,
            admin_allows_custom=policy.updates.allow_custom_user_tools,
        )
    except ToolPreferenceError as exc:
        logger.info(
            "Ignoring invalid personal tool preferences for %s; using admin defaults: %s",
            user.username,
            exc,
        )
        selected_tools = policy.updates.user_tools
        custom_tools_enabled = policy.updates.allow_custom_user_tools
    else:
        selected_tools = selected.tools
        custom_tools_enabled = selected.custom_tools
    return dataclasses.replace(
        policy,
        updates=dataclasses.replace(
            policy.updates,
            run_system=False,
            user_tools=selected_tools,
            allow_custom_user_tools=custom_tools_enabled,
        ),
    )


def _history(system_mode: bool, limit: int) -> int:
    if not 1 <= limit <= 20:
        raise ValueError("history limit must be between 1 and 20")
    paths = system_paths() if system_mode else user_paths()
    sources: list[tuple[str, Path, int]] = []
    if system_mode:
        sources.append(("System", paths.state, 0o644))
        for state_path in sorted(paths.user_state_dir.glob("*.json")):
            if not state_path.stem.isdecimal():
                continue
            try:
                username = pwd.getpwuid(int(state_path.stem)).pw_name
            except (ValueError, KeyError):
                username = f"UID {state_path.stem}"
            sources.append((f"{username} user tools", state_path, 0o644))
    elif (managed := system_paths()).policy.is_file():
        sources.append(
            (
                "Personal tools",
                managed.user_state_dir / f"{os.getuid()}.json",
                0o644,
            )
        )
    else:
        sources.append(("Personal tools", paths.state, 0o600))

    entries: list[tuple[str, RunRecord]] = []
    had_error = False
    for label, state_path, file_mode in sources:
        try:
            records = RunHistoryStore(
                history_path_for_state(state_path), file_mode=file_mode
            ).load()
        except HistoryError as exc:
            print(f"{label} history: {exc}", file=sys.stderr)
            had_error = True
            continue
        entries.extend((label, record) for record in records)

    entries.sort(key=lambda item: _history_timestamp(item[1]), reverse=True)
    if not entries:
        print("No completed update runs recorded.")
    for label, record in entries[:limit]:
        print(
            f"{_format_history_time(record.finished_at)}\t{label}/{record.task}\t"
            f"{record.phase}\t{record.message}"
        )
    return int(had_error)


def _logs(system_mode: bool, lines: int, output: Path | None) -> int:
    if not 1 <= lines <= 10_000:
        raise ValueError("log line count must be between 1 and 10000")
    paths = system_paths() if system_mode else user_paths()
    if system_mode:
        _require_root("machine-wide log access")
    content = paths.log_file.read_bytes()
    if output is not None:
        destination = Path(output)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(destination, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            destination.unlink(missing_ok=True)
            raise
        print(f"Exported current updater log to {destination}")
        return 0
    text = content.decode("utf-8", errors="replace").splitlines()
    print("\n".join(text[-lines:]))
    return 0


def _history_timestamp(record: RunRecord) -> float:
    if record.finished_at is None:
        return 0.0
    try:
        return datetime.fromisoformat(record.finished_at).timestamp()
    except (OverflowError, OSError, ValueError):
        return 0.0


def _format_history_time(value: str | None) -> str:
    if value is None:
        return "time unknown"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    except (OverflowError, OSError, ValueError):
        return "time unknown"


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
    if state.last_output and (not system_mode or os.geteuid() == 0):
        print("\n".join(state.last_output[-10:]))
    if system_mode:
        for user_state_path in sorted(paths.user_state_dir.glob("*.json")):
            if not user_state_path.stem.isdecimal():
                continue
            try:
                username = pwd.getpwuid(int(user_state_path.stem)).pw_name
            except (ValueError, KeyError):
                username = f"uid {user_state_path.stem}"
            try:
                user_state = StateStore(user_state_path).load()
            except StateError as exc:
                print(f"\n{username} user-tool updates: {exc}")
                continue
            print(f"\n{username} user-tool updates: {user_state.phase}\n{user_state.message}")
    return 0


def _doctor() -> int:
    checks = {
        "systemd": shutil.which("systemctl"),
        "topgrade": shutil.which("topgrade"),
        "visudo": shutil.which("visudo"),
        "pkexec": shutil.which("pkexec"),
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


def _write_admin_policy() -> int:
    executable = Path(sys.argv[0])
    if not executable.is_absolute():
        executable = Path.cwd() / executable
    _require_trusted_system_install(executable.absolute())
    encoded = sys.stdin.read(65_537)
    if len(encoded) > 65_536:
        raise ValueError("administrator policy payload is too large")
    raw = json.loads(encoded)
    saved = write_policy(Path("/etc/user_updater/project.yaml"), raw)
    logger = configure_logging(Path("/var/log/user-updater/updater.log"))
    logger.info(
        "administrator policy changed: system=%s user_tools=%s interval_hours=%d custom_tools=%s",
        saved.updates.run_system,
        saved.updates.run_user_tools,
        saved.schedule.user_tools_interval_hours,
        saved.updates.allow_custom_user_tools,
    )
    return 0


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
    for installed_file in (executable.resolve(), Path(__file__).resolve()):
        for path in (installed_file, *installed_file.parents):
            metadata = path.stat()
            if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise RuntimeError(
                    "system mode requires a root-owned, non-writable installation; "
                    "install with `sudo env UV_TOOL_DIR=/opt/user-updater/uv-tools "
                    "UV_TOOL_BIN_DIR=/usr/local/bin uv tool install .` first"
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
