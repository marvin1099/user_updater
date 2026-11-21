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


def test_runner_reports_carriage_return_progress_as_replacements_and_strips_ansi() -> None:
    output: list[str] = []
    progress: list[str] = []

    result = run_command(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write("
            "'\\x1b[2KDownload 10%\\r\\x1b[2KDownload 60%\\r"
            "\\x1b[2KDownload 100%\\nDone\\n'); sys.stdout.flush()",
        ],
        on_output=output.append,
        on_progress=progress.append,
        poll_interval=0.02,
    )

    assert result.returncode == 0
    assert progress == ["Download 10%", "Download 60%", "Download 100%"]
    assert output == ["Done"]
    assert result.output_tail == ("Download 100%", "Done")


def test_runner_keeps_regular_newlines_separate_from_progress() -> None:
    output: list[str] = []
    progress: list[str] = []

    result = run_command(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('First\\nSecond\\rSecond updated\\n')",
        ],
        on_output=output.append,
        on_progress=progress.append,
        poll_interval=0.02,
    )

    assert result.returncode == 0
    assert output == ["First"]
    assert progress == ["Second", "Second updated"]
    assert result.output_tail == ("First", "Second updated")


def test_runner_preserves_sgr_styles_for_the_gui_but_returns_plain_output_tail() -> None:
    output: list[str] = []

    result = run_command(
        [sys.executable, "-c", "print('\\x1b[31mfailed\\x1b[0m')"],
        on_output=output.append,
        poll_interval=0.02,
    )

    assert output == ["\x1b[31mfailed\x1b[0m"]
    assert result.output_tail == ("failed",)


def test_runner_gives_children_a_terminal_without_connecting_terminal_input() -> None:
    output: list[str] = []

    result = run_command(
        [
            sys.executable,
            "-c",
            "import os, sys; "
            "print('{}:{}:{}'.format(sys.stdout.isatty(), sys.stdin.isatty(), os.environ['TERM']))",
        ],
        on_output=output.append,
        poll_interval=0.02,
    )

    assert result.returncode == 0
    assert output == ["True:False:xterm-256color"]


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
