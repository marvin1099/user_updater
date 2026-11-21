from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from user_updater import cli
from user_updater.coordinator import TickAction
from user_updater.history import RunHistoryStore, history_path_for_state
from user_updater.paths import AppPaths
from user_updater.policy import load_policy
from user_updater.requests import load_request, task_request_path
from user_updater.sessions import DesktopUser
from user_updater.state import StateStore, UpdateState
from user_updater.tool_preferences import UserToolPreferenceStore


class _Logger:
    def info(self, *_args: object) -> None:
        pass


def test_run_now_command_requires_system_scope() -> None:
    args = cli.build_parser().parse_args(["run-now", "--system"])

    assert args.command == "run-now"
    assert args.system is True


def test_system_and_per_user_jobs_are_independent_and_tick_in_parallel(
    monkeypatch, tmp_path: Path
) -> None:
    users = [
        DesktopUser("alice", 1001, 1001, tmp_path / "alice", tmp_path / "runtime-alice"),
        DesktopUser("bob", 1002, 1002, tmp_path / "bob", tmp_path / "runtime-bob"),
    ]
    paths = AppPaths(
        policy=tmp_path / "project.yaml",
        state=tmp_path / "system" / "state.json",
        user_state_dir=tmp_path / "system" / "users",
        builder_home=tmp_path / "system" / "builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "requests" / "user-tools.json",
        log_file=tmp_path / "logs" / "updater.log",
    )
    barrier = threading.Barrier(3)
    observed: list[tuple[Path, Path | None]] = []

    class ParallelService:
        def __init__(self, _policy, state_store, _workflow, **kwargs) -> None:
            self.state_store = state_store
            self.lock_file = kwargs["lock_file"]

        def tick(self) -> TickAction:
            barrier.wait(timeout=5)
            observed.append((self.state_store.path, self.lock_file))
            return TickAction.NOT_DUE

    monkeypatch.setattr(cli, "system_paths", lambda: paths)
    monkeypatch.setattr(cli, "load_policy", lambda _path=None: load_policy())
    monkeypatch.setattr(cli, "configure_logging", lambda *_args, **_kwargs: _Logger())
    monkeypatch.setattr(cli, "_require_root", lambda _action: None)
    monkeypatch.setattr(cli, "detect_graphical_users", lambda: users)
    monkeypatch.setattr(cli, "UpdateService", ParallelService)

    assert cli._tick(system_mode=True) == 0
    assert len(observed) == 3
    assert len({state_path for state_path, _lock in observed}) == 3
    assert len({lock_path for _state_path, lock_path in observed}) == 3


def test_cli_applies_owner_tool_preferences_within_admin_allow_list(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "alice"
    home.mkdir()
    runtime = tmp_path / "runtime-alice"
    paths = AppPaths(
        policy=tmp_path / "project.yaml",
        state=tmp_path / "system" / "state.json",
        user_state_dir=tmp_path / "system" / "users",
        builder_home=tmp_path / "system" / "builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "requests" / "user-tools.json",
        log_file=tmp_path / "logs" / "updater.log",
    )
    paths.policy.write_text(
        """
version: 1
updates:
  run_system: false
  run_user_tools: true
  user_tools: [flatpak, pipx]
""",
        encoding="utf-8",
    )
    preferences = UserToolPreferenceStore(
        home / ".config" / "user_updater" / "tool_preferences.json",
        owner_uid=os.getuid(),
    )
    preference_path = preferences.path
    preference_path.parent.mkdir(parents=True)
    # Simulate saved choices from before the administrator removed cargo.
    preference_path.write_text(
        json.dumps({"version": 1, "tools": ["flatpak", "cargo"], "custom_tools": False}),
        encoding="utf-8",
    )
    preference_path.chmod(0o600)
    user = DesktopUser("alice", os.getuid(), os.getgid(), home, runtime)
    observed: list[tuple[tuple[str, ...], bool]] = []

    class PreferenceService:
        def __init__(self, policy, *_args, **_kwargs) -> None:
            observed.append(
                (policy.updates.user_tools, policy.updates.allow_custom_user_tools)
            )

        def tick(self) -> TickAction:
            return TickAction.NOT_DUE

    monkeypatch.setattr(cli, "system_paths", lambda: paths)
    monkeypatch.setattr(cli, "configure_logging", lambda *_args, **_kwargs: _Logger())
    monkeypatch.setattr(cli, "_require_root", lambda _action: None)
    monkeypatch.setattr(cli, "detect_graphical_users", lambda: [user])
    monkeypatch.setattr(cli, "UpdateService", PreferenceService)

    result = cli._tick(system_mode=True)

    assert result == 0
    assert observed == [(("flatpak",), False)]


def test_history_command_shows_recent_personal_tool_runs(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    paths = AppPaths(
        policy=tmp_path / "project.yaml",
        state=tmp_path / "user-tools.json",
        user_state_dir=tmp_path,
        builder_home=tmp_path / "builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "request.json",
        log_file=tmp_path / "updater.log",
    )
    RunHistoryStore(history_path_for_state(paths.state)).append(
        "user-tools",
        UpdateState(
            phase="failed",
            run_id="run-history",
            finished_at=datetime(2026, 11, 16, 12, 0, tzinfo=ZoneInfo("UTC")).isoformat(),
            returncode=1,
            message="Cargo update failed.",
        ),
    )
    monkeypatch.setattr(cli, "user_paths", lambda: paths)
    monkeypatch.setattr(cli, "system_paths", lambda: paths)

    result = cli.main(["history", "--user"])

    output = capsys.readouterr().out
    assert result == 0
    assert "user-tools" in output
    assert "failed" in output
    assert "Cargo update failed." in output


def test_logs_command_exports_private_user_log_without_overwriting(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    log_file = tmp_path / "updater.log"
    log_file.write_text("first\nsecond\n", encoding="utf-8")
    paths = AppPaths(
        policy=tmp_path / "project.yaml",
        state=tmp_path / "user-tools.json",
        user_state_dir=tmp_path,
        builder_home=tmp_path / "builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "request.json",
        log_file=log_file,
    )
    monkeypatch.setattr(cli, "user_paths", lambda: paths)
    exported = tmp_path / "exported.log"

    result = cli.main(["logs", "--user", "--output", str(exported)])

    assert result == 0
    assert exported.read_text(encoding="utf-8") == "first\nsecond\n"
    assert exported.stat().st_mode & 0o777 == 0o600
    assert "Exported" in capsys.readouterr().out

    assert cli.main(["logs", "--user", "--output", str(exported)]) == 1
    assert exported.read_text(encoding="utf-8") == "first\nsecond\n"


def test_user_retry_command_creates_only_a_retry_request_for_a_failed_run(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "alice"
    home.mkdir()
    paths = AppPaths(
        policy=tmp_path / "project.yaml",
        state=tmp_path / "user-tools.json",
        user_state_dir=tmp_path,
        builder_home=tmp_path / "builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "request.json",
        log_file=tmp_path / "updater.log",
    )
    paths.policy.write_text(
        "retry:\n  user_tools:\n    user_manual_attempts: 1\n", encoding="utf-8"
    )
    StateStore(paths.state).save(
        UpdateState(phase="failed", run_id="failed-user-run", returncode=1)
    )
    monkeypatch.setattr(cli, "user_paths", lambda: paths)
    managed_paths = AppPaths(
        policy=tmp_path / "system-policy.yaml",
        state=tmp_path / "managed-state.json",
        user_state_dir=tmp_path / "managed-users",
        builder_home=tmp_path / "managed-builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "managed-request.json",
        log_file=tmp_path / "managed-updater.log",
    )
    monkeypatch.setattr(cli, "system_paths", lambda: managed_paths)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.setattr(cli, "_tick", lambda system_mode: 0 if not system_mode else 1)

    result = cli.main(["retry", "--user"])

    request = load_request(
        task_request_path(home, "user-tools"),
        expected_uid=None,
        expected_run_id="failed-user-run",
    )
    assert result == 0
    assert request is not None
    assert request.action == "retry"
    assert task_request_path(home, "user-tools").stat().st_uid == cli.os.getuid()


def test_user_retry_targets_this_owners_system_managed_task_without_running_root_work(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    if os.getuid() == 0:
        pytest.skip("managed personal retries must be submitted by an unprivileged owner")
    home = tmp_path / "alice"
    home.mkdir()
    paths = AppPaths(
        policy=tmp_path / "project.yaml",
        state=tmp_path / "state.json",
        user_state_dir=tmp_path / "users",
        builder_home=tmp_path / "builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "request.json",
        log_file=tmp_path / "updater.log",
    )
    paths.policy.write_text(
        "retry:\n  user_tools:\n    user_manual_attempts: 1\n", encoding="utf-8"
    )
    uid = cli.os.getuid()
    StateStore(paths.user_state_dir / f"{uid}.json").save(
        UpdateState(phase="failed", run_id="managed-failed-run", returncode=1)
    )
    monkeypatch.setattr(cli, "system_paths", lambda: paths)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.setattr(
        cli,
        "_tick",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not run as user")),
    )

    result = cli.main(["retry", "--user"])

    request_path = task_request_path(home, "user-tools")
    request = load_request(request_path, expected_uid=uid, expected_run_id="managed-failed-run")
    assert result == 0
    assert request is not None and request.action == "retry"
    assert "submitted to the system updater" in capsys.readouterr().out


def test_user_history_reads_only_the_callers_system_managed_history(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    paths = AppPaths(
        policy=tmp_path / "project.yaml",
        state=tmp_path / "state.json",
        user_state_dir=tmp_path / "users",
        builder_home=tmp_path / "builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "request.json",
        log_file=tmp_path / "updater.log",
    )
    paths.policy.write_text("managed system policy", encoding="utf-8")
    uid = cli.os.getuid()
    user_state = paths.user_state_dir / f"{uid}.json"
    RunHistoryStore(history_path_for_state(user_state)).append(
        "user-tools",
        UpdateState(
            phase="succeeded",
            run_id="managed-user-run",
            finished_at=datetime(2026, 11, 16, 12, 0, tzinfo=ZoneInfo("UTC")).isoformat(),
            message="Personal tools completed.",
        ),
    )
    monkeypatch.setattr(cli, "system_paths", lambda: paths)

    result = cli.main(["history", "--user"])

    output = capsys.readouterr().out
    assert result == 0
    assert "Personal tools/user-tools" in output
    assert "Personal tools completed." in output


def test_system_retry_uses_pkexec_instead_of_running_as_the_desktop_user(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    paths = AppPaths(
        policy=tmp_path / "project.yaml",
        state=tmp_path / "system-state.json",
        user_state_dir=tmp_path / "users",
        builder_home=tmp_path / "builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "request.json",
        log_file=tmp_path / "updater.log",
    )
    paths.policy.write_text(
        "retry:\n  system:\n    user_manual_attempts: 1\n", encoding="utf-8"
    )
    StateStore(paths.state).save(
        UpdateState(phase="failed", run_id="failed-system-run", returncode=1)
    )
    executable = tmp_path / "user-updater"
    observed: list[list[str]] = []
    monkeypatch.setattr(cli, "system_paths", lambda: paths)
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(cli, "_executable_path", lambda: executable)
    monkeypatch.setattr(cli, "_require_trusted_system_install", lambda _path: None)
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name: "/usr/bin/pkexec" if name == "pkexec" else None,
    )

    def run(command, **_kwargs):
        observed.append(command)
        return SimpleNamespace(returncode=0, stdout="Retry complete.\n", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", run)

    result = cli.main(["retry", "--system"])

    assert result == 0
    assert observed == [["/usr/bin/pkexec", str(executable), "retry", "--system"]]
    assert "Retry complete." in capsys.readouterr().out


def test_non_root_cannot_read_or_export_system_logs(monkeypatch, tmp_path: Path, capsys) -> None:
    paths = AppPaths(
        policy=tmp_path / "project.yaml",
        state=tmp_path / "state.json",
        user_state_dir=tmp_path / "users",
        builder_home=tmp_path / "builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "request.json",
        log_file=tmp_path / "root-only.log",
    )
    monkeypatch.setattr(cli, "system_paths", lambda: paths)
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)

    result = cli.main(["logs", "--system"])

    assert result == 1
    assert "requires administrator privileges" in capsys.readouterr().err


def test_system_status_does_not_print_machine_update_output_for_users(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    paths = AppPaths(
        policy=tmp_path / "project.yaml",
        state=tmp_path / "state.json",
        user_state_dir=tmp_path / "users",
        builder_home=tmp_path / "builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "request.json",
        log_file=tmp_path / "updater.log",
    )
    StateStore(paths.state).save(
        UpdateState(phase="failed", last_output=("secret package output",))
    )
    monkeypatch.setattr(cli, "system_paths", lambda: paths)
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)

    result = cli.main(["status", "--system"])

    assert result == 0
    assert "secret package output" not in capsys.readouterr().out


def test_system_status_does_not_treat_user_history_as_state(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    paths = AppPaths(
        policy=tmp_path / "project.yaml",
        state=tmp_path / "state.json",
        user_state_dir=tmp_path / "users",
        builder_home=tmp_path / "builders",
        sudoers=tmp_path / "sudoers",
        request=tmp_path / "request.json",
        log_file=tmp_path / "updater.log",
    )
    user_state_path = paths.user_state_dir / f"{os.getuid()}.json"
    state = UpdateState(phase="succeeded", message="Personal tools completed.")
    StateStore(user_state_path).save(state)
    RunHistoryStore(history_path_for_state(user_state_path)).append("user-tools", state)
    monkeypatch.setattr(cli, "system_paths", lambda: paths)

    result = cli.main(["status", "--system"])

    output = capsys.readouterr().out
    assert result == 0
    assert output.count("user-tool updates:") == 1
    assert "user-tool updates: succeeded" in output
    assert "unknown state field" not in output


def test_untrusted_system_install_error_shows_supported_uv_command() -> None:
    with pytest.raises(RuntimeError, match="UV_TOOL_DIR=/opt/user-updater/uv-tools"):
        cli._require_trusted_system_install(Path(__file__))
