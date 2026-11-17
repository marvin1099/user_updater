import os
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from user_updater.gui.admin import AdminSettingsDialog
from user_updater.gui.full_window import FullDetailsWindow
from user_updater.gui.window import UpdaterWindow
from user_updater.history import RunHistoryStore, history_path_for_state
from user_updater.policy import load_policy
from user_updater.requests import task_request_path
from user_updater.state import StateStore, UpdateState
from user_updater.tool_preferences import UserToolPreferenceStore


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
    assert window.isHidden()
    assert store.load().phase == "awaiting_response"
    window.show_status()
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
    store.save(UpdateState(phase="running", run_id="run-live", message="Updating packages"))
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
    store.save(UpdateState(phase="succeeded", run_id="run-live", message="Complete."))
    window.refresh()
    assert window.isHidden()
    window.show_status()
    assert window.isVisible()
    window.deleteLater()


def test_admin_settings_dialog_builds_a_valid_policy_mapping(tmp_path: Path) -> None:
    _application()
    dialog = AdminSettingsDialog(load_policy())
    dialog.run_user_tools.setChecked(False)
    dialog.user_interval.setValue(12)
    dialog.allow_custom_tools.setChecked(False)
    dialog.tool_checks["flatpak"].setChecked(False)
    dialog.system_automatic_retries.setValue(3)
    dialog.system_retry_delay.setValue(20)
    dialog.system_user_retries.setValue(2)
    dialog.user_tools_automatic_retries.setValue(1)
    dialog.user_tools_user_retries.setValue(3)

    mapping = dialog.policy_mapping()

    assert mapping["updates"]["run_user_tools"] is False
    assert mapping["updates"]["allow_custom_user_tools"] is False
    assert "flatpak" not in mapping["updates"]["user_tools"]
    assert mapping["schedule"]["user_tools_interval_hours"] == 12
    assert mapping["retry"]["system"] == {
        "automatic_attempts": 3,
        "delay_minutes": 20,
        "user_manual_attempts": 2,
    }
    assert mapping["retry"]["user_tools"]["automatic_attempts"] == 1
    assert mapping["retry"]["user_tools"]["user_manual_attempts"] == 3
    dialog.deleteLater()


def test_system_window_keeps_system_and_user_responses_separate(tmp_path: Path) -> None:
    _application()
    home = tmp_path / "alice"
    home.mkdir()
    system_store = StateStore(tmp_path / "system.json")
    user_store = StateStore(tmp_path / "user.json")
    now = datetime.now().astimezone()
    system_store.save(
        UpdateState(
            phase="awaiting_response",
            run_id="system-run",
            response_deadline=(now + timedelta(minutes=5)).isoformat(),
        )
    )
    user_store.save(
        UpdateState(
            phase="awaiting_response",
            run_id="user-run",
            response_deadline=(now + timedelta(minutes=5)).isoformat(),
        )
    )
    window = UpdaterWindow(
        load_policy(),
        user_store,
        task_request_path(home, "user-tools"),
        start_timer=False,
        system_state_store=system_store,
        system_request_file=task_request_path(home, "system"),
    )

    assert window.system_panel.update_now_button.isHidden()
    assert window.system_panel.defer_button.isHidden()
    window.system_panel.update_now_button.click()
    window.user_panel.defer_choice.setCurrentIndex(1)
    window.user_panel.defer_button.click()

    assert not task_request_path(home, "system").exists()
    assert '"run_id":"user-run"' in task_request_path(home, "user-tools").read_text()
    assert '"hours":4' in task_request_path(home, "user-tools").read_text()
    window.deleteLater()


def test_full_details_has_read_only_task_tabs_and_returns_after_completion(tmp_path: Path) -> None:
    app = _application()
    system_store = StateStore(tmp_path / "system.json")
    user_store = StateStore(tmp_path / "user.json")
    system_store.save(
        UpdateState(
            phase="running",
            run_id="system-run",
            started_at=datetime.now().astimezone().isoformat(),
            last_output=tuple(f"system line {index}" for index in range(250)),
        )
    )
    user_store.save(
        UpdateState(
            phase="running",
            run_id="user-run",
            started_at=datetime.now().astimezone().isoformat(),
            last_output=("user output",),
        )
    )
    window = FullDetailsWindow(system_store, user_store)
    window.show()
    app.processEvents()

    assert window.tabs.count() == 3
    assert window.system_tab.terminal.isReadOnly()
    assert window.user_tab.terminal.isReadOnly()
    system_output = window.system_tab.terminal.toPlainText().splitlines()
    assert len(system_output) == 200
    assert system_output[0] == "system line 50"
    assert system_output[-1] == "system line 249"
    assert "user output" in window.user_tab.terminal.toPlainText()

    system_store.save(UpdateState(phase="succeeded", message="System complete."))
    user_store.save(UpdateState(phase="succeeded", message="User complete."))
    window.refresh()
    app.processEvents()
    assert window._completion_emitted
    window.deleteLater()


def test_full_details_shows_next_run_history_and_owner_retry_control(tmp_path: Path) -> None:
    _application()
    user_store = StateStore(tmp_path / "user.json")
    next_run = (datetime.now().astimezone() + timedelta(hours=12)).isoformat()
    user_store.save(
        UpdateState(
            phase="failed",
            run_id="failed-user-run",
            next_scheduled_at=next_run,
            finished_at=datetime.now().astimezone().isoformat(),
            message="User update failed.",
        )
    )
    RunHistoryStore(history_path_for_state(user_store.path)).append(
        "user-tools", user_store.load()
    )
    request_path = tmp_path / "requests" / "user-tools.json"
    window = FullDetailsWindow(
        None,
        user_store,
        system_enabled=False,
        user_request_file=request_path,
        user_manual_retry_limit=1,
    )

    assert "Next scheduled:" in window.user_tab.next_run.text()
    assert not window.user_tab.retry_button.isHidden()
    assert "1 left" in window.user_tab.retry_button.text()
    assert "User update failed." in window.history_view.toPlainText()

    window.user_tab.retry_button.click()

    assert '"run_id":"failed-user-run"' in request_path.read_text(encoding="utf-8")
    assert '"action":"retry"' in request_path.read_text(encoding="utf-8")
    assert not window.user_tab.retry_button.isEnabled()
    window.deleteLater()


def test_system_retry_button_requests_an_admin_authorized_action(tmp_path: Path) -> None:
    _application()
    system_store = StateStore(tmp_path / "system.json")
    user_store = StateStore(tmp_path / "user.json")
    system_store.save(
        UpdateState(
            phase="failed",
            run_id="failed-system-run",
            manual_retries_used=1,
        )
    )
    window = FullDetailsWindow(
        system_store,
        user_store,
        user_tools_enabled=False,
        system_manual_retry_limit=2,
    )
    requested: list[bool] = []
    window.system_retry_requested.connect(lambda: requested.append(True))

    window.system_tab.retry_button.click()

    assert requested == [True]
    assert "1 left" in window.system_tab.retry_button.text()
    window.deleteLater()


def test_users_can_toggle_only_admin_enabled_personal_tools(tmp_path: Path) -> None:
    _application()
    user_store = StateStore(tmp_path / "user.json")
    preferences = UserToolPreferenceStore(
        tmp_path / "config" / "tool_preferences.json",
        owner_uid=os.getuid(),
    )
    window = FullDetailsWindow(
        None,
        user_store,
        system_enabled=False,
        admin_allowed_user_tools=("flatpak", "pipx"),
        user_tool_preferences=preferences,
    )

    assert set(window.user_tab.tool_checkboxes) == {"flatpak", "pipx"}
    assert all(check.isChecked() for check in window.user_tab.tool_checkboxes.values())
    window.user_tab.tool_checkboxes["flatpak"].setChecked(False)
    window.user_tab.save_tools_button.click()

    assert preferences.load(("flatpak", "pipx")) == ("pipx",)
    assert "saved" in window.user_tab.tool_preferences_status.text().lower()
    window.deleteLater()


def test_hidden_full_details_does_not_reopen_minimal_ui_on_completion(tmp_path: Path) -> None:
    app = _application()
    system_store = StateStore(tmp_path / "system.json")
    user_store = StateStore(tmp_path / "user.json")
    system_store.save(UpdateState(phase="running", run_id="system-run"))
    user_store.save(UpdateState(phase="running", run_id="user-run"))
    window = FullDetailsWindow(system_store, user_store)
    completed: list[bool] = []
    window.all_updates_done.connect(lambda: completed.append(True))

    system_store.save(UpdateState(phase="succeeded", run_id="system-run"))
    user_store.save(UpdateState(phase="succeeded", run_id="user-run"))
    window.refresh()
    app.processEvents()

    assert completed == []
    window.deleteLater()
