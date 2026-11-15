import sys
import threading
import time

from user_updater.runner import run_command


def test_runner_streams_output_and_preserves_child_exit_code() -> None:
    output: list[str] = []

    result = run_command(
        [sys.executable, "-c", "print('package step'); raise SystemExit(7)"],
        on_output=output.append,
        poll_interval=0.02,
    )

    assert result.returncode == 7
    assert "package step" in output
    assert result.output_tail == ("package step",)


def test_quiet_process_warns_but_is_not_killed() -> None:
    idle_periods: list[float] = []
    output: list[str] = []

    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(0.15); print('finished')"],
        on_output=output.append,
        on_idle=idle_periods.append,
        quiet_warning_seconds=0.03,
        poll_interval=0.01,
    )

    assert result.returncode == 0
    assert result.cancelled is False
    assert idle_periods
    assert output == ["finished"]


def test_cancellation_stops_the_child_process_group() -> None:
    cancel = threading.Event()
    outcomes = []

    def run() -> None:
        outcomes.append(
            run_command(
                [sys.executable, "-c", "import time; time.sleep(20)"],
                on_output=lambda _line: None,
                cancel_event=cancel,
                poll_interval=0.02,
                terminate_grace_seconds=0.2,
            )
        )

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.1)
    cancel.set()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert outcomes[0].cancelled is True
