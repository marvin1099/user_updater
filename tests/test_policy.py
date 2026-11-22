from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from user_updater.policy import (
    PolicyError,
    consume_test_outcome,
    load_policy,
    policy_to_mapping,
    validate_policy_data,
    write_policy,
)
from user_updater.rescheduling import available_reschedule_hours

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_default_policy_matches_product_defaults() -> None:
    policy = load_policy(PROJECT_ROOT / "project.yaml")

    assert policy.schedule.days == ("thu", "sun")
    assert policy.schedule.time == "03:00"
    assert policy.schedule.response_minutes == 5
    assert policy.schedule.user_tools_interval_hours == 24
    assert policy.schedule.user_tools_response_minutes == 5
    assert policy.interaction.allow_deferral is True
    assert policy.interaction.allow_system_deferral is True
    assert policy.interaction.deferral_options_hours == (1, 4, 24)
    assert policy.interaction.max_user_tools_deferral_hours == 48
    assert policy.interaction.max_system_deferral_hours == 24
    assert policy.updates.run_system is True
    assert policy.window.automatic_positioning is True
    assert "flatpak" in policy.updates.user_tools
    assert policy.updates.allow_custom_user_tools is True
    assert policy.retry.system.automatic_attempts == 2
    assert policy.retry.system.delay_minutes == 15
    assert policy.retry.system.user_manual_attempts == 0
    assert policy.retry.user_tools.automatic_attempts == 2
    assert policy.retry.user_tools.user_manual_attempts == 0


def test_automatic_window_positioning_defaults_on_and_can_be_disabled() -> None:
    policy = load_policy()
    assert policy.window.automatic_positioning is True

    raw = policy_to_mapping(policy)
    raw["window"]["automatic_positioning"] = False

    updated = validate_policy_data(raw)
    assert updated.window.automatic_positioning is False
    assert policy_to_mapping(updated)["window"]["automatic_positioning"] is False


def test_administrator_can_configure_automatic_and_user_retry_limits(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text(
        """
version: 1
retry:
  system:
    automatic_attempts: 4
    delay_minutes: 30
    user_manual_attempts: 2
  user_tools:
    automatic_attempts: 1
    delay_minutes: 5
    user_manual_attempts: 3
""",
        encoding="utf-8",
    )

    policy = load_policy(config)

    assert policy.retry.system.automatic_attempts == 4
    assert policy.retry.system.delay_minutes == 30
    assert policy.retry.system.user_manual_attempts == 2
    assert policy.retry.user_tools.automatic_attempts == 1
    assert policy.retry.user_tools.delay_minutes == 5
    assert policy.retry.user_tools.user_manual_attempts == 3


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("automatic_attempts", 6),
        ("delay_minutes", 0),
        ("user_manual_attempts", -1),
    ],
)
def test_retry_policy_rejects_out_of_range_limits(tmp_path: Path, key: str, value: int) -> None:
    config = tmp_path / "project.yaml"
    config.write_text(
        f"""
version: 1
retry:
  user_tools:
    {key}: {value}
""",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match=key):
        load_policy(config)


def test_admin_can_disable_rescheduling_without_disabling_response_window(
    tmp_path: Path,
) -> None:
    config = tmp_path / "project.yaml"
    config.write_text(
        """
version: 1
schedule:
  days: [sun]
  time: '02:30'
  response_minutes: 8
interaction:
  allow_deferral: false
""",
        encoding="utf-8",
    )

    policy = load_policy(config)

    assert policy.schedule.response_minutes == 8
    now = datetime(2026, 11, 15, 3, 0, tzinfo=ZoneInfo("UTC"))
    assert available_reschedule_hours(policy, "user-tools", now.isoformat(), now) == ()


@pytest.mark.parametrize(
    ("elapsed_hours", "expected"),
    [(0, (1, 4, 24)), (34, (1, 4, 13)), (47, ()), (48, ())],
)
def test_user_reschedule_options_shrink_with_remaining_time(
    elapsed_hours: int,
    expected: tuple[int, ...],
) -> None:
    policy = load_policy(PROJECT_ROOT / "project.yaml")
    scheduled_for = datetime(2026, 11, 15, 3, 0, tzinfo=ZoneInfo("UTC"))
    now = scheduled_for + timedelta(hours=elapsed_hours)

    assert (
        available_reschedule_hours(policy, "user-tools", scheduled_for.isoformat(), now) == expected
    )


def test_unknown_admin_policy_keys_are_rejected(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text(
        """
version: 1
schedule:
  days: [sun]
  time: '03:00'
interaction:
  allow_arbitrary_command: true
""",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match="unknown"):
        load_policy(config)


def test_system_reschedule_is_capped_before_the_next_system_schedule() -> None:
    policy = load_policy(PROJECT_ROOT / "project.yaml")
    raw = policy_to_mapping(policy)
    raw["schedule"]["days"] = ["sun"]
    raw["schedule"]["time"] = "17:00"
    policy = validate_policy_data(raw)
    scheduled_for = datetime(2026, 11, 15, 3, 0, tzinfo=ZoneInfo("UTC"))

    assert available_reschedule_hours(
        policy, "system", scheduled_for.isoformat(), scheduled_for
    ) == (1, 4, 13)


def test_legacy_deferral_count_is_ignored_and_removed_when_saved(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("version: 1\ninteraction:\n  max_deferrals_per_run: 0\n", encoding="utf-8")

    policy = load_policy(config)

    assert policy.interaction.max_user_tools_deferral_hours == 48
    assert "max_deferrals_per_run" not in policy_to_mapping(policy)["interaction"]


def test_admin_policy_write_validates_and_round_trips_atomically(tmp_path: Path) -> None:
    path = tmp_path / "etc" / "project.yaml"
    original = load_policy(PROJECT_ROOT / "project.yaml")
    mapping = policy_to_mapping(original)
    mapping["schedule"]["user_tools_interval_hours"] = 12

    saved = write_policy(path, mapping)

    assert saved.schedule.user_tools_interval_hours == 12
    assert load_policy(path) == saved
    assert path.stat().st_mode & 0o777 == 0o644


def test_forced_test_outcomes_are_consumed_once_with_failures_first(tmp_path: Path) -> None:
    path = tmp_path / "etc" / "project.yaml"
    mapping = policy_to_mapping(load_policy(PROJECT_ROOT / "project.yaml"))
    mapping["testing"]["system"] = {"fail_next": 1, "succeed_next": 1}
    write_policy(path, mapping)

    assert consume_test_outcome(path, "system") == "failure"
    assert consume_test_outcome(path, "system") == "success"
    assert consume_test_outcome(path, "system") is None
    remaining = load_policy(path).testing.system
    assert remaining.fail_next == 0
    assert remaining.succeed_next == 0
