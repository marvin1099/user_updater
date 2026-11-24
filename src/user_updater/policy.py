"""Validated administrator policy for scheduled update runs."""

from __future__ import annotations

import copy
import fcntl
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_USER_TOOLS = ("flatpak", "pipx", "pip", "npm", "cargo", "gem")
_SUDO_MODES = ("package-manager", "unrestricted")
_DEFAULTS: dict[str, Any] = {
    "version": 1,
    "schedule": {
        "days": ["thu", "sun"],
        "time": "03:00",
        "response_minutes": 5,
        "user_tools_days": ["thu", "sun"],
        "user_tools_time": "18:00",
        "user_tools_response_minutes": 5,
        "check_interval_minutes": 1,
    },
    "network": {"stable_seconds": 60},
    "interaction": {
        "allow_deferral": True,
        "allow_system_deferral": True,
        "deferral_options_hours": [1, 4, 24],
        "max_user_tools_deferral_hours": 48,
        "max_system_deferral_hours": 24,
    },
    "updates": {
        "run_system": True,
        "run_user_tools": True,
        "user_tools": list(_USER_TOOLS),
        "allow_custom_user_tools": True,
    },
    "retry": {
        "system": {
            "automatic_attempts": 2,
            "delay_minutes": 15,
            "user_manual_attempts": 0,
        },
        "user_tools": {
            "automatic_attempts": 2,
            "delay_minutes": 15,
            "user_manual_attempts": 0,
        },
    },
    "testing": {
        "system": {"fail_next": 0, "succeed_next": 0},
        "user_tools": {"fail_next": 0, "succeed_next": 0},
    },
    "window": {"automatic_positioning": True},
    "system_scripts": {
        "before": {"builder": False, "root": False},
        "after": {"builder": False, "root": False},
    },
    "security": {"builder_sudo_mode": "package-manager"},
}


class PolicyError(ValueError):
    """Raised when administrator policy is malformed or unsafe."""


@dataclass(frozen=True)
class SchedulePolicy:
    days: tuple[str, ...]
    time: str
    response_minutes: int
    user_tools_days: tuple[str, ...]
    user_tools_time: str
    user_tools_response_minutes: int
    check_interval_minutes: int


@dataclass(frozen=True)
class NetworkPolicy:
    stable_seconds: int


@dataclass(frozen=True)
class InteractionPolicy:
    allow_deferral: bool
    allow_system_deferral: bool
    deferral_options_hours: tuple[int, ...]
    max_user_tools_deferral_hours: int
    max_system_deferral_hours: int


@dataclass(frozen=True)
class UpdatePolicy:
    run_system: bool
    run_user_tools: bool
    user_tools: tuple[str, ...]
    allow_custom_user_tools: bool


@dataclass(frozen=True)
class TaskRetryPolicy:
    automatic_attempts: int
    delay_minutes: int
    user_manual_attempts: int


@dataclass(frozen=True)
class RetryPolicy:
    system: TaskRetryPolicy
    user_tools: TaskRetryPolicy


@dataclass(frozen=True)
class TaskTestingPolicy:
    fail_next: int
    succeed_next: int


@dataclass(frozen=True)
class TestingPolicy:
    system: TaskTestingPolicy
    user_tools: TaskTestingPolicy


@dataclass(frozen=True)
class SecurityPolicy:
    builder_sudo_mode: str


@dataclass(frozen=True)
class WindowPolicy:
    automatic_positioning: bool


@dataclass(frozen=True)
class ScriptPhasePolicy:
    builder: bool
    root: bool


@dataclass(frozen=True)
class SystemScriptsPolicy:
    before: ScriptPhasePolicy
    after: ScriptPhasePolicy


@dataclass(frozen=True)
class Policy:
    version: int
    schedule: SchedulePolicy
    network: NetworkPolicy
    interaction: InteractionPolicy
    updates: UpdatePolicy
    retry: RetryPolicy
    testing: TestingPolicy
    window: WindowPolicy
    system_scripts: SystemScriptsPolicy
    security: SecurityPolicy


def load_policy(path: Path | None = None) -> Policy:
    """Load YAML policy, applying omitted keys from safe built-in defaults."""
    source = Path(path) if path is not None else None
    if source is None:
        raw: Any = {}
    else:
        try:
            raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        except OSError as exc:
            raise PolicyError(f"cannot read policy {source}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise PolicyError(f"invalid YAML in {source}: {exc}") from exc

    return validate_policy_data(raw)


def validate_policy_data(raw: Any) -> Policy:
    """Validate a policy mapping supplied by an administrator UI or helper."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise PolicyError("policy must be a YAML mapping")
    raw = copy.deepcopy(raw)
    interaction = raw.get("interaction")
    if isinstance(interaction, dict):
        # Older policy files limited the number of deferrals. Accept and discard
        # this obsolete setting so existing installations migrate on next save.
        interaction.pop("max_deferrals_per_run", None)
    schedule = raw.get("schedule")
    if isinstance(schedule, dict):
        # Personal tools now use the same calendar planner as system updates.
        schedule.pop("user_tools_interval_hours", None)
    merged = _merge_checked(_DEFAULTS, raw, "policy")
    return _make_policy(merged)


def policy_to_mapping(policy: Policy) -> dict[str, Any]:
    """Convert a validated policy to plain YAML/JSON-compatible values."""
    return {
        "version": policy.version,
        "schedule": {
            "days": list(policy.schedule.days),
            "time": policy.schedule.time,
            "response_minutes": policy.schedule.response_minutes,
            "user_tools_days": list(policy.schedule.user_tools_days),
            "user_tools_time": policy.schedule.user_tools_time,
            "user_tools_response_minutes": policy.schedule.user_tools_response_minutes,
            "check_interval_minutes": policy.schedule.check_interval_minutes,
        },
        "network": {"stable_seconds": policy.network.stable_seconds},
        "interaction": {
            "allow_deferral": policy.interaction.allow_deferral,
            "allow_system_deferral": policy.interaction.allow_system_deferral,
            "deferral_options_hours": list(policy.interaction.deferral_options_hours),
            "max_user_tools_deferral_hours": (policy.interaction.max_user_tools_deferral_hours),
            "max_system_deferral_hours": policy.interaction.max_system_deferral_hours,
        },
        "updates": {
            "run_system": policy.updates.run_system,
            "run_user_tools": policy.updates.run_user_tools,
            "user_tools": list(policy.updates.user_tools),
            "allow_custom_user_tools": policy.updates.allow_custom_user_tools,
        },
        "retry": {
            task: {
                "automatic_attempts": getattr(policy.retry, task).automatic_attempts,
                "delay_minutes": getattr(policy.retry, task).delay_minutes,
                "user_manual_attempts": getattr(policy.retry, task).user_manual_attempts,
            }
            for task in ("system", "user_tools")
        },
        "testing": {
            task: {
                "fail_next": getattr(policy.testing, task).fail_next,
                "succeed_next": getattr(policy.testing, task).succeed_next,
            }
            for task in ("system", "user_tools")
        },
        "window": {"automatic_positioning": policy.window.automatic_positioning},
        "system_scripts": {
            phase: {
                "builder": getattr(policy.system_scripts, phase).builder,
                "root": getattr(policy.system_scripts, phase).root,
            }
            for phase in ("before", "after")
        },
        "security": {"builder_sudo_mode": policy.security.builder_sudo_mode},
    }


def write_policy(path: Path, raw: Any, file_mode: int = 0o644) -> Policy:
    """Validate and atomically replace a policy file."""
    policy = validate_policy_data(raw)
    path = Path(path)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise PolicyError("policy directory must not be a symbolic link")
    if path.is_symlink():
        raise PolicyError("policy file must not be a symbolic link")
    with _policy_lock(path):
        _write_policy_unlocked(path, policy, file_mode)
    return policy


def consume_test_outcome(path: Path, task: str) -> str | None:
    """Atomically consume one forced test result, prioritizing failures over successes."""
    if task not in {"system", "user_tools"}:
        raise ValueError(f"unknown update task for testing outcome: {task}")
    path = Path(path)
    if not path.is_file():
        return None
    if path.is_symlink() or path.parent.is_symlink():
        raise PolicyError("policy path must not contain a symbolic link")
    with _policy_lock(path):
        policy = load_policy(path)
        task_policy = getattr(policy.testing, task)
        if task_policy.fail_next:
            outcome = "failure"
            updated_task_policy = replace(task_policy, fail_next=task_policy.fail_next - 1)
        elif task_policy.succeed_next:
            outcome = "success"
            updated_task_policy = replace(task_policy, succeed_next=task_policy.succeed_next - 1)
        else:
            return None
        updated_testing = replace(policy.testing, **{task: updated_task_policy})
        updated_policy = replace(policy, testing=updated_testing)
        file_mode = stat.S_IMODE(path.stat().st_mode)
        _write_policy_unlocked(path, updated_policy, file_mode)
    return outcome


def _write_policy_unlocked(path: Path, policy: Policy, file_mode: int) -> None:
    rendered = yaml.safe_dump(policy_to_mapping(policy), sort_keys=False)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary_path = Path(stream.name)
            os.fchmod(stream.fileno(), file_mode)
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, file_mode)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise PolicyError(f"cannot write policy {path}: {exc}") from exc


@contextmanager
def _policy_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f".{path.name}.lock")
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
        ):
            raise PolicyError("policy lock must be a singly linked regular file owned by updater")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def default_policy_text(user_mode: bool = False) -> str:
    """Render the bundled default policy for a system or user-only install."""
    defaults = copy.deepcopy(_DEFAULTS)
    if user_mode:
        defaults["updates"]["run_system"] = False
    return yaml.safe_dump(defaults, sort_keys=False)


def _merge_checked(default: dict[str, Any], override: dict[str, Any], path: str) -> dict[str, Any]:
    unknown = set(override) - set(default)
    if unknown:
        key = sorted(unknown)[0]
        raise PolicyError(f"unknown key {path}.{key}")

    result: dict[str, Any] = {}
    for key, default_value in default.items():
        if key not in override:
            result[key] = default_value
            continue
        value = override[key]
        if isinstance(default_value, dict):
            if not isinstance(value, dict):
                raise PolicyError(f"{path}.{key} must be a mapping")
            result[key] = _merge_checked(default_value, value, f"{path}.{key}")
        else:
            result[key] = value
    return result


def _schedule_days(raw: Any, name: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise PolicyError(f"{name} must be a non-empty list")
    days = tuple(str(day).lower() for day in raw)
    if len(set(days)) != len(days) or any(day not in _DAYS for day in days):
        raise PolicyError(f"{name} must contain unique values from {_DAYS}")
    return days


def _schedule_time(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or len(raw) != 5 or raw[2] != ":":
        raise PolicyError(f"{name} must use HH:MM 24-hour format")
    hour, minute = raw.split(":", 1)
    if (
        not hour.isdigit()
        or not minute.isdigit()
        or not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59)
    ):
        raise PolicyError(f"{name} must use a valid HH:MM 24-hour time")
    return raw


def _make_policy(raw: dict[str, Any]) -> Policy:
    version = _integer(raw["version"], "version", minimum=1, maximum=1)

    schedule_raw = raw["schedule"]
    days = _schedule_days(schedule_raw["days"], "schedule.days")
    clock = _schedule_time(schedule_raw["time"], "schedule.time")
    user_tools_days = _schedule_days(
        schedule_raw["user_tools_days"], "schedule.user_tools_days"
    )
    user_tools_time = _schedule_time(
        schedule_raw["user_tools_time"], "schedule.user_tools_time"
    )

    schedule = SchedulePolicy(
        days=days,
        time=clock,
        response_minutes=_integer(
            schedule_raw["response_minutes"], "schedule.response_minutes", minimum=1, maximum=60
        ),
        user_tools_days=user_tools_days,
        user_tools_time=user_tools_time,
        user_tools_response_minutes=_integer(
            schedule_raw["user_tools_response_minutes"],
            "schedule.user_tools_response_minutes",
            minimum=1,
            maximum=60,
        ),
        check_interval_minutes=_integer(
            schedule_raw["check_interval_minutes"],
            "schedule.check_interval_minutes",
            minimum=1,
            maximum=60,
        ),
    )

    network_raw = raw["network"]
    network = NetworkPolicy(
        stable_seconds=_integer(
            network_raw["stable_seconds"],
            "network.stable_seconds",
            minimum=1,
            maximum=3600,
        )
    )

    interaction_raw = raw["interaction"]
    options_raw = interaction_raw["deferral_options_hours"]
    if not isinstance(options_raw, list):
        raise PolicyError("interaction.deferral_options_hours must be a list")
    options = tuple(
        _integer(value, "interaction.deferral_options_hours item", minimum=1, maximum=168)
        for value in options_raw
    )
    if len(set(options)) != len(options):
        raise PolicyError("interaction.deferral_options_hours must be unique")
    interaction = InteractionPolicy(
        allow_deferral=_boolean(interaction_raw["allow_deferral"], "interaction.allow_deferral"),
        allow_system_deferral=_boolean(
            interaction_raw["allow_system_deferral"], "interaction.allow_system_deferral"
        ),
        deferral_options_hours=options,
        max_user_tools_deferral_hours=_integer(
            interaction_raw["max_user_tools_deferral_hours"],
            "interaction.max_user_tools_deferral_hours",
            minimum=1,
            maximum=168,
        ),
        max_system_deferral_hours=_integer(
            interaction_raw["max_system_deferral_hours"],
            "interaction.max_system_deferral_hours",
            minimum=1,
            maximum=168,
        ),
    )

    updates_raw = raw["updates"]
    tools_raw = updates_raw["user_tools"]
    if not isinstance(tools_raw, list):
        raise PolicyError("updates.user_tools must be a list")
    tools = tuple(str(tool).lower() for tool in tools_raw)
    if len(set(tools)) != len(tools) or any(tool not in _USER_TOOLS for tool in tools):
        raise PolicyError(f"updates.user_tools may contain only {_USER_TOOLS}")
    updates = UpdatePolicy(
        run_system=_boolean(updates_raw["run_system"], "updates.run_system"),
        run_user_tools=_boolean(updates_raw["run_user_tools"], "updates.run_user_tools"),
        user_tools=tools,
        allow_custom_user_tools=_boolean(
            updates_raw["allow_custom_user_tools"], "updates.allow_custom_user_tools"
        ),
    )

    retry_raw = raw["retry"]
    retry = RetryPolicy(
        system=_make_task_retry_policy(retry_raw["system"], "retry.system"),
        user_tools=_make_task_retry_policy(retry_raw["user_tools"], "retry.user_tools"),
    )

    testing_raw = raw["testing"]
    testing = TestingPolicy(
        system=_make_task_testing_policy(testing_raw["system"], "testing.system"),
        user_tools=_make_task_testing_policy(testing_raw["user_tools"], "testing.user_tools"),
    )

    window_raw = raw["window"]
    window = WindowPolicy(
        automatic_positioning=_boolean(
            window_raw["automatic_positioning"], "window.automatic_positioning"
        )
    )

    scripts_raw = raw["system_scripts"]
    system_scripts = SystemScriptsPolicy(
        before=ScriptPhasePolicy(
            builder=_boolean(
                scripts_raw["before"]["builder"], "system_scripts.before.builder"
            ),
            root=_boolean(scripts_raw["before"]["root"], "system_scripts.before.root"),
        ),
        after=ScriptPhasePolicy(
            builder=_boolean(scripts_raw["after"]["builder"], "system_scripts.after.builder"),
            root=_boolean(scripts_raw["after"]["root"], "system_scripts.after.root"),
        ),
    )

    security_raw = raw["security"]
    sudo_mode = security_raw["builder_sudo_mode"]
    if sudo_mode not in _SUDO_MODES:
        raise PolicyError(f"security.builder_sudo_mode must be one of {_SUDO_MODES}")
    security = SecurityPolicy(builder_sudo_mode=sudo_mode)

    return Policy(
        version=version,
        schedule=schedule,
        network=network,
        interaction=interaction,
        updates=updates,
        retry=retry,
        testing=testing,
        window=window,
        system_scripts=system_scripts,
        security=security,
    )


def _make_task_retry_policy(raw: dict[str, Any], name: str) -> TaskRetryPolicy:
    return TaskRetryPolicy(
        automatic_attempts=_integer(
            raw["automatic_attempts"], f"{name}.automatic_attempts", minimum=0, maximum=5
        ),
        delay_minutes=_integer(
            raw["delay_minutes"], f"{name}.delay_minutes", minimum=1, maximum=1440
        ),
        user_manual_attempts=_integer(
            raw["user_manual_attempts"], f"{name}.user_manual_attempts", minimum=0, maximum=5
        ),
    )


def _make_task_testing_policy(raw: dict[str, Any], name: str) -> TaskTestingPolicy:
    return TaskTestingPolicy(
        fail_next=_integer(raw["fail_next"], f"{name}.fail_next", minimum=0, maximum=100),
        succeed_next=_integer(raw["succeed_next"], f"{name}.succeed_next", minimum=0, maximum=100),
    )


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PolicyError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise PolicyError(f"{name} must be true or false")
    return value
