"""Durable schedule, prompt, and deferral state machine."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from enum import StrEnum
from uuid import uuid4

from .policy import Policy, TaskRetryPolicy
from .requests import UserRequest
from .rescheduling import available_reschedule_hours
from .schedule import local_timezone, next_scheduled_after
from .state import StateStore, UpdateState


class TickAction(StrEnum):
    WORKER_ALREADY_ACTIVE = "worker_already_active"
    NOT_DUE = "not_due"
    WAITING_FOR_RESPONSE = "waiting_for_response"
    WAITING_FOR_INTERNET = "waiting_for_internet"
    DEFERRED = "deferred"
    RUN = "run"
    RECOVERED_INTERRUPTED_RUN = "recovered_interrupted_run"


class UpdateCoordinator:
    """Advance one persisted update cycle without executing package commands."""

    def __init__(
        self,
        policy: Policy,
        state_store: StateStore,
        *,
        response_minutes: int | None = None,
        retry_policy: TaskRetryPolicy | None = None,
        task_id: str = "user-tools",
    ) -> None:
        self.policy = policy
        self.state_store = state_store
        self.response_minutes = response_minutes or policy.schedule.response_minutes
        self.retry_policy = retry_policy or policy.retry.user_tools
        self.task_id = task_id

    def tick(
        self,
        now: datetime,
        requests: list[UserRequest] | None = None,
    ) -> TickAction:
        state = self.state_store.load()
        requests = requests or []

        if state.phase == "running":
            self.state_store.save(
                UpdateState(
                    phase="failed",
                    run_id=state.run_id,
                    retry_group_id=state.retry_group_id or state.run_id,
                    automatic_retries_used=state.automatic_retries_used,
                    manual_retries_used=state.manual_retries_used,
                    scheduled_for=state.scheduled_for,
                    next_scheduled_at=self._next_scheduled_at(now).isoformat(),
                    deferrals_used=state.deferrals_used,
                    started_at=state.started_at,
                    finished_at=now.isoformat(),
                    message=(
                        "The previous update run was interrupted while running. "
                        "It may have made partial changes; no immediate retry was started. "
                        "Review the output before the next scheduled run."
                    ),
                    last_output=state.last_output,
                )
            )
            return TickAction.RECOVERED_INTERRUPTED_RUN

        if state.phase == "failed":
            if (
                state.retry_after is not None
                and state.automatic_retries_used < self.retry_policy.automatic_attempts
            ):
                retry_at = _parse_datetime(state.retry_after, "retry_after")
                if now < retry_at:
                    return TickAction.NOT_DUE
                return self.retry_failed(now, automatic=True)
            if state.retry_after is not None:
                state = UpdateState(**{**state.__dict__, "retry_after": None})
                self.state_store.save(state)

        if state.phase == "awaiting_response":
            return self._handle_response_window(state, now, requests)

        if state.phase == "deferred":
            deferred_until = _parse_datetime(state.deferred_until, "deferred_until")
            if now < deferred_until:
                return TickAction.DEFERRED
            resumed = UpdateState(
                phase="awaiting_response",
                run_id=state.run_id,
                retry_group_id=state.retry_group_id or state.run_id,
                automatic_retries_used=state.automatic_retries_used,
                manual_retries_used=state.manual_retries_used,
                scheduled_for=state.scheduled_for,
                next_scheduled_at=state.next_scheduled_at,
                response_deadline=(now + timedelta(minutes=self.response_minutes)).isoformat(),
                deferrals_used=state.deferrals_used,
                message="The postponed update is due. It will start after the response window.",
                last_output=state.last_output,
            )
            self.state_store.save(resumed)
            return self._handle_response_window(resumed, now, requests)

        if (
            self.task_id != "system"
            and state.phase in {"succeeded", "failed"}
            and state.finished_at is not None
        ):
            finished_at = _parse_datetime(state.finished_at, "finished_at")
            if now.tzinfo is not None:
                finished_at = finished_at.astimezone(now.tzinfo)
            next_scheduled_at = self._next_scheduled_at(finished_at).isoformat()
            if state.next_scheduled_at != next_scheduled_at:
                state = UpdateState(
                    **{**state.__dict__, "next_scheduled_at": next_scheduled_at}
                )
                self.state_store.save(state)

        due_at = (
            _parse_datetime(state.next_scheduled_at, "next_scheduled_at")
            if state.next_scheduled_at
            else (
                now
                if self.task_id != "system"
                else next_scheduled_after(self.policy, now, self.task_id)
            )
        )
        if now < due_at:
            if state.next_scheduled_at is None:
                self.state_store.save(UpdateState(next_scheduled_at=due_at.isoformat()))
            return TickAction.NOT_DUE

        new_run_id = uuid4().hex
        pending = UpdateState(
            phase="awaiting_response",
            run_id=new_run_id,
            retry_group_id=new_run_id,
            scheduled_for=due_at.isoformat(),
            next_scheduled_at=due_at.isoformat(),
            response_deadline=(now + timedelta(minutes=self.response_minutes)).isoformat(),
            message=(
                "An update is scheduled. Choose a permitted reschedule or "
                "let it begin automatically."
            ),
        )
        self.state_store.save(pending)
        return TickAction.WAITING_FOR_RESPONSE

    def prompt_is_due(self, now: datetime) -> bool:
        """Return whether this tick would create or resume a user response prompt."""
        state = self.state_store.load()
        if state.phase in {"running", "awaiting_response"}:
            return False
        if state.phase == "failed" and (
            state.retry_after is not None
            and state.automatic_retries_used < self.retry_policy.automatic_attempts
        ):
            # A due automatic retry starts work directly, without a new prompt.
            return False
        if state.phase == "deferred":
            return now >= _parse_datetime(state.deferred_until, "deferred_until")

        if (
            self.task_id != "system"
            and state.phase in {"succeeded", "failed"}
            and state.finished_at is not None
        ):
            finished_at = _parse_datetime(state.finished_at, "finished_at")
            if now.tzinfo is not None:
                finished_at = finished_at.astimezone(now.tzinfo)
            due_at = self._next_scheduled_at(finished_at)
        else:
            due_at = (
                _parse_datetime(state.next_scheduled_at, "next_scheduled_at")
                if state.next_scheduled_at
                else (
                    now
                    if self.task_id != "system"
                    else next_scheduled_after(self.policy, now, self.task_id)
                )
            )
        return now >= due_at

    def run_now(
        self,
        now: datetime,
        *,
        message: str = "An administrator started machine-wide updates.",
    ) -> TickAction:
        """Create a fresh run that bypasses its normal schedule and response window."""
        state = self.state_store.load()
        if state.phase == "running":
            raise ValueError("an update is already marked as running")
        run_id = uuid4().hex
        self.state_store.save(
            UpdateState(
                phase="running",
                run_id=run_id,
                retry_group_id=run_id,
                scheduled_for=now.isoformat(),
                next_scheduled_at=state.next_scheduled_at,
                started_at=now.isoformat(),
                message=message,
            )
        )
        return TickAction.RUN

    def schedule_prompt_now(
        self,
        now: datetime,
        *,
        message: str = (
            "An administrator scheduled an update. It will start after the response window."
        ),
    ) -> TickAction:
        """Schedule a fresh due prompt, replacing a prompt or deferral but not a running task."""
        state = self.state_store.load()
        if state.phase == "running":
            raise ValueError(f"cannot schedule another update while this task is {state.phase}")

        run_id = uuid4().hex
        self.state_store.save(
            UpdateState(
                phase="awaiting_response",
                run_id=run_id,
                retry_group_id=run_id,
                scheduled_for=now.isoformat(),
                next_scheduled_at=now.isoformat(),
                response_deadline=(now + timedelta(minutes=self.response_minutes)).isoformat(),
                message=message,
            )
        )
        return TickAction.WAITING_FOR_RESPONSE

    def record_output(self, lines: tuple[str, ...], message: str | None = None) -> None:
        state = self.state_store.load()
        self.state_store.save(
            UpdateState(
                **{
                    **state.__dict__,
                    "last_output": lines[-200:],
                    "message": message if message is not None else state.message,
                }
            )
        )

    def complete(self, returncode: int, now: datetime, message: str | None = None) -> None:
        state = self.state_store.load()
        success = returncode == 0
        retry_after = None
        if not success and state.automatic_retries_used < self.retry_policy.automatic_attempts:
            retry_after = (now + timedelta(minutes=self.retry_policy.delay_minutes)).isoformat()
        self.state_store.save(
            UpdateState(
                phase="succeeded" if success else "failed",
                run_id=state.run_id,
                retry_group_id=state.retry_group_id or state.run_id,
                automatic_retries_used=state.automatic_retries_used,
                manual_retries_used=state.manual_retries_used,
                retry_after=retry_after,
                scheduled_for=state.scheduled_for,
                next_scheduled_at=self._next_scheduled_at(now).isoformat(),
                deferrals_used=state.deferrals_used,
                started_at=state.started_at,
                finished_at=now.isoformat(),
                returncode=returncode,
                message=message
                or ("Updates completed." if success else "One or more updates failed."),
                last_output=state.last_output,
            )
        )

    def retry_failed(self, now: datetime, *, automatic: bool = False) -> TickAction:
        """Start a bounded automatic retry or user-requested retry."""
        state = self.state_store.load()
        if state.phase != "failed":
            raise ValueError("only a failed update can be retried")
        if automatic:
            if state.automatic_retries_used >= self.retry_policy.automatic_attempts:
                raise ValueError("automatic retry limit has been reached")
            if state.retry_after is None or now < _parse_datetime(state.retry_after, "retry_after"):
                raise ValueError("automatic retry is not due yet")
        elif state.manual_retries_used >= self.retry_policy.user_manual_attempts:
            raise ValueError("manual retry limit has been reached")
        self.state_store.save(
            UpdateState(
                phase="running",
                run_id=uuid4().hex,
                retry_group_id=state.retry_group_id or state.run_id,
                automatic_retries_used=(
                    state.automatic_retries_used + 1 if automatic else state.automatic_retries_used
                ),
                manual_retries_used=(
                    state.manual_retries_used if automatic else state.manual_retries_used + 1
                ),
                retry_after=None,
                scheduled_for=now.isoformat(),
                next_scheduled_at=state.next_scheduled_at,
                started_at=now.isoformat(),
                message=(
                    "An automatic retry is running."
                    if automatic
                    else "A manually requested retry is running."
                ),
            )
        )
        return TickAction.RUN

    def _handle_response_window(
        self,
        state: UpdateState,
        now: datetime,
        requests: list[UserRequest],
    ) -> TickAction:
        deadline = _parse_datetime(state.response_deadline, "response_deadline")
        matching = [
            request
            for request in requests
            if request.run_id == state.run_id and now <= deadline
        ]
        deferral = next(
            (
                request
                for request in matching
                if request.action == "defer"
                and request.hours is not None
                and not isinstance(request.hours, bool)
                and request.hours
                in available_reschedule_hours(
                    self.policy,
                    self.task_id,
                    state.scheduled_for or state.next_scheduled_at,
                    now,
                )
            ),
            None,
        )
        if deferral is not None:
            deferred_until = now + timedelta(hours=deferral.hours or 0)
            self.state_store.save(
                UpdateState(
                    phase="deferred",
                    run_id=state.run_id,
                    retry_group_id=state.retry_group_id or state.run_id,
                    automatic_retries_used=state.automatic_retries_used,
                    manual_retries_used=state.manual_retries_used,
                    scheduled_for=state.scheduled_for or state.next_scheduled_at or now.isoformat(),
                    next_scheduled_at=state.next_scheduled_at,
                    deferred_until=deferred_until.isoformat(),
                    deferrals_used=state.deferrals_used + 1,
                    message=(
                        "A user rescheduled this update until "
                        f"{deferred_until.astimezone().strftime('%Y-%m-%d %H:%M %Z')}."
                    ),
                    last_output=state.last_output,
                )
            )
            return TickAction.DEFERRED

        if self.task_id != "system" and any(request.action == "start_now" for request in matching):
            return self._start(state, now)

        if now < deadline:
            return TickAction.WAITING_FOR_RESPONSE
        return self._start(state, now)

    def _start(self, state: UpdateState, now: datetime) -> TickAction:
        self.state_store.save(
            UpdateState(
                phase="running",
                run_id=state.run_id,
                retry_group_id=state.retry_group_id or state.run_id,
                automatic_retries_used=state.automatic_retries_used,
                manual_retries_used=state.manual_retries_used,
                scheduled_for=state.scheduled_for,
                next_scheduled_at=state.next_scheduled_at,
                deferrals_used=state.deferrals_used,
                started_at=now.isoformat(),
                message="Updates are running. Do not power off the computer.",
                last_output=state.last_output,
            )
        )
        return TickAction.RUN

    def _next_scheduled_at(self, now: datetime) -> datetime:
        after = now
        if self.task_id != "system":
            if after.tzinfo is None:
                after = after.replace(tzinfo=local_timezone())
            # Catching up after a missed run should not trigger a second run
            # at a later scheduled time on that same local calendar day.
            after = datetime.combine(after.date(), time.max, tzinfo=after.tzinfo)
        return next_scheduled_after(self.policy, after, self.task_id)


def _parse_datetime(value: str | None, name: str) -> datetime:
    if value is None:
        raise ValueError(f"state is missing {name}")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"state {name} must include a timezone")
    return parsed
