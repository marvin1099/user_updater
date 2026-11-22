"""Per-desktop-user tool updates, kept separate from machine package updates."""

from __future__ import annotations

import json
import re
import shutil
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .runner import CommandResult, run_command
from .sessions import DesktopUser

Runner = Callable[..., CommandResult]
Which = Callable[..., str | None]
Output = Callable[[str], None]
Progress = Callable[[str], None]
ProgressEnd = Callable[[], None]
_CUSTOM_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.+-]{1,128}$")


@dataclass(frozen=True)
class ToolResult:
    tool: str
    status: str
    returncode: int | None
    message: str


class UserToolUpdater:
    """Run allow-listed user tools using the selected user's UID and home."""

    def __init__(self, runner: Runner = run_command, which: Which = shutil.which) -> None:
        self.runner = runner
        self.which = which

    def update_user(
        self,
        user: DesktopUser,
        tools: Sequence[str],
        on_output: Output,
        on_idle: Callable[[float], None] | None = None,
        allow_custom_tools: bool = True,
        on_progress: Progress | None = None,
        on_progress_end: ProgressEnd | None = None,
    ) -> list[ToolResult]:
        results: list[ToolResult] = []
        env = _user_environment(user)
        for tool in tools:
            if tool == "pip":
                results.append(
                    self._update_pip(user, env, on_output, on_idle, on_progress, on_progress_end)
                )
                continue
            if tool == "npm":
                results.append(
                    self._update_npm(user, env, on_output, on_idle, on_progress, on_progress_end)
                )
                continue
            if tool == "cargo":
                results.append(
                    self._update_cargo(user, env, on_output, on_idle, on_progress, on_progress_end)
                )
                continue

            command_name, arguments = {
                "flatpak": ("flatpak", ["update", "--user", "-y"]),
                "pipx": ("pipx", ["upgrade-all"]),
                "gem": ("gem", ["update", "--user-install"]),
            }.get(tool, ("", []))
            executable = self.which(command_name, path=env["PATH"]) if command_name else None
            if executable is None:
                results.append(
                    _skipped(tool, f"{command_name or tool} is not installed for this user.")
                )
                continue
            results.append(
                self._run_tool(
                    tool,
                    [executable, *arguments],
                    user,
                    env,
                    on_output,
                    on_idle,
                    on_progress,
                    on_progress_end,
                )
            )
        if allow_custom_tools:
            results.extend(
                self._run_custom_tools(user, env, on_output, on_idle, on_progress, on_progress_end)
            )
        return results

    def _run_custom_tools(
        self,
        user: DesktopUser,
        env: dict[str, str],
        on_output: Output,
        on_idle: Callable[[float], None] | None,
        on_progress: Progress | None = None,
        on_progress_end: ProgressEnd | None = None,
    ) -> list[ToolResult]:
        directory = user.home / ".config" / "user-updater" / "tools.d"
        if not directory.exists():
            legacy_directory = user.home / ".config" / "user_updater" / "tools.d"
            if legacy_directory.exists() or legacy_directory.is_symlink():
                directory = legacy_directory
        try:
            metadata = directory.stat(follow_symlinks=False)
        except FileNotFoundError:
            return []
        except OSError as exc:
            return [_failed("custom-tools", 1, f"Could not inspect custom tools: {exc}")]
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != user.uid:
            return [_failed("custom-tools", 1, "Custom tools directory must be user-owned.")]

        results: list[ToolResult] = []
        try:
            candidates = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            return [_failed("custom-tools", 1, f"Could not read custom tools: {exc}")]

        for script in candidates:
            if not _CUSTOM_TOOL_NAME.fullmatch(script.name):
                continue
            try:
                metadata = script.stat(follow_symlinks=False)
            except OSError as exc:
                results.append(_failed(f"custom:{script.name}", 1, str(exc)))
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != user.uid
                or not metadata.st_mode & stat.S_IXUSR
            ):
                continue
            result = self._run_tool(
                f"custom:{script.name}",
                [str(script)],
                user,
                env,
                on_output,
                on_idle,
                on_progress,
                on_progress_end,
            )
            results.append(result)
        return results

    def _update_pip(
        self,
        user: DesktopUser,
        env: dict[str, str],
        on_output: Output,
        on_idle: Callable[[float], None] | None,
        on_progress: Progress | None = None,
        on_progress_end: ProgressEnd | None = None,
    ) -> ToolResult:
        python = self.which("python3", path=env["PATH"])
        if python is None:
            return _skipped("pip", "python3 is not installed for this user.")
        listing = self.runner(
            [python, "-m", "pip", "list", "--user", "--outdated", "--format=json"],
            on_output=on_output,
            on_idle=on_idle,
            user_id=user.uid,
            group_id=user.gid,
            env=env,
            cwd=user.home,
            tail_lines=10_000,
            **_progress_options(on_progress),
            **_progress_end_options(on_progress_end),
        )
        if listing.returncode != 0:
            return _failed("pip", listing.returncode, "Could not list outdated user packages.")
        try:
            output = "\n".join(listing.output_tail)
            start, end = output.find("["), output.rfind("]")
            packages = json.loads(output[start : end + 1]) if start >= 0 and end >= start else []
            names = [item["name"] for item in packages if isinstance(item, dict)]
        except (json.JSONDecodeError, KeyError, TypeError):
            return _failed("pip", 1, "pip returned invalid JSON for outdated packages.")
        for package in names:
            result = self.runner(
                [python, "-m", "pip", "install", "--user", "--upgrade", package],
                on_output=on_output,
                on_idle=on_idle,
                user_id=user.uid,
                group_id=user.gid,
                env=env,
                cwd=user.home,
                **_progress_options(on_progress),
                **_progress_end_options(on_progress_end),
            )
            if result.returncode != 0:
                return _failed("pip", result.returncode, f"Failed to upgrade {package}.")
        message = (
            f"Updated {len(names)} user package(s)." if names else "No outdated user packages."
        )
        return ToolResult("pip", "updated", 0, message)

    def _update_npm(
        self,
        user: DesktopUser,
        env: dict[str, str],
        on_output: Output,
        on_idle: Callable[[float], None] | None,
        on_progress: Progress | None = None,
        on_progress_end: ProgressEnd | None = None,
    ) -> ToolResult:
        npm = self.which("npm", path=env["PATH"])
        if npm is None:
            return _skipped("npm", "npm is not installed for this user.")
        prefix = self.runner(
            [npm, "config", "get", "prefix"],
            on_output=on_output,
            on_idle=on_idle,
            user_id=user.uid,
            group_id=user.gid,
            env=env,
            cwd=user.home,
            **_progress_options(on_progress),
            **_progress_end_options(on_progress_end),
        )
        if prefix.returncode != 0:
            return _failed("npm", prefix.returncode, "Could not determine the npm global prefix.")
        prefix_path = Path(prefix.output_tail[-1]).resolve() if prefix.output_tail else Path("/")
        if not _is_inside(prefix_path, user.home.resolve()):
            return _skipped("npm", "Global npm prefix is outside the user's home directory.")
        return self._run_tool(
            "npm",
            [npm, "update", "-g"],
            user,
            env,
            on_output,
            on_idle,
            on_progress,
            on_progress_end,
        )

    def _update_cargo(
        self,
        user: DesktopUser,
        env: dict[str, str],
        on_output: Output,
        on_idle: Callable[[float], None] | None,
        on_progress: Progress | None = None,
        on_progress_end: ProgressEnd | None = None,
    ) -> ToolResult:
        cargo = self.which("cargo", path=env["PATH"])
        if cargo is None:
            return _skipped("cargo", "cargo is not installed for this user.")
        probe = self.runner(
            [cargo, "install-update", "-V"],
            on_output=on_output,
            on_idle=on_idle,
            user_id=user.uid,
            group_id=user.gid,
            env=env,
            cwd=user.home,
            **_progress_options(on_progress),
            **_progress_end_options(on_progress_end),
        )
        if probe.returncode != 0:
            return _skipped("cargo", "Install cargo-update to enable Cargo binary updates.")
        return self._run_tool(
            "cargo",
            [cargo, "install-update", "-a"],
            user,
            env,
            on_output,
            on_idle,
            on_progress,
            on_progress_end,
        )

    def _run_tool(
        self,
        name: str,
        command: list[str],
        user: DesktopUser,
        env: dict[str, str],
        on_output: Output,
        on_idle: Callable[[float], None] | None,
        on_progress: Progress | None = None,
        on_progress_end: ProgressEnd | None = None,
    ) -> ToolResult:
        result = self.runner(
            command,
            on_output=on_output,
            on_idle=on_idle,
            user_id=user.uid,
            group_id=user.gid,
            env=env,
            cwd=user.home,
            **_progress_options(on_progress),
            **_progress_end_options(on_progress_end),
        )
        if result.returncode == 0:
            return ToolResult(name, "updated", 0, f"{name} update completed.")
        return _failed(name, result.returncode, f"{name} update failed.")


def _user_environment(user: DesktopUser) -> dict[str, str]:
    path = ":".join(
        (
            str(user.home / ".local" / "bin"),
            str(user.home / ".cargo" / "bin"),
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        )
    )
    env = {
        "HOME": str(user.home),
        "USER": user.username,
        "LOGNAME": user.username,
        "PATH": path,
        "LANG": "C.UTF-8",
        "TMPDIR": "/tmp",
    }
    if user.runtime_dir.is_dir():
        env["XDG_RUNTIME_DIR"] = str(user.runtime_dir)
        bus = user.runtime_dir / "bus"
        if bus.exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    return env


def _progress_options(on_progress: Progress | None) -> dict[str, Progress]:
    return {"on_progress": on_progress} if on_progress is not None else {}


def _progress_end_options(
    on_progress_end: ProgressEnd | None,
) -> dict[str, ProgressEnd]:
    return {"on_progress_end": on_progress_end} if on_progress_end is not None else {}


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _skipped(tool: str, message: str) -> ToolResult:
    return ToolResult(tool, "skipped", None, message)


def _failed(tool: str, returncode: int, message: str) -> ToolResult:
    return ToolResult(tool, "failed", returncode, message)
