"""Validated administrator policy for scheduled update runs."""

from __future__ import annotations

import copy
import os
import tempfile
from dataclasses import dataclass
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
        "user_tools_interval_hours": 24,
        "user_tools_response_minutes": 5,
        "check_interval_minutes": 1,
    },
    "interaction": {
        "allow_deferral": True,
        "deferral_options_hours": [1, 4, 24],
        "max_deferrals_per_run": 2,
    },
    "updates": {
        "run_system": True,
        "run_user_tools": True,
        "user_tools": list(_USER_TOOLS),
        "allow_custom_user_tools": True,
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
    user_tools_interval_hours: int
    user_tools_response_minutes: int
    check_interval_minutes: int


@dataclass(frozen=True)
class InteractionPolicy:
    allow_deferral: bool
    deferral_options_hours: tuple[int, ...]
    max_deferrals_per_run: int


@dataclass(frozen=True)
class UpdatePolicy:
    run_system: bool
    run_user_tools: bool
    user_tools: tuple[str, ...]
    allow_custom_user_tools: bool


@dataclass(frozen=True)
class SecurityPolicy:
    builder_sudo_mode: str


@dataclass(frozen=True)
class Policy:
    version: int
    schedule: SchedulePolicy
    interaction: InteractionPolicy
    updates: UpdatePolicy
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
            "user_tools_interval_hours": policy.schedule.user_tools_interval_hours,
            "user_tools_response_minutes": policy.schedule.user_tools_response_minutes,
            "check_interval_minutes": policy.schedule.check_interval_minutes,
        },
        "interaction": {
            "allow_deferral": policy.interaction.allow_deferral,
            "deferral_options_hours": list(policy.interaction.deferral_options_hours),
            "max_deferrals_per_run": policy.interaction.max_deferrals_per_run,
        },
        "updates": {
            "run_system": policy.updates.run_system,
            "run_user_tools": policy.updates.run_user_tools,
            "user_tools": list(policy.updates.user_tools),
            "allow_custom_user_tools": policy.updates.allow_custom_user_tools,
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
    return policy


def check_deferral(policy: Policy, hours: int, deferrals_used: int) -> bool:
    """Return whether an individual deferral request is allowed by admin policy."""
    return (
        policy.interaction.allow_deferral
        and isinstance(hours, int)
        and not isinstance(hours, bool)
        and hours in policy.interaction.deferral_options_hours
        and isinstance(deferrals_used, int)
        and not isinstance(deferrals_used, bool)
        and 0 <= deferrals_used < policy.interaction.max_deferrals_per_run
    )


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


def _make_policy(raw: dict[str, Any]) -> Policy:
    version = _integer(raw["version"], "version", minimum=1, maximum=1)

    schedule_raw = raw["schedule"]
    days_raw = schedule_raw["days"]
    if not isinstance(days_raw, list) or not days_raw:
        raise PolicyError("schedule.days must be a non-empty list")
    days = tuple(str(day).lower() for day in days_raw)
    if len(set(days)) != len(days) or any(day not in _DAYS for day in days):
        raise PolicyError(f"schedule.days must contain unique values from {_DAYS}")

    clock = schedule_raw["time"]
    if not isinstance(clock, str) or len(clock) != 5 or clock[2] != ":":
        raise PolicyError("schedule.time must use HH:MM 24-hour format")
    hour, minute = clock.split(":", 1)
    if (
        not hour.isdigit()
        or not minute.isdigit()
        or not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59)
    ):
        raise PolicyError("schedule.time must use a valid HH:MM 24-hour time")

    schedule = SchedulePolicy(
        days=days,
        time=clock,
        response_minutes=_integer(
            schedule_raw["response_minutes"], "schedule.response_minutes", minimum=1, maximum=60
        ),
        user_tools_interval_hours=_integer(
            schedule_raw["user_tools_interval_hours"],
            "schedule.user_tools_interval_hours",
            minimum=1,
            maximum=720,
        ),
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
        deferral_options_hours=options,
        max_deferrals_per_run=_integer(
            interaction_raw["max_deferrals_per_run"],
            "interaction.max_deferrals_per_run",
            minimum=0,
            maximum=10,
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

    security_raw = raw["security"]
    sudo_mode = security_raw["builder_sudo_mode"]
    if sudo_mode not in _SUDO_MODES:
        raise PolicyError(f"security.builder_sudo_mode must be one of {_SUDO_MODES}")
    security = SecurityPolicy(builder_sudo_mode=sudo_mode)

    return Policy(
        version=version,
        schedule=schedule,
        interaction=interaction,
        updates=updates,
        security=security,
    )


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PolicyError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise PolicyError(f"{name} must be true or false")
    return value
