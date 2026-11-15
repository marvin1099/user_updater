import os
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from user_updater.gui.window import UpdaterWindow
from user_updater.policy import load_policy
from user_updater.state import StateStore, UpdateState


def _application() -> QApplication:
    application = QApplication.instance()
    return application or QApplication([])


def test_pending_window_offers_only_admin_allowed_reschedule_and_update_now(
    tmp_path: Path,
) -> None:
    app = _application()
    store = StateStore(tmp_path / "state.json")
    now = datetime.now().astimezone()
    store.save(
        UpdateState(
            phase="awaiting_response",
            run_id="run-1",
            response_deadline=(now + timedelta(minutes=5)).isoformat(),
            message="Scheduled update is waiting for your response.",
        )
    )
    window = UpdaterWindow(load_policy(), store, tmp_path / "request.json", start_timer=False)

    window.refresh()

    assert window.isVisible()
    assert bool(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
    assert window.update_now_button.isVisible()
    assert window.defer_button.isVisible()
    assert window.defer_choice.count() == 3
    app.processEvents()
    window.close()
    assert window.isVisible()
    window.deleteLater()


def test_defer_button_writes_a_user_request_for_the_current_run(tmp_path: Path) -> None:
    _application()
    store = StateStore(tmp_path / "state.json")
    store.save(UpdateState(phase="awaiting_response", run_id="run-42"))
    request_file = tmp_path / "request.json"
    window = UpdaterWindow(load_policy(), store, request_file, start_timer=False)
    window.refresh()
    window.defer_choice.setCurrentIndex(1)

    window.defer_button.click()

    assert '"run_id":"run-42"' in request_file.read_text(encoding="utf-8")
    assert '"hours":4' in request_file.read_text(encoding="utf-8")
    window.deleteLater()


def test_closing_running_window_hides_only_the_ui_and_does_not_cancel_update(
    tmp_path: Path,
) -> None:
    app = _application()
    store = StateStore(tmp_path / "state.json")
    store.save(UpdateState(phase="running", message="Updating packages"))
    window = UpdaterWindow(load_policy(), store, tmp_path / "request.json", start_timer=False)

    window.refresh()
    app.processEvents()
    window.close()

    assert window.isHidden()
    assert store.load().phase == "running"
    assert bool(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
    assert window.update_now_button.isHidden()
    assert window.defer_button.isHidden()
    window.refresh()
    assert window.isHidden()
    window.show_status()
    assert window.isVisible()
    window.deleteLater()
