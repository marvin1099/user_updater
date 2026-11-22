"""System and per-user update orchestration."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass

from .accounts import BuilderAccount, BuilderAccountManager
from .policy import Policy
from .runner import CommandResult, run_command
from .sessions import DesktopUser, detect_graphical_users
from .user_tools import ToolResult, UserToolUpdater

Runner = Callable[..., CommandResult]
Which = Callable[..., str | None]
Output = Callable[[str], None]
Progress = Callable[[str], None]
ProgressEnd = Callable[[], None]
TestOutcome = Callable[[], str | None]


@dataclass(frozen=True)
class WorkflowResult:
    returncode: int
    system_result: CommandResult | None
    user_results: tuple[tuple[str, ToolResult], ...]


class UpdateWorkflow:
    """Run configured machine updates and then selected desktop-user tools."""

    def __init__(
        self,
        policy: Policy,
        builder_manager: BuilderAccountManager | None,
        runner: Runner = run_command,
        which: Which = shutil.which,
        session_detector: Callable[[], list[DesktopUser]] = detect_graphical_users,
        user_tool_updater: UserToolUpdater | None = None,
        test_outcome: TestOutcome | None = None,
    ) -> None:
        self.policy = policy
        self.builder_manager = builder_manager
        self.runner = runner
        self.which = which
        self.session_detector = session_detector
        self.user_tool_updater = user_tool_updater or UserToolUpdater(runner, which)
        self.test_outcome = test_outcome

    def run(
        self,
        on_output: Output,
        on_idle: Callable[[float], None] | None = None,
        *,
        on_progress: Progress | None = None,
        on_progress_end: ProgressEnd | None = None,
    ) -> WorkflowResult:
        forced_outcome = self.test_outcome() if self.test_outcome is not None else None
        if forced_outcome in {"failure", "success"}:
            description = "failure" if forced_outcome == "failure" else "success"
            on_output(
                f"TEST MODE: forcing this update to report {description}; "
                "no package or user-tool commands were run."
            )
            return WorkflowResult(
                returncode=int(forced_outcome == "failure"),
                system_result=(
                    CommandResult(int(forced_outcome == "failure"), 0.0, False, ())
                    if self.policy.updates.run_system
                    else None
                ),
                user_results=(),
            )

        failed = False
        system_result: CommandResult | None = None
        user_results: list[tuple[str, ToolResult]] = []

        if self.policy.updates.run_system:
            system_result = self._run_system_update(
                on_output, on_idle, on_progress, on_progress_end
            )
            failed = system_result.returncode != 0

        if self.policy.updates.run_user_tools:
            users = self.session_detector()
            if not users:
                on_output("No active local desktop session; skipping per-user tool updates.")
            for user in users:
                on_output(f"Updating configured user tools for {user.username}.")
                results = self.user_tool_updater.update_user(
                    user,
                    self.policy.updates.user_tools,
                    lambda line, name=user.username: on_output(f"[{name}] {line}"),
                    on_idle,
                    allow_custom_tools=self.policy.updates.allow_custom_user_tools,
                    on_progress=(
                        (lambda line, name=user.username: on_progress(f"[{name}] {line}"))
                        if on_progress is not None
                        else None
                    ),
                    on_progress_end=on_progress_end,
                )
                for result in results:
                    user_results.append((user.username, result))
                    on_output(f"[{user.username}] {result.message}")
                    failed = failed or result.status == "failed"

        return WorkflowResult(
            returncode=1 if failed else 0,
            system_result=system_result,
            user_results=tuple(user_results),
        )

    def _run_system_update(
        self,
        on_output: Output,
        on_idle: Callable[[float], None] | None,
        on_progress: Progress | None,
        on_progress_end: ProgressEnd | None,
    ) -> CommandResult:
        account: BuilderAccount | None = None
        result = CommandResult(1, 0.0, False, ())
        if self.builder_manager is None:
            on_output("System updates are unavailable in this installation mode.")
            return CommandResult(1, 0.0, False, ())
        try:
            account = self.builder_manager.create()
            executable = self.which(
                "topgrade",
                path="/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin",
            )
            if executable is None:
                on_output("Error: topgrade is not installed or is not in the system PATH.")
                result = CommandResult(127, 0.0, False, ())
            else:
                garuda_mode = self.builder_manager.package_manager == "/usr/bin/garuda-update"
                self._write_topgrade_config(account, use_paru=garuda_mode)
                environment = _builder_environment(account)
                if garuda_mode:
                    result = self._run_garuda_update(
                        on_output,
                        on_idle,
                        on_progress,
                        on_progress_end,
                    )
                if not garuda_mode or result.returncode == 0:
                    result = self._run_topgrade(
                        account,
                        executable,
                        environment,
                        on_output,
                        on_idle,
                        on_progress,
                        on_progress_end,
                        # The disposable Garuda builder needs no cache cleanup.
                        cleanup=not garuda_mode,
                    )
                if result.returncode != 0:
                    on_output(f"System update command exited with status {result.returncode}.")
        except (OSError, RuntimeError, ValueError) as exc:
            on_output(f"System update setup failed: {exc}")
            result = CommandResult(1, 0.0, False, ())
        finally:
            if account is not None:
                try:
                    self.builder_manager.remove(account)
                    on_output("Temporary builder account removed.")
                except (OSError, RuntimeError, ValueError) as exc:
                    on_output(f"Warning: failed to remove temporary builder: {exc}")
                    result = CommandResult(
                        returncode=result.returncode or 1,
                        duration_seconds=result.duration_seconds,
                        cancelled=result.cancelled,
                        output_tail=result.output_tail,
                    )
        return result

    def _run_garuda_update(
        self,
        on_output: Output,
        on_idle: Callable[[float], None] | None,
        on_progress: Progress | None,
        on_progress_end: ProgressEnd | None,
    ) -> CommandResult:
        """Run Garuda's wrapper as the updater, leaving AUR work to Topgrade/Paru."""
        on_output("Running Garuda's system update before Topgrade's Paru step.")
        environment = os.environ.copy()
        environment.pop("UPDATE_AUR", None)
        environment["SKIP_MIRRORLIST"] = "1"
        options: dict[str, object] = {
            "on_output": on_output,
            "on_idle": on_idle,
            "env": environment,
        }
        if on_progress is not None:
            options["on_progress"] = on_progress
        if on_progress_end is not None:
            options["on_progress_end"] = on_progress_end
        return self.runner(
            ["/usr/bin/garuda-update", "--noconfirm"],
            **options,
        )

    def _run_topgrade(
        self,
        account: BuilderAccount,
        executable: str,
        environment: dict[str, str],
        on_output: Output,
        on_idle: Callable[[float], None] | None,
        on_progress: Progress | None,
        on_progress_end: ProgressEnd | None,
        *,
        cleanup: bool = True,
    ) -> CommandResult:
        on_output(f"Running topgrade as temporary builder {account.name}.")
        options: dict[str, object] = {
            "on_output": on_output,
            "on_idle": on_idle,
            "env": environment,
            "cwd": account.home,
            "user_id": account.uid,
            "group_id": account.gid,
            "extra_group_ids": account.supplementary_group_ids,
        }
        if on_progress is not None:
            options["on_progress"] = on_progress
        if on_progress_end is not None:
            options["on_progress_end"] = on_progress_end
        # The updater GUI handles notifications; the temporary builder has no
        # desktop runtime for Topgrade's own notification command.
        command = [executable, "--notify-end", "never"]
        if cleanup:
            command.append("--cleanup")
        command.append("--no-retry")
        return self.runner(command, **options)

    @staticmethod
    def _write_topgrade_config(account: BuilderAccount, *, use_paru: bool = False) -> None:
        # Seed the temporary home before Topgrade's first-run config prompt can block.
        config_directory = account.home / ".config"
        config_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        config_file = config_directory / "topgrade.toml"
        contents = "[misc]\nassume_yes = true\n\n[flatpak]\nuse_sudo = true\n"
        if use_paru:
            contents += '\n[linux]\narch_package_manager = "paru"\n'
        config_file.write_text(contents, encoding="utf-8")
        os.chmod(config_file, 0o600)
        os.chown(config_directory, account.uid, account.gid)
        os.chown(config_file, account.uid, account.gid)


def _builder_environment(account: BuilderAccount) -> dict[str, str]:
    return {
        "HOME": str(account.home),
        "USER": account.name,
        "LOGNAME": account.name,
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin",
        "XDG_CONFIG_HOME": str(account.home / ".config"),
        "XDG_CACHE_HOME": str(account.home / ".cache"),
        "XDG_DATA_HOME": str(account.home / ".local" / "share"),
        "LANG": "C.UTF-8",
        "TMPDIR": "/tmp",
    }
