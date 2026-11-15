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
    user = DesktopUser("alice", 1001, 1001, home, tmp_path / "runtime")

    results = updater.update_user(user, ("flatpak", "pipx"), lambda _line: None)

    assert len(calls) == 1
    assert calls[0][0] == ["/usr/bin/flatpak", "update", "--user", "-y"]
    assert calls[0][1]["user_id"] == 1001
    assert calls[0][1]["group_id"] == 1001
    assert calls[0][1]["env"]["HOME"] == str(home)
    assert results[0].tool == "flatpak"
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
