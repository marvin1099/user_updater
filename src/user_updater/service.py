"""One-shot worker service driven by a frequent persistent systemd timer."""

from __future__ import annotations

import fcntl
import os
import pwd
import stat
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .coordinator import TickAction, UpdateCoordinator
from .policy import Policy
from .requests import UserRequest, load_request, request_path
from .schedule import local_timezone
from .state import StateStore
from .workflow import WorkflowResult


@dataclass(frozen=True)
class RequestHome:
    home: Path
    uid: int


class Workflow(Protocol):
    def run(self, on_output: Callable[[str], None], on_idle=None) -> WorkflowResult: ...


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
    ) -> None:
        self.state_store = state_store
        self.coordinator = UpdateCoordinator(policy, state_store)
        self.workflow = workflow
        self.request_homes = request_homes or local_request_homes
        self.clock = clock or (lambda: datetime.now(local_timezone()))
        self.on_log = on_log
        self.lock_file = Path(lock_file) if lock_file is not None else None

    def tick(self, now: datetime | None = None) -> TickAction:
        if self.lock_file is None:
            return self._tick_locked(now)

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
            return self._tick_locked(now)
        finally:
            os.close(descriptor)

    def _tick_locked(self, now: datetime | None) -> TickAction:
        current = now or self.clock()
        state = self.state_store.load()
        requests = self._read_requests(state.run_id) if state.phase == "awaiting_response" else []
        action = self.coordinator.tick(current, requests)
        if action is not TickAction.RUN:
            return action

        tail: deque[str] = deque(maxlen=200)

        def on_output(line: str) -> None:
            tail.append(line)
            self.coordinator.record_output(tuple(tail))
            self.on_log(line)

        def on_idle(seconds: float) -> None:
            message = f"Update is still running; no new output for {int(seconds)} seconds."
            self.coordinator.record_output(tuple(tail), message)
            self.on_log(message)

        try:
            result = self.workflow.run(on_output, on_idle)
            returncode = result.returncode
        except Exception as exc:
            on_output(f"Update worker failed: {exc}")
            returncode = 1
        self.coordinator.complete(returncode, self.clock())
        return action

    def _read_requests(self, run_id: str | None) -> list[UserRequest]:
        if run_id is None:
            return []
        requests: list[UserRequest] = []
        for account in self.request_homes():
            path = request_path(account.home)
            request = load_request(path, account.uid, run_id)
            try:
                metadata = path.lstat()
                if stat.S_ISREG(metadata.st_mode) and metadata.st_uid == account.uid:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            except OSError:
                continue
            if request is not None:
                requests.append(request)
        return requests


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
