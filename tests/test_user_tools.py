import os
from pathlib import Path

from user_updater.runner import CommandResult
from user_updater.sessions import DesktopUser
from user_updater.user_tools import UserToolUpdater


def test_user_tool_commands_run_as_the_selected_desktop_user(tmp_path: Path) -> None:
    home = tmp_path / "alice"
    home.mkdir()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> CommandResult:
        calls.append((command, kwargs))
        return CommandResult(0, 0.01, False, ())

    updater = UserToolUpdater(
        runner=runner,
        which=lambda name, path=None: f"/usr/bin/{name}" if name == "flatpak" else None,
    )
    selected_uid = os.geteuid() + 1
    selected_gid = os.getegid() + 1
    user = DesktopUser("alice", selected_uid, selected_gid, home, tmp_path / "runtime")

    results = updater.update_user(user, ("flatpak", "pipx"), lambda _line: None)

    assert len(calls) == 1
    assert calls[0][0] == ["/usr/bin/flatpak", "update", "--user", "-y"]
    assert calls[0][1]["user_id"] == selected_uid
    assert calls[0][1]["group_id"] == selected_gid
    assert calls[0][1]["env"]["HOME"] == str(home)
    assert results[0].tool == "flatpak"
    assert results[0].returncode == 0


def test_user_mode_runs_tool_without_privilege_transition_for_current_user(
    tmp_path: Path,
) -> None:
    home = tmp_path / "marvin"
    home.mkdir()
    fake_flatpak = tmp_path / "flatpak"
    fake_flatpak.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_flatpak.chmod(0o700)
    user = DesktopUser("marvin", os.getuid(), os.getgid(), home, tmp_path / "runtime")

    updater = UserToolUpdater(which=lambda _name, path=None: str(fake_flatpak))
    results = updater.update_user(
        user,
        ("flatpak",),
        lambda _line: None,
        allow_custom_tools=False,
    )

    assert results[0].status == "updated"
    assert results[0].returncode == 0


def test_pip_user_updates_parse_json_without_jq(tmp_path: Path) -> None:
    home = tmp_path / "alice"
    home.mkdir()
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> CommandResult:
        calls.append(command)
        if "--outdated" in command:
            return CommandResult(0, 0.01, False, ('[{"name":"sample-package"}]',))
        return CommandResult(0, 0.01, False, ())

    updater = UserToolUpdater(
        runner=runner,
        which=lambda name, path=None: f"/usr/bin/{name}" if name == "python3" else None,
    )
    user = DesktopUser("alice", 1001, 1001, home, tmp_path / "runtime")

    result = updater.update_user(user, ("pip",), lambda _line: None)

    assert calls[0][-4:] == ["list", "--user", "--outdated", "--format=json"]
    assert calls[1][-4:] == ["install", "--user", "--upgrade", "sample-package"]
    assert result[0].returncode == 0


def test_custom_executable_tools_run_last_as_the_user_and_can_be_disabled(
    tmp_path: Path,
) -> None:
    home = tmp_path / "alice"
    custom_dir = home / ".config" / "user_updater" / "tools.d"
    custom_dir.mkdir(parents=True)
    script = custom_dir / "sync-notes"
    script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    script.chmod(0o700)
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> CommandResult:
        calls.append(command)
        return CommandResult(0, 0.01, False, ())

    updater = UserToolUpdater(runner=runner, which=lambda _name, path=None: None)
    user = DesktopUser("alice", os.getuid(), os.getgid(), home, tmp_path / "runtime")

    results = updater.update_user(user, (), lambda _line: None)
    assert [result.tool for result in results] == ["custom:sync-notes"]
    assert calls == [[str(script)]]

    disabled = updater.update_user(
        user,
        (),
        lambda _line: None,
        allow_custom_tools=False,
    )
    assert disabled == []
    assert calls == [[str(script)]]
