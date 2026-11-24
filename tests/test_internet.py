from __future__ import annotations

from collections import deque

from user_updater.internet import InternetStabilityGate


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _gate(
    outcomes: list[bool],
    clock: FakeClock,
    *,
    stable_seconds: float = 2.0,
    check_interval: float = 1.0,
) -> InternetStabilityGate:
    checks = deque(outcomes)
    return InternetStabilityGate(
        is_online=lambda: checks.popleft() if checks else True,
        stable_seconds=stable_seconds,
        check_interval=check_interval,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def test_internet_gate_waits_while_offline_then_for_stable_access() -> None:
    clock = FakeClock()
    statuses: list[str] = []
    gate = _gate([False, False, True, True, True], clock)

    gate.wait_until_stable(statuses.append)

    assert clock.now == 4
    assert statuses[0] == "Waiting for internet connection; updates have not started."
    assert any("waiting for 2 seconds of stable access" in status for status in statuses)
    assert statuses[-1] == "Internet has been stable for 2 seconds; starting updates."


def test_internet_gate_resets_stability_after_a_failed_check() -> None:
    clock = FakeClock()
    statuses: list[str] = []
    gate = _gate(
        [True, True, False, True, True, True, True],
        clock,
        stable_seconds=3,
    )

    gate.wait_until_stable(statuses.append)

    assert clock.now == 6
    assert "Internet connection dropped; restarting the 3 seconds stability check." in statuses
    assert statuses[-1] == "Internet has been stable for 3 seconds; starting updates."


def test_internet_gate_releases_after_the_full_stable_period() -> None:
    clock = FakeClock()
    gate = _gate([True, True, True, True], clock, stable_seconds=3)

    gate.wait_until_stable()

    assert clock.now == 3
    assert len(clock.sleeps) == 3
