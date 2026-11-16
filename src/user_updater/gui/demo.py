"""Safe Qt demonstration that never invokes system package tools."""

from __future__ import annotations

import os
import tempfile
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ..policy import load_policy
from ..requests import load_request, request_path
from ..state import StateStore, UpdateState
from .full_window import FullDetailsWindow
from .window import UpdaterWindow


def run_demo() -> int:
    application = QApplication.instance() or QApplication([])
    application.setApplicationName("User Updater Demo")
    application.setQuitOnLastWindowClosed(False)
    temporary_directory = tempfile.TemporaryDirectory(prefix="user-updater-demo-")
    root = Path(temporary_directory.name)
    store = StateStore(root / "state.json", file_mode=0o600)
    request_file = request_path(root)
    run_id = "demo-run"
    now = datetime.now().astimezone()
    store.save(
        UpdateState(
            phase="awaiting_response",
            run_id=run_id,
            scheduled_for=now.isoformat(),
            response_deadline=(now + timedelta(seconds=30)).isoformat(),
            message="Demo update: choose Update now, or reschedule it.",
        )
    )
    window = UpdaterWindow(load_policy(), store, request_file)
    full_window = FullDetailsWindow(None, store, system_enabled=False)
    window.full_mode_requested.connect(lambda: _show_full_details(window, full_window))
    full_window.back_requested.connect(window.show_status)

    def finish_full_details() -> None:
        full_window.hide()
        window.show_status()

    full_window.all_updates_done.connect(finish_full_details)
    output: deque[str] = deque(maxlen=200)
    run_timer = QTimer()
    run_timer.setInterval(1000)
    demo_lines = iter(
        (
            "Checking configured update sources…",
            "Updating system packages…",
            "Updating user tools…",
            "Verifying package state…",
            "All simulated update steps finished.",
        )
    )

    def on_running_tick() -> None:
        try:
            line = next(demo_lines)
            output.append(line)
            store.save(
                UpdateState(
                    phase="running",
                    run_id=run_id,
                    scheduled_for=now.isoformat(),
                    started_at=now.isoformat(),
                    message="Simulated update running. Do not power off.",
                    last_output=tuple(output),
                )
            )
            window.refresh()
        except StopIteration:
            finished = datetime.now().astimezone()
            store.save(
                UpdateState(
                    phase="succeeded",
                    run_id=run_id,
                    scheduled_for=now.isoformat(),
                    started_at=now.isoformat(),
                    finished_at=finished.isoformat(),
                    returncode=0,
                    message="Demo update completed successfully.",
                    last_output=tuple(output),
                )
            )
            window.refresh()
            run_timer.stop()

    def check_response() -> None:
        state = store.load()
        if state.phase != "awaiting_response":
            return
        request = load_request(request_file, os.getuid(), run_id)
        if request is not None:
            request_file.unlink(missing_ok=True)
            if request.action == "defer" and request.hours is not None:
                until = datetime.now().astimezone() + timedelta(hours=request.hours)
                store.save(
                    UpdateState(
                        phase="deferred",
                        run_id=run_id,
                        scheduled_for=now.isoformat(),
                        deferred_until=until.isoformat(),
                        deferrals_used=1,
                        message=f"Demo rescheduled until {until.strftime('%H:%M %Z')}.",
                    )
                )
                window.refresh()
                return
            start_update()
            return
        if state.response_deadline and datetime.now().astimezone() >= datetime.fromisoformat(
            state.response_deadline
        ):
            start_update()

    def start_update() -> None:
        store.save(
            UpdateState(
                phase="running",
                run_id=run_id,
                scheduled_for=now.isoformat(),
                started_at=datetime.now().astimezone().isoformat(),
                message="Simulated update running. Do not power off.",
            )
        )
        window.refresh()
        response_timer.stop()
        run_timer.start()

    response_timer = QTimer()
    response_timer.timeout.connect(check_response)
    response_timer.start(250)
    run_timer.timeout.connect(on_running_tick)

    result = application.exec()
    temporary_directory.cleanup()
    return result


def _show_full_details(window: UpdaterWindow, full_window: FullDetailsWindow) -> None:
    window.set_full_mode(True)
    full_window.refresh()
    full_window.show()
    full_window.position_bottom_right()
