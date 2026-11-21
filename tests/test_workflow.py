import os
import stat
from pathlib import Path

from user_updater.accounts import BuilderAccount
from user_updater.policy import load_policy
from user_updater.runner import CommandResult
from user_updater.workflow import UpdateWorkflow


class FakeBuilderManager:
    def __init__(self, home: Path) -> None:
        self.home_root = home
        self.account = BuilderAccount(
            "uupd-0123456789",
            os.getuid(),
            os.getgid(),
            home / "uupd-0123456789",
            home / "sudoers" / "user-updater-uupd-0123456789",
        )
        self.account.home.mkdir(parents=True)
        self.removed: list[BuilderAccount] = []
        self.fail_remove = False

    def create(self) -> BuilderAccount:
        return self.account

    def remove(self, account: BuilderAccount) -> None:
        if self.fail_remove:
            raise RuntimeError("simulated cleanup failure")
        self.removed.append(account)


def test_topgrade_runs_as_the_temporary_builder_and_is_cleaned(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("version: 1\nupdates:\n  run_user_tools: false\n", encoding="utf-8")
    manager = FakeBuilderManager(tmp_path / "builders")
    calls: list[tuple[list[str], dict[str, object]]] = []
    progress_ends: list[None] = []
    topgrade_config = manager.account.home / ".config" / "topgrade.toml"
    assert not topgrade_config.exists()

    def runner(command: list[str], **kwargs: object) -> CommandResult:
        assert topgrade_config.read_text(encoding="utf-8") == (
            "[misc]\n"
            "assume_yes = true\n\n"
            "[flatpak]\n"
            "use_sudo = true\n"
        )
        config_stat = topgrade_config.stat()
        assert stat.S_IMODE(config_stat.st_mode) == 0o600
        assert config_stat.st_uid == manager.account.uid
        assert config_stat.st_gid == manager.account.gid
        calls.append((command, kwargs))
        if "on_progress_end" in kwargs:
            kwargs["on_progress_end"]()
        kwargs["on_output"]("Packages updated")
        return CommandResult(0, 3.0, False, ("Packages updated",))

    workflow = UpdateWorkflow(
        policy=load_policy(config),
        builder_manager=manager,
        runner=runner,
        which=lambda name, path=None: "/usr/bin/topgrade" if name == "topgrade" else None,
        session_detector=lambda: [],
    )
    output: list[str] = []

    result = workflow.run(
        output.append,
        on_progress_end=lambda: progress_ends.append(None),
    )

    assert result.returncode == 0
    assert calls[0][0] == [
        "/usr/bin/topgrade",
        "--cleanup",
        "--no-retry",
        "--only",
        "system",
        "flatpak",
    ]
    assert calls[0][1]["user_id"] == manager.account.uid
    assert calls[0][1]["group_id"] == manager.account.gid
    assert calls[0][1]["env"]["HOME"] == str(manager.account.home)
    assert calls[0][1]["env"]["XDG_CONFIG_HOME"] == str(topgrade_config.parent)
    assert progress_ends == [None]
    assert manager.removed == [manager.account]
    assert "Packages updated" in output


def test_garuda_updates_aur_with_noninteractive_paru_and_keeps_garuda_system_step(
    tmp_path: Path,
) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("version: 1\nupdates:\n  run_user_tools: false\n", encoding="utf-8")
    manager = FakeBuilderManager(tmp_path / "builders")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> CommandResult:
        calls.append((command, kwargs))
        return CommandResult(0, 1.0, False, ())

    executables = {
        "topgrade": "/usr/bin/topgrade",
        "garuda-update": "/usr/bin/garuda-update",
        "paru": "/usr/bin/paru",
    }
    workflow = UpdateWorkflow(
        policy=load_policy(config),
        builder_manager=manager,
        runner=runner,
        which=lambda name, path=None: executables.get(name),
        session_detector=lambda: [],
    )

    result = workflow.run(lambda _line: None)

    assert result.returncode == 0
    assert [call[0] for call in calls] == [
        ["/usr/bin/garuda-update", "--noconfirm"],
        ["/usr/bin/paru", "-Sua", "--noconfirm"],
        ["/usr/bin/topgrade", "--cleanup", "--no-retry", "--only", "flatpak"],
    ]
    garuda_environment = calls[0][1]["env"]
    assert garuda_environment["SKIP_MIRRORLIST"] == "1"
    assert "UPDATE_AUR" not in garuda_environment
    assert calls[1][1]["user_id"] == manager.account.uid


def test_garuda_skips_aur_when_system_update_fails(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("version: 1\nupdates:\n  run_user_tools: false\n", encoding="utf-8")
    manager = FakeBuilderManager(tmp_path / "builders")
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> CommandResult:
        calls.append(command)
        return CommandResult(1 if command[0] == "/usr/bin/garuda-update" else 0, 1.0, False, ())

    executables = {
        "topgrade": "/usr/bin/topgrade",
        "garuda-update": "/usr/bin/garuda-update",
        "paru": "/usr/bin/paru",
    }
    workflow = UpdateWorkflow(
        policy=load_policy(config),
        builder_manager=manager,
        runner=runner,
        which=lambda name, path=None: executables.get(name),
        session_detector=lambda: [],
    )

    result = workflow.run(lambda _line: None)

    assert result.returncode == 1
    assert calls == [
        ["/usr/bin/garuda-update", "--noconfirm"],
        ["/usr/bin/topgrade", "--cleanup", "--no-retry", "--only", "flatpak"],
    ]


def test_missing_topgrade_fails_cleanly_and_still_removes_builder(tmp_path: Path) -> None:
    manager = FakeBuilderManager(tmp_path / "builders")
    workflow = UpdateWorkflow(
        policy=load_policy(),
        builder_manager=manager,
        runner=lambda *_args, **_kwargs: CommandResult(0, 0, False, ()),
        which=lambda _name, path=None: None,
        session_detector=lambda: [],
    )
    output: list[str] = []

    result = workflow.run(output.append)

    assert result.returncode != 0
    assert manager.removed == [manager.account]
    assert any("topgrade is not installed" in line for line in output)


def test_failed_builder_cleanup_makes_successful_update_fail_closed(tmp_path: Path) -> None:
    manager = FakeBuilderManager(tmp_path / "builders")
    manager.fail_remove = True
    workflow = UpdateWorkflow(
        policy=load_policy(),
        builder_manager=manager,
        runner=lambda *_args, **_kwargs: CommandResult(0, 0.1, False, ()),
        which=lambda name, path=None: "/usr/bin/topgrade" if name == "topgrade" else None,
        session_detector=lambda: [],
    )
    output: list[str] = []

    result = workflow.run(output.append)

    assert result.returncode != 0
    assert any("failed to remove temporary builder" in line for line in output)
