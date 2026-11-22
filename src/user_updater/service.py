"""One-shot worker service driven by a frequent persistent systemd timer."""

from __future__ import annotations

import fcntl
import os
import pwd
import stat
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from .coordinator import TickAction, UpdateCoordinator
from .history import HistoryError, RunHistoryStore, history_path_for_state
from .policy import Policy, TaskRetryPolicy
from .requests import UserRequest, load_request, request_path, task_request_path
from .runner import strip_ansi
from .schedule import local_timezone
from .state import StateError, StateStore
from .workflow import WorkflowResult


@dataclass(frozen=True)
class RequestHome:
    home: Path
    uid: int


class Workflow(Protocol):
    def run(
        self,
        on_output: Callable[[str], None],
        on_idle=None,
        *,
        on_progress: Callable[[str], None] | None = None,
        on_progress_end: Callable[[], None] | None = None,
    ) -> WorkflowResult: ...


class UpdateService:
    """Advance policy state and execute an update only when the response window ends."""

    def __init__(
        self,
        policy: Policy,
        state_store: StateStore,
        workflow: Workflow,
        request_homes: Callable[[], list[RequestHome]] | None = None,
        clock: Callable[[], datetime] | None = None,
        on_log: Callable[[str], None] = print,
        lock_file: Path | None = None,
        task_id: str = "update",
        interval_hours: int | None = None,
        response_minutes: int | None = None,
        history_store: RunHistoryStore | None = None,
        retry_policy: TaskRetryPolicy | None = None,
    ) -> None:
        self.state_store = state_store
        selected_retry_policy = retry_policy or (
            policy.retry.system if task_id == "system" else policy.retry.user_tools
        )
        self.coordinator = UpdateCoordinator(
            policy,
            state_store,
            interval_hours=interval_hours,
            response_minutes=response_minutes,
            retry_policy=selected_retry_policy,
            task_id=task_id,
        )
        self.workflow = workflow
        self.request_homes = request_homes or local_request_homes
        self.clock = clock or (lambda: datetime.now(local_timezone()))
        self.on_log = on_log
        self.lock_file = Path(lock_file) if lock_file is not None else None
        self.task_id = task_id
        self.history_store = history_store or RunHistoryStore(
            history_path_for_state(self.state_store.path),
            file_mode=self.state_store.file_mode,
        )

    def tick(self, now: datetime | None = None) -> TickAction:
        return self._with_worker_lock(lambda: self._tick_locked(now))

    def retry_failed(self, now: datetime | None = None) -> TickAction:
        """Run a failed task again immediately; callers enforce authorization."""
        current = now or self.clock()
        return self._with_worker_lock(lambda: self._retry_failed_locked(current))

    def run_now(self, now: datetime | None = None) -> TickAction:
        """Start a fresh update immediately; callers enforce administrator authorization."""
        current = now or self.clock()
        return self._with_worker_lock(lambda: self._run_now_locked(current))

    def _with_worker_lock(self, operation: Callable[[], TickAction]) -> TickAction:
        if self.lock_file is None:
            return operation()
        self.lock_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.lock_file, flags, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return TickAction.WORKER_ALREADY_ACTIVE
            return operation()
        finally:
            os.close(descriptor)

    def _run_now_locked(self, now: datetime) -> TickAction:
        if self.state_store.load().phase == "running":
            action = self.coordinator.tick(now)
            if action is TickAction.RECOVERED_INTERRUPTED_RUN:
                self.on_log(self.state_store.load().message)
                self._record_history()
            return action
        task_description = (
            "machine-wide system updates" if self.task_id == "system" else "personal-tool updates"
        )
        action = self.coordinator.run_now(
            now,
            message=f"An administrator started {task_description}.",
        )
        self.on_log(f"An administrator started {task_description} immediately.")
        return self._run_workflow(action)

    def _tick_locked(self, now: datetime | None) -> TickAction:
        current = now or self.clock()
        state = self.state_store.load()
        if state.phase == "failed" and self.task_id == "user-tools":
            requests = self._read_requests(state.run_id)
            if any(
                request.action == "retry" and _request_is_recent(request, current)
                for request in requests
            ):
                self.on_log("The user requested an immediate retry of personal tool updates.")
                action = self.coordinator.retry_failed(current)
                return self._run_workflow(action)
        if state.phase == "awaiting_response":
            requests = self._read_requests(
                state.run_id,
                accept=(
                    self.task_id != "system"
                    or self.coordinator.policy.interaction.allow_system_deferral
                ),
            )
        else:
            requests = []
        action = self.coordinator.tick(current, requests)
        if action is TickAction.RECOVERED_INTERRUPTED_RUN:
            self.on_log(self.state_store.load().message)
            self._record_history()
        if action is not TickAction.RUN:
            return action

        return self._run_workflow(action)

    def _retry_failed_locked(self, now: datetime) -> TickAction:
        action = self.coordinator.retry_failed(now)
        self.on_log("An authorized manual retry was requested.")
        return self._run_workflow(action)

    def _run_workflow(self, action: TickAction) -> TickAction:
        tail: deque[str] = deque(maxlen=200)
        progress_active = False
        last_progress_write = 0.0

        def on_output(line: str) -> None:
            nonlocal progress_active
            progress_active = False
            tail.append(line)
            self.coordinator.record_output(tuple(tail))
            self.on_log(strip_ansi(line))

        def on_progress(line: str) -> None:
            nonlocal progress_active, last_progress_write
            if progress_active and tail:
                tail[-1] = line
            else:
                tail.append(line)
            progress_active = True
            now = time.monotonic()
            if now - last_progress_write >= 0.25:
                self.coordinator.record_output(tuple(tail))
                last_progress_write = now

        def on_progress_end() -> None:
            nonlocal progress_active
            if progress_active:
                progress_active = False
                self.coordinator.record_output(tuple(tail))

        def on_idle(seconds: float) -> None:
            message = f"Update is still running; no new output for {int(seconds)} seconds."
            self.coordinator.record_output(tuple(tail), message)
            self.on_log(message)

        try:
            result = self.workflow.run(
                on_output,
                on_idle,
                on_progress=on_progress,
                on_progress_end=on_progress_end,
            )
            returncode = result.returncode
        except Exception as exc:
            on_output(f"Update worker failed: {exc}")
            returncode = 1
        self.coordinator.record_output(tuple(tail))
        self.coordinator.complete(returncode, self.clock())
        self._record_history()
        return action

    def _record_history(self) -> None:
        task = "user-tools" if self.task_id == "update" else self.task_id
        try:
            self.history_store.append(task, self.state_store.load())
        except (HistoryError, OSError, StateError) as exc:
            self.on_log(f"Could not record recent run history: {exc}")

    def _read_requests(self, run_id: str | None, *, accept: bool = True) -> list[UserRequest]:
        if run_id is None:
            return []
        requests: list[UserRequest] = []
        for account in self.request_homes():
            path = (
                request_path(account.home)
                if self.task_id == "update"
                else task_request_path(account.home, self.task_id)
            )
            request = load_request(path, account.uid, run_id)
            try:
                metadata = path.lstat()
                if stat.S_ISREG(metadata.st_mode) and metadata.st_uid == account.uid:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            except OSError:
                continue
            if accept and request is not None:
                requests.append(request)
        return requests


def _request_is_recent(request: UserRequest, now: datetime) -> bool:
    if request.submitted_at is None:
        return False
    submitted = datetime.fromisoformat(request.submitted_at)
    if submitted.tzinfo is None:
        return False
    age = now - submitted.astimezone(now.tzinfo)
    return timedelta(0) <= age <= timedelta(minutes=5)


def local_request_homes() -> list[RequestHome]:
    """Return real local account homes where GUI clients may store a response."""
    homes: list[RequestHome] = []
    for account in pwd.getpwall():
        if account.pw_uid < 1000 or account.pw_uid == 65534:
            continue
        home = Path(account.pw_dir)
        if home.is_absolute() and home.is_dir():
            homes.append(RequestHome(home, account.pw_uid))
    return homes
