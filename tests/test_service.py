import fcntl
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from user_updater.history import RunHistoryStore
from user_updater.policy import (
    TaskRetryPolicy,
    load_policy,
    policy_to_mapping,
    validate_policy_data,
)
from user_updater.requests import (
    UserRequest,
    legacy_task_request_path,
    request_path,
    save_request,
    task_request_path,
)
from user_updater.service import RequestHome, UpdateService
from user_updater.state import StateStore, UpdateState
from user_updater.workflow import WorkflowResult

NOW = datetime(2026, 11, 15, 3, 0, tzinfo=ZoneInfo("UTC"))


class FakeWorkflow:
    def __init__(self) -> None:
        self.calls = 0
        self.progress_messages: tuple[str, ...] = ()
        self.progress_batches: tuple[tuple[str, ...], ...] = ()

    def run(
        self,
        on_output,
        _on_idle=None,
        *,
        on_progress=None,
        on_progress_end=None,
    ) -> WorkflowResult:
        self.calls += 1
        if on_progress is not None:
            for line in self.progress_messages:
                on_progress(line)
            for batch in self.progress_batches:
                for line in batch:
                    on_progress(line)
                if on_progress_end is not None:
                    on_progress_end()
        on_output("fake topgrade completed")
        return WorkflowResult(0, None, ())


def test_service_reads_valid_user_deferral_then_runs_after_response_window(
    tmp_path: Path,
) -> None:
    policy_file = tmp_path / "project.yaml"
    policy_file.write_text(
        "version: 1\nupdates:\n  run_system: false\n  run_user_tools: false\n",
        encoding="utf-8",
    )
    policy = load_policy(policy_file)
    state_store = StateStore(tmp_path / "state.json")
    state_store.save(UpdateState(next_scheduled_at=NOW.isoformat()))
    user_home = tmp_path / "alice"
    user_home.mkdir()
    workflow = FakeWorkflow()
    service = UpdateService(
        policy,
        state_store,
        workflow,
        request_homes=lambda: [RequestHome(user_home, os.getuid())],
    )

    service.tick(NOW)
    run_id = state_store.load().run_id
    save_request(
        request_path(user_home),
        UserRequest(
            run_id or "",
            "defer",
            1,
            submitted_at=(NOW + timedelta(minutes=1)).isoformat(),
        ),
    )
    service.tick(NOW + timedelta(minutes=1))

    assert state_store.load().phase == "deferred"
    assert workflow.calls == 0

    service.tick(NOW + timedelta(hours=1, minutes=1))
    assert state_store.load().phase == "awaiting_response"
    service.tick(NOW + timedelta(hours=1, minutes=6))

    assert workflow.calls == 1
    assert state_store.load().phase == "succeeded"
    assert "fake topgrade completed" in state_store.load().last_output


def test_system_service_reads_user_reschedule_requests(tmp_path: Path) -> None:
    policy = load_policy()
    state_store = StateStore(tmp_path / "system-state.json")
    state_store.save(UpdateState(next_scheduled_at=NOW.isoformat()))
    user_home = tmp_path / "alice"
    user_home.mkdir()
    service = UpdateService(
        policy,
        state_store,
        FakeWorkflow(),
        request_homes=lambda: [RequestHome(user_home, os.getuid())],
        task_id="system",
    )

    service.tick(NOW)
    run_id = state_store.load().run_id
    save_request(
        task_request_path(user_home, "system"),
        UserRequest(
            run_id or "",
            "defer",
            1,
            submitted_at=(NOW + timedelta(minutes=1)).isoformat(),
        ),
    )

    service.tick(NOW + timedelta(minutes=1))

    assert state_store.load().phase == "deferred"
    assert state_store.load().deferred_until == (NOW + timedelta(hours=1, minutes=1)).isoformat()


def test_system_service_reads_legacy_underscore_request_path(tmp_path: Path) -> None:
    policy = load_policy()
    state_store = StateStore(tmp_path / "system-state.json")
    state_store.save(UpdateState(next_scheduled_at=NOW.isoformat()))
    user_home = tmp_path / "alice"
    user_home.mkdir()
    service = UpdateService(
        policy,
        state_store,
        FakeWorkflow(),
        request_homes=lambda: [RequestHome(user_home, os.getuid())],
        task_id="system",
    )

    service.tick(NOW)
    run_id = state_store.load().run_id
    save_request(
        legacy_task_request_path(user_home, "system"),
        UserRequest(
            run_id or "",
            "defer",
            1,
            submitted_at=(NOW + timedelta(minutes=1)).isoformat(),
        ),
    )

    service.tick(NOW + timedelta(minutes=1))

    assert state_store.load().phase == "deferred"
    assert state_store.load().deferred_until == (NOW + timedelta(hours=1, minutes=1)).isoformat()


def test_worker_lock_prevents_a_second_manual_or_scheduled_run(tmp_path: Path) -> None:
    state_store = StateStore(tmp_path / "state.json")
    workflow = FakeWorkflow()
    lock_file = tmp_path / "worker.lock"
    service = UpdateService(
        load_policy(),
        state_store,
        workflow,
        lock_file=lock_file,
    )
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("w", encoding="utf-8") as lock_stream:
        fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)

        assert service.tick(NOW).value == "worker_already_active"

    assert workflow.calls == 0


def test_service_recovers_interrupted_run_and_does_not_replay_it(tmp_path: Path) -> None:
    state_store = StateStore(tmp_path / "state.json")
    state_store.save(
        UpdateState(
            phase="running",
            run_id="crashed-run",
            scheduled_for=(NOW - timedelta(minutes=10)).isoformat(),
            started_at=(NOW - timedelta(minutes=5)).isoformat(),
            last_output=("Updating package database",),
        )
    )
    workflow = FakeWorkflow()
    messages: list[str] = []
    service = UpdateService(
        load_policy(),
        state_store,
        workflow,
        on_log=messages.append,
        lock_file=tmp_path / "locks" / "worker.lock",
    )

    action = service.tick(NOW)

    assert action.value == "recovered_interrupted_run"
    assert state_store.load().phase == "failed"
    assert state_store.load().run_id == "crashed-run"
    assert any("interrupted while running" in message for message in messages)
    assert workflow.calls == 0
    recovered_history = RunHistoryStore(tmp_path / "state.history.json").load()
    assert len(recovered_history) == 1
    assert recovered_history[0].phase == "failed"
    assert recovered_history[0].run_id == "crashed-run"

    next_action = service.tick(NOW + timedelta(minutes=1))

    assert next_action.value == "not_due"
    assert workflow.calls == 0


def test_service_can_keep_user_command_output_out_of_shared_logs(tmp_path: Path) -> None:
    state_store = StateStore(tmp_path / "state.json")
    state_store.save(
        UpdateState(
            phase="awaiting_response",
            run_id="user-run",
            scheduled_for=NOW.isoformat(),
            response_deadline=NOW.isoformat(),
        )
    )
    messages: list[str] = []
    private_output: list[str] = []
    service = UpdateService(
        load_policy(),
        state_store,
        FakeWorkflow(),
        on_log=messages.append,
        log_output=False,
        on_output_log=private_output.append,
    )

    service.tick(NOW + timedelta(seconds=1))

    assert "fake topgrade completed" in state_store.load().last_output
    assert "fake topgrade completed" not in "\n".join(messages)
    assert private_output == ["fake topgrade completed"]


def test_manual_retry_starts_a_new_run_for_a_failed_task(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.save(
        UpdateState(
            phase="failed",
            run_id="failed-run",
            scheduled_for=(NOW - timedelta(days=1)).isoformat(),
            next_scheduled_at=(NOW + timedelta(days=1)).isoformat(),
            started_at=(NOW - timedelta(days=1)).isoformat(),
            finished_at=(NOW - timedelta(days=1) + timedelta(minutes=2)).isoformat(),
            returncode=1,
            message="Package install failed.",
            last_output=("old failure output",),
        )
    )
    workflow = FakeWorkflow()
    service = UpdateService(
        load_policy(),
        store,
        workflow,
        clock=lambda: NOW,
        interval_hours=24,
        lock_file=tmp_path / "locks" / "retry.lock",
        retry_policy=TaskRetryPolicy(
            automatic_attempts=2, delay_minutes=15, user_manual_attempts=1
        ),
    )

    action = service.retry_failed(NOW)

    retried = store.load()
    assert action.value == "run"
    assert workflow.calls == 1
    assert retried.phase == "succeeded"
    assert retried.run_id != "failed-run"
    assert retried.started_at == NOW.isoformat()
    assert retried.next_scheduled_at == (NOW + timedelta(hours=24)).isoformat()
    assert retried.last_output == ("fake topgrade completed",)


def test_run_now_bypasses_schedule_and_replaces_progress_in_the_saved_output(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "system.json", file_mode=0o644)
    store.save(
        UpdateState(
            phase="awaiting_response",
            run_id="scheduled-run",
            next_scheduled_at=(NOW + timedelta(days=1)).isoformat(),
        )
    )
    workflow = FakeWorkflow()
    workflow.progress_messages = ("Flatpak update 5%", "Flatpak update 100%")
    service = UpdateService(
        load_policy(),
        store,
        workflow,
        task_id="system",
        clock=lambda: NOW,
        lock_file=tmp_path / "locks" / "system.lock",
    )

    action = service.run_now(NOW)

    state = store.load()
    assert action.value == "run"
    assert workflow.calls == 1
    assert state.phase == "succeeded"
    assert state.run_id != "scheduled-run"
    assert state.last_output == ("Flatpak update 100%", "fake topgrade completed")


def test_service_keeps_distinct_completed_progress_lines(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "system.json", file_mode=0o644)
    workflow = FakeWorkflow()
    workflow.progress_batches = (("package A 100%",), ("package B 100%",))
    service = UpdateService(
        load_policy(),
        store,
        workflow,
        task_id="system",
        clock=lambda: NOW,
    )

    service.run_now(NOW)

    assert store.load().last_output == (
        "package A 100%",
        "package B 100%",
        "fake topgrade completed",
    )


def test_admin_user_tools_run_now_logs_the_correct_task(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "user.json")
    logs: list[str] = []
    service = UpdateService(
        load_policy(),
        store,
        FakeWorkflow(),
        task_id="user-tools",
        clock=lambda: NOW,
        on_log=logs.append,
    )

    service.run_now(NOW)

    assert logs[0] == "An administrator started personal-tool updates immediately."


def test_scheduled_prompt_waits_for_response_before_running_updates(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "system.json", file_mode=0o644)
    workflow = FakeWorkflow()
    service = UpdateService(
        load_policy(),
        store,
        workflow,
        task_id="system",
        response_minutes=2,
        clock=lambda: NOW + timedelta(minutes=2),
    )

    action = service.schedule_prompt_now(NOW)

    scheduled = store.load()
    assert action.value == "waiting_for_response"
    assert scheduled.phase == "awaiting_response"
    assert scheduled.scheduled_for == NOW.isoformat()
    assert scheduled.response_deadline == (NOW + timedelta(minutes=2)).isoformat()
    assert workflow.calls == 0

    assert service.tick(NOW + timedelta(minutes=1)).value == "waiting_for_response"
    assert workflow.calls == 0
    assert service.tick(NOW + timedelta(minutes=2)).value == "run"
    assert workflow.calls == 1
    assert store.load().phase == "succeeded"


def test_scheduled_prompt_replaces_a_deferred_task_with_a_fresh_response_window(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "system.json")
    store.save(
        UpdateState(
            phase="deferred",
            run_id="existing",
            scheduled_for=(NOW - timedelta(hours=1)).isoformat(),
            deferred_until=(NOW + timedelta(hours=2)).isoformat(),
        )
    )
    workflow = FakeWorkflow()
    service = UpdateService(
        load_policy(),
        store,
        workflow,
        task_id="system",
        response_minutes=2,
        clock=lambda: NOW,
    )

    action = service.schedule_prompt_now(NOW)

    scheduled = store.load()
    assert action.value == "waiting_for_response"
    assert scheduled.phase == "awaiting_response"
    assert scheduled.run_id != "existing"
    assert scheduled.scheduled_for == NOW.isoformat()
    assert scheduled.response_deadline == (NOW + timedelta(minutes=2)).isoformat()
    assert workflow.calls == 0


def test_scheduled_prompt_does_not_replace_a_running_task(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "system.json")
    store.save(UpdateState(phase="running", run_id="running-task"))
    workflow = FakeWorkflow()
    service = UpdateService(load_policy(), store, workflow, task_id="system")

    with pytest.raises(ValueError, match="cannot schedule another update"):
        service.schedule_prompt_now(NOW)

    assert store.load().run_id == "running-task"
    assert workflow.calls == 0


def test_manual_retry_is_refused_unless_the_latest_state_failed(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.save(UpdateState(phase="running", run_id="live-run"))
    workflow = FakeWorkflow()
    service = UpdateService(load_policy(), store, workflow)

    with pytest.raises(ValueError, match="failed update"):
        service.retry_failed(NOW)

    assert store.load().run_id == "live-run"
    assert workflow.calls == 0


def test_system_retry_keeps_a_bounded_read_only_output_preview_in_shared_state(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.json", file_mode=0o644)
    store.save(UpdateState(phase="failed", run_id="old-system-run", returncode=1))
    messages: list[str] = []
    service = UpdateService(
        load_policy(),
        store,
        FakeWorkflow(),
        on_log=messages.append,
        task_id="system",
        retry_policy=TaskRetryPolicy(2, 15, 1),
    )

    service.retry_failed(NOW)

    assert store.load().phase == "succeeded"
    assert store.load().last_output == ("fake topgrade completed",)
    assert "fake topgrade completed" in messages
    assert RunHistoryStore(tmp_path / "state.history.json").load()[0].phase == "succeeded"


def test_system_state_keeps_bounded_output_available_to_the_read_only_client(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.json", file_mode=0o644)
    store.save(
        UpdateState(
            phase="failed",
            run_id="legacy-system-run",
            next_scheduled_at=(NOW + timedelta(days=1)).isoformat(),
            last_output=("legacy private system output",),
        )
    )
    service = UpdateService(load_policy(), store, FakeWorkflow(), task_id="system")

    action = service.tick(NOW)

    assert action.value == "not_due"
    assert store.load().last_output == ("legacy private system output",)


def test_automatic_retry_waits_for_configured_delay_and_runs_without_user_action(
    tmp_path: Path,
) -> None:
    retry_at = NOW + timedelta(minutes=15)
    store = StateStore(tmp_path / "system.json", file_mode=0o644)
    store.save(
        UpdateState(
            phase="failed",
            run_id="attempt-0",
            retry_group_id="scheduled-run",
            next_scheduled_at=(NOW + timedelta(days=2)).isoformat(),
            retry_after=retry_at.isoformat(),
            returncode=1,
        )
    )
    workflow = FakeWorkflow()
    service = UpdateService(
        load_policy(),
        store,
        workflow,
        clock=lambda: retry_at,
        task_id="system",
        retry_policy=TaskRetryPolicy(
            automatic_attempts=1, delay_minutes=15, user_manual_attempts=0
        ),
    )

    assert service.tick(NOW).value == "not_due"
    assert workflow.calls == 0
    action = service.tick(retry_at)

    retried = store.load()
    assert action.value == "run"
    assert workflow.calls == 1
    assert retried.phase == "succeeded"
    assert retried.automatic_retries_used == 1
    assert retried.manual_retries_used == 0
    assert retried.retry_group_id == "scheduled-run"


def test_only_the_owning_user_can_request_a_retry_of_personal_tools(tmp_path: Path) -> None:
    home = tmp_path / "alice"
    home.mkdir()
    request_file = task_request_path(home, "user-tools")
    store = StateStore(tmp_path / "state.json")
    store.save(
        UpdateState(
            phase="failed",
            run_id="failed-user-run",
            next_scheduled_at=(NOW + timedelta(days=1)).isoformat(),
            returncode=1,
        )
    )
    save_request(
        request_file,
        UserRequest("failed-user-run", "retry", submitted_at=NOW.isoformat()),
    )
    workflow = FakeWorkflow()
    service = UpdateService(
        load_policy(),
        store,
        workflow,
        request_homes=lambda: [RequestHome(home, os.getuid())],
        task_id="user-tools",
        interval_hours=24,
        lock_file=tmp_path / "locks" / "user-tools.lock",
        retry_policy=TaskRetryPolicy(
            automatic_attempts=2, delay_minutes=15, user_manual_attempts=1
        ),
    )

    action = service.tick(NOW)

    assert action.value == "run"
    assert store.load().phase == "succeeded"
    assert workflow.calls == 1
    assert not request_file.exists()


def test_user_retry_request_cannot_start_a_system_update(tmp_path: Path) -> None:
    home = tmp_path / "alice"
    home.mkdir()
    request_file = task_request_path(home, "system")
    store = StateStore(tmp_path / "state.json")
    store.save(
        UpdateState(
            phase="failed",
            run_id="failed-system-run",
            next_scheduled_at=(NOW + timedelta(days=1)).isoformat(),
            returncode=1,
        )
    )
    save_request(
        request_file,
        UserRequest("failed-system-run", "retry", submitted_at=NOW.isoformat()),
    )
    workflow = FakeWorkflow()
    service = UpdateService(
        load_policy(),
        store,
        workflow,
        request_homes=lambda: [RequestHome(home, os.getuid())],
        task_id="system",
        lock_file=tmp_path / "locks" / "system.lock",
    )

    action = service.tick(NOW)

    assert action.value == "not_due"
    assert workflow.calls == 0
    assert store.load().phase == "failed"


def test_service_records_a_run_summary_after_completion(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json", file_mode=0o600)
    store.save(UpdateState(next_scheduled_at=NOW.isoformat()))
    service = UpdateService(
        load_policy(),
        store,
        FakeWorkflow(),
        request_homes=lambda: [],
        clock=lambda: NOW,
        task_id="user-tools",
        interval_hours=24,
        response_minutes=1,
        lock_file=tmp_path / "locks" / "history.lock",
    )

    service.tick(NOW)
    service.tick(NOW + timedelta(minutes=1))

    records = RunHistoryStore(tmp_path / "state.history.json").load()
    assert len(records) == 1
    assert records[0].task == "user-tools"
    assert records[0].phase == "succeeded"
    assert records[0].message == "Updates completed."


def test_system_task_ignores_reschedules_when_disabled_by_policy(tmp_path: Path) -> None:
    raw_policy = policy_to_mapping(load_policy())
    raw_policy["interaction"]["allow_system_deferral"] = False
    policy = validate_policy_data(raw_policy)
    user_home = tmp_path / "alice"
    user_home.mkdir()
    state_store = StateStore(tmp_path / "system-state.json")
    run_id = "system-run"
    deadline = NOW + timedelta(minutes=5)
    state_store.save(
        UpdateState(
            phase="awaiting_response",
            run_id=run_id,
            response_deadline=deadline.isoformat(),
        )
    )
    save_request(
        task_request_path(user_home, "system"),
        UserRequest(
            run_id,
            "defer",
            1,
            submitted_at=(NOW + timedelta(minutes=1)).isoformat(),
        ),
    )
    service = UpdateService(
        policy,
        state_store,
        FakeWorkflow(),
        request_homes=lambda: [RequestHome(user_home, os.getuid())],
        task_id="system",
    )

    action = service.tick(NOW + timedelta(minutes=1))

    assert action.value == "waiting_for_response"
    assert state_store.load().phase == "awaiting_response"
    assert not task_request_path(user_home, "system").exists()
