import os
import stat
from pathlib import Path

import pytest

from user_updater.accounts import BuilderAccount
from user_updater.policy import load_policy
from user_updater.runner import CommandResult
from user_updater.script_hooks import ensure_hook_directories
from user_updater.workflow import UpdateWorkflow


class FakeBuilderManager:
    def __init__(self, home: Path) -> None:
        self.home_root = home
        self.package_manager: str | None = None
        self.account = BuilderAccount(
            "uupd-0123456789",
            os.getuid(),
            os.getgid(),
            home / "uupd-0123456789",
            home / "sudoers" / "user-updater-uupd-0123456789",
            supplementary_group_ids=(937,),
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
            "[misc]\nassume_yes = true\n\n[flatpak]\nuse_sudo = true\n"
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
        "--notify-end",
        "never",
        "--cleanup",
        "--no-retry",
    ]
    assert calls[0][1]["user_id"] == manager.account.uid
    assert calls[0][1]["group_id"] == manager.account.gid
    assert calls[0][1]["extra_group_ids"] == (937,)
    assert calls[0][1]["env"]["HOME"] == str(manager.account.home)
    assert calls[0][1]["env"]["XDG_CONFIG_HOME"] == str(topgrade_config.parent)
    assert progress_ends == [None]
    assert manager.removed == [manager.account]
    assert "Packages updated" in output


def test_fwupd_runs_as_root_in_a_firmware_only_topgrade_pass(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("version: 1\nupdates:\n  run_user_tools: false\n", encoding="utf-8")
    manager = FakeBuilderManager(tmp_path / "builders")
    calls: list[tuple[list[str], dict[str, object]]] = []
    firmware_configs: list[Path] = []

    def runner(command: list[str], **kwargs: object) -> CommandResult:
        calls.append((command, kwargs))
        if "--only" in command:
            config_path = Path(command[command.index("--config") + 1])
            assert config_path.read_text(encoding="utf-8") == (
                "[misc]\nassume_yes = true\n\n[firmware]\nupgrade = true\n"
            )
            assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
            firmware_configs.append(config_path)
        return CommandResult(0, 1.0, False, ())

    def which(name: str, path: str | None = None) -> str | None:
        return {
            "topgrade": "/usr/bin/topgrade",
            "fwupdmgr": "/usr/bin/fwupdmgr",
        }.get(name)

    workflow = UpdateWorkflow(
        policy=load_policy(config),
        builder_manager=manager,
        runner=runner,
        which=which,
        session_detector=lambda: [],
    )
    output: list[str] = []

    result = workflow.run(output.append)

    assert result.returncode == 0
    assert len(calls) == 2
    builder_command, builder_options = calls[0]
    assert builder_command == [
        "/usr/bin/topgrade",
        "--notify-end",
        "never",
        "--cleanup",
        "--disable",
        "firmware",
        "--no-retry",
    ]
    assert builder_options["user_id"] == manager.account.uid
    firmware_command, firmware_options = calls[1]
    assert firmware_command == [
        "/usr/bin/topgrade",
        "--config",
        str(firmware_configs[0]),
        "--only",
        "firmware",
        "--allow-root",
        "--yes",
        "firmware",
        "--notify-end",
        "never",
        "--no-retry",
    ]
    assert "user_id" not in firmware_options
    assert firmware_options["env"]["HOME"] == "/root"
    assert firmware_options["env"]["USER"] == "root"
    assert firmware_options["cwd"] == Path("/")
    assert any("firmware step as root" in line for line in output)
    assert manager.removed == [manager.account]
    assert len(firmware_configs) == 1
    assert not firmware_configs[0].exists()


def test_root_firmware_failure_is_reported_and_builder_is_cleaned(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("version: 1\nupdates:\n  run_user_tools: false\n", encoding="utf-8")
    manager = FakeBuilderManager(tmp_path / "builders")
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> CommandResult:
        calls.append(command)
        return CommandResult(int("--only" in command), 1.0, False, ())

    workflow = UpdateWorkflow(
        policy=load_policy(config),
        builder_manager=manager,
        runner=runner,
        which=lambda name, path=None: {
            "topgrade": "/usr/bin/topgrade",
            "fwupdmgr": "/usr/bin/fwupdmgr",
        }.get(name),
        session_detector=lambda: [],
    )

    result = workflow.run(lambda _line: None)

    assert result.returncode == 1
    assert len(calls) == 2
    assert not Path(calls[1][calls[1].index("--config") + 1]).exists()
    assert manager.removed == [manager.account]


def test_system_hooks_run_in_order_as_the_selected_identity(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text(
        """
version: 1
updates:
  run_user_tools: false
system_scripts:
  before:
    root: true
    builder: true
  after:
    builder: true
    root: true
""",
        encoding="utf-8",
    )
    manager = FakeBuilderManager(tmp_path / "builders")
    script_root = tmp_path / "etc" / "user-updater" / "scripts"
    script_root.parent.mkdir(parents=True)
    ensure_hook_directories(script_root, owner_uid=os.getuid())
    scripts = (
        script_root / "before" / "root.d" / "root-before",
        script_root / "before" / "builder.d" / "builder-before",
        script_root / "after" / "builder.d" / "builder-after",
        script_root / "after" / "root.d" / "root-after",
    )
    for script in scripts:
        script.write_text("#!/bin/sh\n", encoding="utf-8")
        script.chmod(0o755)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> CommandResult:
        calls.append((command, kwargs))
        return CommandResult(0, 0.1, False, ())

    workflow = UpdateWorkflow(
        policy=load_policy(config),
        builder_manager=manager,
        runner=runner,
        which=lambda name, path=None: "/usr/bin/topgrade" if name == "topgrade" else None,
        session_detector=lambda: [],
        script_root=script_root,
        script_owner_uid=os.getuid(),
    )

    result = workflow.run(lambda _line: None)

    assert result.returncode == 0
    assert [command for command, _kwargs in calls] == [
        [str(scripts[0])],
        [str(scripts[1])],
        ["/usr/bin/topgrade", "--notify-end", "never", "--cleanup", "--no-retry"],
        [str(scripts[2])],
        [str(scripts[3])],
    ]
    for index in (0, 4):
        assert "user_id" not in calls[index][1]
        assert calls[index][1]["env"]["USER"] == "root"
        assert calls[index][1]["cwd"] == Path("/")
    for index in (1, 3):
        assert calls[index][1]["user_id"] == manager.account.uid
        assert calls[index][1]["group_id"] == manager.account.gid
        assert calls[index][1]["extra_group_ids"] == (937,)
        assert calls[index][1]["cwd"] == manager.account.home


def test_failed_before_hook_skips_topgrade_and_after_hooks(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text(
        """
version: 1
updates:
  run_user_tools: false
system_scripts:
  before:
    root: true
  after:
    root: true
""",
        encoding="utf-8",
    )
    manager = FakeBuilderManager(tmp_path / "builders")
    script_root = tmp_path / "etc" / "user-updater" / "scripts"
    script_root.parent.mkdir(parents=True)
    ensure_hook_directories(script_root, owner_uid=os.getuid())
    root_before = script_root / "before" / "root.d" / "fail"
    root_after = script_root / "after" / "root.d" / "should-not-run"
    for script in (root_before, root_after):
        script.write_text("#!/bin/sh\n", encoding="utf-8")
        script.chmod(0o755)
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> CommandResult:
        calls.append(command)
        return CommandResult(2, 0.1, False, ())

    workflow = UpdateWorkflow(
        policy=load_policy(config),
        builder_manager=manager,
        runner=runner,
        which=lambda name, path=None: "/usr/bin/topgrade" if name == "topgrade" else None,
        session_detector=lambda: [],
        script_root=script_root,
        script_owner_uid=os.getuid(),
    )
    output: list[str] = []

    result = workflow.run(output.append)

    assert result.returncode != 0
    assert result.system_result is not None
    assert result.system_result.returncode == 2
    assert calls == [[str(root_before)]]
    assert any("skipping the system update" in line for line in output)
    assert manager.removed == [manager.account]


def test_after_hook_runs_after_topgrade_failure_and_preserves_failure(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text(
        """
version: 1
updates:
  run_user_tools: false
system_scripts:
  after:
    root: true
""",
        encoding="utf-8",
    )
    manager = FakeBuilderManager(tmp_path / "builders")
    script_root = tmp_path / "etc" / "user-updater" / "scripts"
    script_root.parent.mkdir(parents=True)
    ensure_hook_directories(script_root, owner_uid=os.getuid())
    root_after = script_root / "after" / "root.d" / "cleanup"
    root_after.write_text("#!/bin/sh\n", encoding="utf-8")
    root_after.chmod(0o755)
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> CommandResult:
        calls.append(command)
        code = 1 if command[0] == "/usr/bin/topgrade" else 0
        return CommandResult(code, 0.1, False, ())

    workflow = UpdateWorkflow(
        policy=load_policy(config),
        builder_manager=manager,
        runner=runner,
        which=lambda name, path=None: "/usr/bin/topgrade" if name == "topgrade" else None,
        session_detector=lambda: [],
        script_root=script_root,
        script_owner_uid=os.getuid(),
    )

    result = workflow.run(lambda _line: None)

    assert result.returncode != 0
    assert calls == [
        ["/usr/bin/topgrade", "--notify-end", "never", "--cleanup", "--no-retry"],
        [str(root_after)],
    ]


def test_garuda_uses_full_topgrade_without_a_custom_paru_command(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("version: 1\nupdates:\n  run_user_tools: false\n", encoding="utf-8")
    manager = FakeBuilderManager(tmp_path / "builders")
    manager.package_manager = "/usr/bin/garuda-update"
    calls: list[tuple[list[str], dict[str, object]]] = []
    which_calls: list[str] = []

    def runner(command: list[str], **kwargs: object) -> CommandResult:
        calls.append((command, kwargs))
        return CommandResult(0, 1.0, False, ())

    def which(name: str, path: str | None = None) -> str | None:
        which_calls.append(name)
        return "/usr/bin/topgrade" if name == "topgrade" else None

    workflow = UpdateWorkflow(
        policy=load_policy(config),
        builder_manager=manager,
        runner=runner,
        which=which,
        session_detector=lambda: [],
    )

    result = workflow.run(lambda _line: None)

    assert result.returncode == 0
    assert [call[0] for call in calls] == [
        ["/usr/bin/garuda-update", "--noconfirm"],
        ["/usr/bin/topgrade", "--notify-end", "never", "--no-retry"],
    ]
    garuda_environment = calls[0][1]["env"]
    assert garuda_environment["SKIP_MIRRORLIST"] == "1"
    assert "UPDATE_AUR" not in garuda_environment
    assert "user_id" not in calls[0][1]
    assert calls[1][1]["user_id"] == manager.account.uid
    assert which_calls == ["topgrade", "fwupdmgr"]
    assert "assume_yes = true" in (manager.account.home / ".config" / "topgrade.toml").read_text(
        encoding="utf-8"
    )
    assert 'arch_package_manager = "paru"' in (
        manager.account.home / ".config" / "topgrade.toml"
    ).read_text(encoding="utf-8")


def test_garuda_failure_prevents_topgrade_paru_step(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("version: 1\nupdates:\n  run_user_tools: false\n", encoding="utf-8")
    manager = FakeBuilderManager(tmp_path / "builders")
    manager.package_manager = "/usr/bin/garuda-update"
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> CommandResult:
        calls.append(command)
        return CommandResult(1, 1.0, False, ("Garuda failed",))

    workflow = UpdateWorkflow(
        policy=load_policy(config),
        builder_manager=manager,
        runner=runner,
        which=lambda name, path=None: "/usr/bin/topgrade" if name == "topgrade" else None,
        session_detector=lambda: [],
    )

    result = workflow.run(lambda _line: None)

    assert result.returncode != 0
    assert calls == [["/usr/bin/garuda-update", "--noconfirm"]]
    assert manager.removed == [manager.account]


@pytest.mark.parametrize(
    "existing_config",
    [
        "[misc]\nassume_yes = false\n",
        "[misc]assume_yes = true\n",
        "assume_yes = false\n",
        "[misc]\nassume_yes = true\n",
    ],
)
def test_topgrade_config_replaces_existing_or_malformed_config(
    tmp_path: Path, existing_config: str
) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("version: 1\nupdates:\n  run_user_tools: false\n", encoding="utf-8")
    manager = FakeBuilderManager(tmp_path / "builders")
    topgrade_config = manager.account.home / ".config" / "topgrade.toml"
    topgrade_config.parent.mkdir(parents=True)
    topgrade_config.write_text(existing_config, encoding="utf-8")
    expected_config = "[misc]\nassume_yes = true\n\n[flatpak]\nuse_sudo = true\n"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> CommandResult:
        calls.append((command, kwargs))
        assert topgrade_config.read_text(encoding="utf-8") == expected_config
        return CommandResult(0, 1.0, False, ())

    workflow = UpdateWorkflow(
        policy=load_policy(config),
        builder_manager=manager,
        runner=runner,
        which=lambda name, path=None: "/usr/bin/topgrade" if name == "topgrade" else None,
        session_detector=lambda: [],
    )

    result = workflow.run(lambda _line: None)

    assert result.returncode == 0
    assert len(calls) == 1
    environment = calls[0][1]["env"]
    assert environment["HOME"] == str(manager.account.home)
    assert environment["XDG_CONFIG_HOME"] == str(topgrade_config.parent)
    assert topgrade_config.read_text(encoding="utf-8") == expected_config


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


@pytest.mark.parametrize(("outcome", "expected_returncode"), [("failure", 1), ("success", 0)])
def test_forced_test_outcome_skips_every_real_update_command(
    tmp_path: Path, outcome: str, expected_returncode: int
) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("version: 1\nupdates:\n  run_user_tools: false\n", encoding="utf-8")
    manager = FakeBuilderManager(tmp_path / "builders")
    calls: list[list[str]] = []
    output: list[str] = []
    workflow = UpdateWorkflow(
        policy=load_policy(config),
        builder_manager=manager,
        runner=lambda command, **_kwargs: calls.append(command) or CommandResult(0, 0.1, False, ()),
        which=lambda name, path=None: "/usr/bin/topgrade" if name == "topgrade" else None,
        session_detector=lambda: [],
        test_outcome=lambda: outcome,
    )

    result = workflow.run(output.append)

    assert result.returncode == expected_returncode
    assert calls == []
    assert manager.removed == []
    assert any(
        "TEST MODE" in line and "no package or user-tool commands" in line for line in output
    )
