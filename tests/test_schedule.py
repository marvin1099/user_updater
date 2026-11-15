from datetime import datetime
from zoneinfo import ZoneInfo

from user_updater.policy import load_policy
from user_updater.schedule import next_scheduled_after


def test_next_scheduled_time_uses_configured_local_days_and_time() -> None:
    policy = load_policy()
    friday_noon = datetime(2026, 11, 13, 12, 0, tzinfo=ZoneInfo("UTC"))

    assert next_scheduled_after(policy, friday_noon) == datetime(
        2026, 11, 15, 3, 0, tzinfo=ZoneInfo("UTC")
    )


def test_exact_schedule_time_advances_to_the_next_occurrence() -> None:
    policy = load_policy()
    sunday_at_schedule = datetime(2026, 11, 15, 3, 0, tzinfo=ZoneInfo("UTC"))

    assert next_scheduled_after(policy, sunday_at_schedule) == datetime(
        2026, 11, 19, 3, 0, tzinfo=ZoneInfo("UTC")
    )


def test_local_schedule_remains_at_wall_clock_time_across_daylight_saving() -> None:
    policy = load_policy()
    before_spring_transition = datetime(2026, 3, 28, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))

    scheduled = next_scheduled_after(policy, before_spring_transition)

    assert scheduled == datetime(2026, 3, 29, 3, 0, tzinfo=ZoneInfo("Europe/Berlin"))
