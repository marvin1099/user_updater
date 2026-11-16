from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from user_updater.coordinator import TickAction, UpdateCoordinator
from user_updater.policy import load_policy
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


def test_unapproved_or_exhausted_deferral_does_not_postpone_the_update(tmp_path: Path) -> None:
    store = _store_due_now(tmp_path)
    store.save(
        UpdateState(
            phase="awaiting_response",
            run_id="scheduled-run",
            scheduled_for=NOW.isoformat(),
            response_deadline=(NOW + timedelta(minutes=5)).isoformat(),
            deferrals_used=2,
        )
    )
    coordinator = UpdateCoordinator(load_policy(), store)

    action = coordinator.tick(
        NOW + timedelta(minutes=1),
        [UserRequest(run_id="scheduled-run", action="defer", hours=1)],
    )

    assert action is TickAction.WAITING_FOR_RESPONSE
    assert store.load().phase == "awaiting_response"


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


def _store_due_now(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "state.json")
    store.save(UpdateState(next_scheduled_at=NOW.isoformat()))
    return store
