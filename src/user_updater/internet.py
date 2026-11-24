"""Internet availability checks for update workflows."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections.abc import Callable

_CONNECTIVITY_CHECK_URL = "https://connectivitycheck.gstatic.com/generate_204"


def internet_available(timeout: float = 5.0) -> bool:
    """Return whether an HTTPS connectivity check reaches the public internet."""
    request = urllib.request.Request(
        _CONNECTIVITY_CHECK_URL,
        headers={"Cache-Control": "no-cache", "User-Agent": "UserUpdater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            # The 204 response distinguishes real connectivity from captive portals.
            return response.status == 204
    except (OSError, TimeoutError, urllib.error.URLError):
        return False


class InternetStabilityGate:
    """Wait until successful internet checks have remained consecutive for a duration."""

    def __init__(
        self,
        *,
        is_online: Callable[[], bool] = internet_available,
        stable_seconds: float = 60.0,
        check_interval: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if stable_seconds < 0:
            raise ValueError("stable_seconds must not be negative")
        if check_interval <= 0:
            raise ValueError("check_interval must be greater than zero")
        self.is_online = is_online
        self.stable_seconds = stable_seconds
        self.check_interval = check_interval
        self.sleep = sleep
        self.monotonic = monotonic

    def wait_until_stable(
        self,
        on_status: Callable[[str], None] | None = None,
        *,
        ready_message: str | None = None,
    ) -> None:
        """Block until internet access has passed consecutive checks for the full duration."""
        stable_since: float | None = None
        last_status: str | None = None
        duration = _duration_label(self.stable_seconds)

        def report(message: str) -> None:
            nonlocal last_status
            if message != last_status and on_status is not None:
                on_status(message)
            last_status = message

        while True:
            try:
                online = self.is_online()
            except Exception:
                # A failed probe is treated as offline: updates must fail closed.
                online = False

            now = self.monotonic()
            if not online:
                if stable_since is None:
                    report("Waiting for internet connection; updates have not started.")
                else:
                    report(
                        f"Internet connection dropped; restarting the {duration} stability check."
                    )
                stable_since = None
            else:
                if stable_since is None:
                    stable_since = now
                    report(
                        f"Internet connection detected; waiting for {duration} of stable access."
                    )
                if now - stable_since >= self.stable_seconds:
                    report(
                        ready_message
                        or f"Internet has been stable for {duration}; starting updates."
                    )
                    return

            self.sleep(self.check_interval)


def _duration_label(seconds: float) -> str:
    if seconds == 60:
        return "1 minute"
    whole_seconds = max(0, int(seconds))
    return f"{whole_seconds} second{'s' if whole_seconds != 1 else ''}"
