"""Time-bounded, per-task user rescheduling choices."""

from __future__ import annotations

from datetime import datetime, timedelta

from .policy import Policy
from .schedule import local_timezone, next_scheduled_after

_HOUR = timedelta(hours=1)


def available_reschedule_hours(
    policy: Policy,
    task_id: str,
    scheduled_for: str | None,
    now: datetime,
) -> tuple[int, ...]:
    """Return configured delays that remain strictly inside this task's time limit."""
    is_system = task_id == "system"
    interaction = policy.interaction
    if is_system:
        enabled = interaction.allow_system_deferral
        maximum_hours = interaction.max_system_deferral_hours
    else:
        enabled = interaction.allow_deferral
        maximum_hours = interaction.max_user_tools_deferral_hours
    if not enabled or not interaction.deferral_options_hours:
        return ()

    if now.tzinfo is None:
        now = now.replace(tzinfo=local_timezone())
    anchor = datetime.fromisoformat(scheduled_for) if scheduled_for else now
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=now.tzinfo)

    deadline = anchor + timedelta(hours=maximum_hours)
    if is_system:
        deadline = min(deadline, next_scheduled_after(policy, anchor))
    remaining = deadline - now
    if remaining <= timedelta(0):
        return ()

    max_selectable_hours = remaining // _HOUR
    if remaining % _HOUR == timedelta(0):
        max_selectable_hours -= 1
    if max_selectable_hours < 1:
        return ()

    configured = sorted(interaction.deferral_options_hours)
    available = [hours for hours in configured if hours <= max_selectable_hours]
    if configured[-1] > max_selectable_hours:
        available.append(max_selectable_hours)
    return tuple(sorted(set(available)))
