import fcntl
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from user_updater.policy import load_policy
from user_updater.requests import UserRequest, request_path, save_request, task_request_path
from user_updater.service import RequestHome, UpdateService
from user_updater.state import StateStore, UpdateState
from user_updater.workflow import WorkflowResult

NOW = datetime(2026, 11, 15, 3, 0, tzinfo=ZoneInfo("UTC"))


class FakeWorkflow:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, on_output, _on_idle=None) -> WorkflowResult:
        self.calls += 1
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


def test_system_task_reads_only_its_own_response_file(tmp_path: Path) -> None:
    policy = load_policy()
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

    assert action.value == "deferred"
    assert state_store.load().phase == "deferred"
