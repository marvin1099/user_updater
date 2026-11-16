"""Durable schedule, prompt, and deferral state machine."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from .policy import Policy, check_deferral
from .requests import UserRequest
from .schedule import next_scheduled_after
from .state import StateStore, UpdateState


class TickAction(StrEnum):
    WORKER_ALREADY_ACTIVE = "worker_already_active"
    NOT_DUE = "not_due"
    WAITING_FOR_RESPONSE = "waiting_for_response"
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
        interval_hours: int | None = None,
        response_minutes: int | None = None,
    ) -> None:
        self.policy = policy
        self.state_store = state_store
        self.interval_hours = interval_hours
        self.response_minutes = response_minutes or policy.schedule.response_minutes

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

        if state.phase == "awaiting_response":
            return self._handle_response_window(state, now, requests)

        if state.phase == "deferred":
            deferred_until = _parse_datetime(state.deferred_until, "deferred_until")
            if now < deferred_until:
                return TickAction.DEFERRED
            resumed = UpdateState(
                phase="awaiting_response",
                run_id=state.run_id,
                scheduled_for=state.scheduled_for,
                next_scheduled_at=state.next_scheduled_at,
                response_deadline=(now + timedelta(minutes=self.response_minutes)).isoformat(),
                deferrals_used=state.deferrals_used,
                message="The postponed update is due. It will start after the response window.",
                last_output=state.last_output,
            )
            self.state_store.save(resumed)
            return self._handle_response_window(resumed, now, requests)

        due_at = (
            _parse_datetime(state.next_scheduled_at, "next_scheduled_at")
            if state.next_scheduled_at
            else (
                now if self.interval_hours is not None else next_scheduled_after(self.policy, now)
            )
        )
        if now < due_at:
            if state.next_scheduled_at is None:
                self.state_store.save(UpdateState(next_scheduled_at=due_at.isoformat()))
            return TickAction.NOT_DUE

        pending = UpdateState(
            phase="awaiting_response",
            run_id=uuid4().hex,
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
        self.state_store.save(
            UpdateState(
                phase="succeeded" if success else "failed",
                run_id=state.run_id,
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
            if request.run_id == state.run_id and _request_is_timely(request, deadline, now)
        ]
        deferral = next(
            (
                request
                for request in matching
                if request.action == "defer"
                and request.hours is not None
                and check_deferral(self.policy, request.hours, state.deferrals_used)
            ),
            None,
        )
        if deferral is not None:
            deferred_until = now + timedelta(hours=deferral.hours or 0)
            self.state_store.save(
                UpdateState(
                    phase="deferred",
                    run_id=state.run_id,
                    scheduled_for=state.scheduled_for,
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

        if any(request.action == "start_now" for request in matching):
            return self._start(state, now)

        if now < deadline:
            return TickAction.WAITING_FOR_RESPONSE
        return self._start(state, now)

    def _start(self, state: UpdateState, now: datetime) -> TickAction:
        self.state_store.save(
            UpdateState(
                phase="running",
                run_id=state.run_id,
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
        if self.interval_hours is not None:
            return now + timedelta(hours=self.interval_hours)
        return next_scheduled_after(self.policy, now)


def _parse_datetime(value: str | None, name: str) -> datetime:
    if value is None:
        raise ValueError(f"state is missing {name}")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"state {name} must include a timezone")
    return parsed


def _request_is_timely(request: UserRequest, deadline: datetime, now: datetime) -> bool:
    if request.submitted_at is None:
        return now <= deadline
    submitted_at = datetime.fromisoformat(request.submitted_at)
    if submitted_at.tzinfo is None:
        return False
    return submitted_at <= deadline
