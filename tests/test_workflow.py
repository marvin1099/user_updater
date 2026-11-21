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
    topgrade_config = manager.account.home / ".config" / "topgrade.toml"
    assert not topgrade_config.exists()

    def runner(command: list[str], **kwargs: object) -> CommandResult:
        assert topgrade_config.read_text(encoding="utf-8") == (
            "[misc]\n"
            "assume_yes = true\n\n"
            "[linux]\n"
            'garuda_update_arguments = "--noconfirm"\n\n'
            "[flatpak]\n"
            "use_sudo = true\n"
        )
        config_stat = topgrade_config.stat()
        assert stat.S_IMODE(config_stat.st_mode) == 0o600
        assert config_stat.st_uid == manager.account.uid
        assert config_stat.st_gid == manager.account.gid
        calls.append((command, kwargs))
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

    result = workflow.run(output.append)

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
    assert manager.removed == [manager.account]
    assert "Packages updated" in output


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
