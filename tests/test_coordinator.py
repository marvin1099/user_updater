from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from user_updater.coordinator import TickAction, UpdateCoordinator
from user_updater.policy import (
    TaskRetryPolicy,
    load_policy,
    policy_to_mapping,
    validate_policy_data,
)
from user_updater.requests import UserRequest
from user_updater.state import StateStore, UpdateState

NOW = datetime(2026, 11, 15, 3, 0, tzinfo=ZoneInfo("UTC"))


def test_scheduled_update_waits_for_response_then_runs_without_a_reply(tmp_path: Path) -> None:
    store = _store_due_now(tmp_path)
    coordinator = UpdateCoordinator(load_policy(), store)

    assert coordinator.tick(NOW) is TickAction.WAITING_FOR_RESPONSE
    assert store.load().response_deadline == (NOW + timedelta(minutes=5)).isoformat()
    assert (
        coordinator.tick(NOW + timedelta(minutes=4, seconds=59)) is TickAction.WAITING_FOR_RESPONSE
    )
    assert coordinator.tick(NOW + timedelta(minutes=5)) is TickAction.RUN
    assert store.load().phase == "running"


def test_allowed_deferral_reschedules_and_shows_another_response_window(tmp_path: Path) -> None:
    store = _store_due_now(tmp_path)
    coordinator = UpdateCoordinator(load_policy(), store)
    coordinator.tick(NOW)

    action = coordinator.tick(
        NOW + timedelta(minutes=1),
        [UserRequest(run_id=store.load().run_id or "", action="defer", hours=1)],
    )

    deferred = store.load()
    assert action is TickAction.DEFERRED
    assert deferred.deferrals_used == 1
    assert deferred.deferred_until == (NOW + timedelta(hours=1, minutes=1)).isoformat()
    assert coordinator.tick(NOW + timedelta(hours=1, minutes=1)) is TickAction.WAITING_FOR_RESPONSE
    assert coordinator.tick(NOW + timedelta(hours=1, minutes=6)) is TickAction.RUN


def test_rescheduling_can_be_repeated_until_the_time_limit(tmp_path: Path) -> None:
    store = _store_due_now(tmp_path)
    coordinator = UpdateCoordinator(load_policy(), store)
    coordinator.tick(NOW)

    response_at = NOW + timedelta(minutes=1)
    for deferrals in range(1, 4):
        state = store.load()
        assert (
            coordinator.tick(
                response_at,
                [UserRequest(run_id=state.run_id or "", action="defer", hours=1)],
            )
            is TickAction.DEFERRED
        )
        assert store.load().deferrals_used == deferrals
        resume_at = datetime.fromisoformat(store.load().deferred_until or "")
        assert coordinator.tick(resume_at) is TickAction.WAITING_FOR_RESPONSE
        response_at = resume_at + timedelta(minutes=1)


def test_reschedule_is_rejected_when_the_maximum_time_window_is_exhausted(
    tmp_path: Path,
) -> None:
    store = _store_due_now(tmp_path)
    scheduled_for = NOW - timedelta(hours=47)
    store.save(
        UpdateState(
            phase="awaiting_response",
            run_id="scheduled-run",
            scheduled_for=scheduled_for.isoformat(),
            response_deadline=(NOW + timedelta(minutes=5)).isoformat(),
        )
    )
    coordinator = UpdateCoordinator(load_policy(), store)

    action = coordinator.tick(
        NOW + timedelta(minutes=1),
        [UserRequest(run_id="scheduled-run", action="defer", hours=1)],
    )

    assert action is TickAction.WAITING_FOR_RESPONSE
    assert store.load().phase == "awaiting_response"


def test_boolean_is_not_treated_as_a_one_hour_reschedule(tmp_path: Path) -> None:
    store = _store_due_now(tmp_path)
    store.save(
        UpdateState(
            phase="awaiting_response",
            run_id="scheduled-run",
            scheduled_for=NOW.isoformat(),
            response_deadline=(NOW + timedelta(minutes=5)).isoformat(),
        )
    )
    coordinator = UpdateCoordinator(load_policy(), store)

    action = coordinator.tick(
        NOW + timedelta(minutes=1),
        [UserRequest(run_id="scheduled-run", action="defer", hours=True)],
    )

    assert action is TickAction.WAITING_FOR_RESPONSE


def test_system_reschedule_uses_system_policy_and_ignores_start_now(
    tmp_path: Path,
) -> None:
    raw = policy_to_mapping(load_policy())
    raw["schedule"]["days"] = ["sun"]
    raw["schedule"]["time"] = "17:00"
    policy = validate_policy_data(raw)
    store = _store_due_now(tmp_path)
    store.save(
        UpdateState(
            phase="awaiting_response",
            run_id="system-run",
            scheduled_for=NOW.isoformat(),
            next_scheduled_at=NOW.isoformat(),
            response_deadline=(NOW + timedelta(minutes=5)).isoformat(),
        )
    )
    coordinator = UpdateCoordinator(policy, store, task_id="system")

    assert (
        coordinator.tick(
            NOW + timedelta(minutes=1),
            [UserRequest(run_id="system-run", action="defer", hours=13)],
        )
        is TickAction.DEFERRED
    )
    assert store.load().deferred_until == (NOW + timedelta(hours=13, minutes=1)).isoformat()

    store.save(
        UpdateState(
            phase="awaiting_response",
            run_id="system-start-now",
            scheduled_for=NOW.isoformat(),
            response_deadline=(NOW + timedelta(minutes=5)).isoformat(),
        )
    )
    assert (
        coordinator.tick(
            NOW + timedelta(minutes=1),
            [UserRequest(run_id="system-start-now", action="start_now")],
        )
        is TickAction.WAITING_FOR_RESPONSE
    )


def test_timely_response_is_honored_even_if_worker_polls_after_deadline(
    tmp_path: Path,
) -> None:
    store = _store_due_now(tmp_path)
    store.save(
        UpdateState(
            phase="awaiting_response",
            run_id="scheduled-run",
            scheduled_for=NOW.isoformat(),
            response_deadline=(NOW + timedelta(minutes=5)).isoformat(),
        )
    )
    coordinator = UpdateCoordinator(load_policy(), store)
    timely_request = UserRequest(
        run_id="scheduled-run",
        action="defer",
        hours=1,
        submitted_at=(NOW + timedelta(minutes=4, seconds=59)).isoformat(),
    )

    action = coordinator.tick(NOW + timedelta(minutes=5, seconds=30), [timely_request])

    assert action is TickAction.DEFERRED


def test_late_response_does_not_extend_the_response_window(tmp_path: Path) -> None:
    store = _store_due_now(tmp_path)
    store.save(
        UpdateState(
            phase="awaiting_response",
            run_id="scheduled-run",
            scheduled_for=NOW.isoformat(),
            response_deadline=(NOW + timedelta(minutes=5)).isoformat(),
        )
    )
    coordinator = UpdateCoordinator(load_policy(), store)
    late_request = UserRequest(
        run_id="scheduled-run",
        action="defer",
        hours=1,
        submitted_at=(NOW + timedelta(minutes=5, seconds=1)).isoformat(),
    )

    action = coordinator.tick(NOW + timedelta(minutes=6), [late_request])

    assert action is TickAction.RUN


def test_interval_schedule_starts_on_first_user_session_then_repeats_from_completion(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "user-state.json")
    coordinator = UpdateCoordinator(
        load_policy(),
        store,
        interval_hours=24,
        response_minutes=3,
    )

    assert coordinator.tick(NOW) is TickAction.WAITING_FOR_RESPONSE
    assert store.load().response_deadline == (NOW + timedelta(minutes=3)).isoformat()
    assert coordinator.tick(NOW + timedelta(minutes=3)) is TickAction.RUN
    coordinator.complete(0, NOW + timedelta(minutes=4))

    due_at = NOW + timedelta(hours=24, minutes=4)
    assert store.load().next_scheduled_at == due_at.isoformat()
    assert coordinator.tick(due_at - timedelta(seconds=1)) is TickAction.NOT_DUE
    assert coordinator.tick(due_at) is TickAction.WAITING_FOR_RESPONSE


def test_admin_can_disable_deferral_without_skipping_the_prompt(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("version: 1\ninteraction:\n  allow_deferral: false\n", encoding="utf-8")
    policy = load_policy(config)
    store = _store_due_now(tmp_path)
    coordinator = UpdateCoordinator(policy, store)
    coordinator.tick(NOW)

    action = coordinator.tick(
        NOW + timedelta(minutes=1),
        [UserRequest(run_id=store.load().run_id or "", action="defer", hours=1)],
    )

    assert action is TickAction.WAITING_FOR_RESPONSE
    assert coordinator.tick(NOW + timedelta(minutes=5)) is TickAction.RUN


def test_interrupted_run_is_recorded_without_an_immediate_retry(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    started_at = NOW - timedelta(minutes=10)
    store.save(
        UpdateState(
            phase="running",
            run_id="interrupted-run",
            scheduled_for=(NOW - timedelta(minutes=15)).isoformat(),
            next_scheduled_at=(NOW - timedelta(minutes=15)).isoformat(),
            started_at=started_at.isoformat(),
            last_output=("Installing package A", "Package A configured"),
        )
    )
    coordinator = UpdateCoordinator(load_policy(), store)

    action = coordinator.tick(NOW)

    recovered = store.load()
    assert action is TickAction.RECOVERED_INTERRUPTED_RUN
    assert recovered.phase == "failed"
    assert recovered.run_id == "interrupted-run"
    assert recovered.started_at == started_at.isoformat()
    assert recovered.finished_at == NOW.isoformat()
    assert recovered.returncode is None
    assert recovered.next_scheduled_at is not None
    assert datetime.fromisoformat(recovered.next_scheduled_at) > NOW
    assert recovered.last_output == ("Installing package A", "Package A configured")
    assert "partial changes" in recovered.message
    assert "no immediate retry" in recovered.message


def test_failed_update_retries_automatically_after_admin_delay_and_stops_at_limit(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.json")
    store.save(
        UpdateState(
            phase="running",
            run_id="attempt-0",
            retry_group_id="scheduled-run",
            next_scheduled_at=(NOW + timedelta(days=2)).isoformat(),
            started_at=NOW.isoformat(),
        )
    )
    policy = TaskRetryPolicy(
        automatic_attempts=2,
        delay_minutes=15,
        user_manual_attempts=1,
    )
    coordinator = UpdateCoordinator(load_policy(), store, retry_policy=policy)

    coordinator.complete(1, NOW)
    first_failure = store.load()
    retry_one_at = NOW + timedelta(minutes=15)
    assert first_failure.retry_after == retry_one_at.isoformat()
    assert coordinator.tick(retry_one_at - timedelta(seconds=1)) is TickAction.NOT_DUE
    assert coordinator.tick(retry_one_at) is TickAction.RUN
    retry_one = store.load()
    assert retry_one.run_id != "attempt-0"
    assert retry_one.retry_group_id == "scheduled-run"
    assert retry_one.automatic_retries_used == 1

    coordinator.complete(1, retry_one_at)
    retry_two_at = retry_one_at + timedelta(minutes=15)
    assert store.load().retry_after == retry_two_at.isoformat()
    assert coordinator.tick(retry_two_at) is TickAction.RUN
    assert store.load().automatic_retries_used == 2

    coordinator.complete(1, retry_two_at)
    exhausted = store.load()
    assert exhausted.retry_after is None
    assert exhausted.automatic_retries_used == 2
    assert coordinator.tick(NOW + timedelta(days=1)) is TickAction.NOT_DUE


def test_user_requested_retry_increments_and_enforces_admin_cap(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.save(
        UpdateState(
            phase="failed",
            run_id="failed-attempt",
            retry_group_id="scheduled-run",
            manual_retries_used=0,
            next_scheduled_at=(NOW + timedelta(days=2)).isoformat(),
        )
    )
    coordinator = UpdateCoordinator(
        load_policy(),
        store,
        retry_policy=TaskRetryPolicy(0, 15, 1),
    )

    assert coordinator.retry_failed(NOW) is TickAction.RUN
    assert store.load().manual_retries_used == 1
    coordinator.complete(1, NOW)

    with pytest.raises(ValueError, match="manual retry limit"):
        coordinator.retry_failed(NOW + timedelta(minutes=1))


def _store_due_now(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "state.json")
    store.save(UpdateState(next_scheduled_at=NOW.isoformat()))
    return store
