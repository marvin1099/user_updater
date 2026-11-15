"""Calendar schedule calculations using the host's local timezone."""

from __future__ import annotations

from datetime import datetime, time, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .policy import Policy

_DAY_INDEX = {
    name: index for index, name in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))
}


def local_timezone() -> tzinfo:
    """Load the system's transition-aware local timezone from /etc/localtime."""
    zoneinfo_root = Path("/usr/share/zoneinfo")
    try:
        localtime = Path("/etc/localtime").resolve(strict=True)
        key = localtime.relative_to(zoneinfo_root.resolve()).as_posix()
        return ZoneInfo(key)
    except (OSError, ValueError, ZoneInfoNotFoundError):
        pass

    try:
        with Path("/etc/localtime").open("rb") as stream:
            return ZoneInfo.from_file(stream)
    except OSError:
        return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")


def next_scheduled_after(policy: Policy, after: datetime) -> datetime:
    """Return the next configured local occurrence strictly after ``after``."""
    if after.tzinfo is None:
        after = after.replace(tzinfo=local_timezone())
    hour, minute = (int(part) for part in policy.schedule.time.split(":"))
    scheduled_time = time(hour=hour, minute=minute)

    for offset in range(8):
        day = after.date() + timedelta(days=offset)
        if day.weekday() not in {_DAY_INDEX[name] for name in policy.schedule.days}:
            continue
        candidate = datetime.combine(day, scheduled_time, tzinfo=after.tzinfo)
        if candidate > after:
            return candidate

    raise RuntimeError("failed to find a future schedule occurrence")
