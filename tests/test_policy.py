from pathlib import Path

import pytest

from user_updater.policy import (
    PolicyError,
    check_deferral,
    load_policy,
    policy_to_mapping,
    write_policy,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_default_policy_matches_product_defaults() -> None:
    policy = load_policy(PROJECT_ROOT / "project.yaml")

    assert policy.schedule.days == ("thu", "sun")
    assert policy.schedule.time == "03:00"
    assert policy.schedule.response_minutes == 5
    assert policy.schedule.user_tools_interval_hours == 24
    assert policy.schedule.user_tools_response_minutes == 5
    assert policy.interaction.allow_deferral is True
    assert policy.interaction.deferral_options_hours == (1, 4, 24)
    assert policy.updates.run_system is True
    assert "flatpak" in policy.updates.user_tools
    assert policy.updates.allow_custom_user_tools is True


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
    assert check_deferral(policy, hours=1, deferrals_used=0) is False


@pytest.mark.parametrize(
    ("hours", "deferrals_used", "expected"),
    [
        (1, 0, True),
        (4, 1, True),
        (2, 0, False),
        (24, 2, False),
    ],
)
def test_deferral_is_limited_by_admin_options_and_count(
    hours: int,
    deferrals_used: int,
    expected: bool,
) -> None:
    policy = load_policy(PROJECT_ROOT / "project.yaml")

    assert check_deferral(policy, hours, deferrals_used) is expected


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


def test_boolean_is_not_accepted_as_an_integer_deferral() -> None:
    policy = load_policy(PROJECT_ROOT / "project.yaml")

    assert check_deferral(policy, hours=True, deferrals_used=0) is False


def test_admin_policy_write_validates_and_round_trips_atomically(tmp_path: Path) -> None:
    path = tmp_path / "etc" / "project.yaml"
    original = load_policy(PROJECT_ROOT / "project.yaml")
    mapping = policy_to_mapping(original)
    mapping["schedule"]["user_tools_interval_hours"] = 12

    saved = write_policy(path, mapping)

    assert saved.schedule.user_tools_interval_hours == 12
    assert load_policy(path) == saved
    assert path.stat().st_mode & 0o777 == 0o644
