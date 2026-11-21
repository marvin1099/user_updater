import os
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from user_updater.gui import admin as admin_gui
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


def test_auto_hidden_terminal_window_stays_hidden_after_poll(tmp_path: Path) -> None:
    _application()
    store = StateStore(tmp_path / "state.json")
    store.save(UpdateState(phase="failed", run_id="failed-run", message="Update failed."))
    window = UpdaterWindow(load_policy(), store, tmp_path / "request.json", start_timer=False)

    window.refresh()
    assert window.isVisible()

    window._hide_if_phase("failed")
    assert window.isHidden()
    window.refresh()

    assert window.isHidden()
    window.deleteLater()


def test_old_terminal_hide_timer_does_not_hide_a_later_run(tmp_path: Path) -> None:
    _application()
    store = StateStore(tmp_path / "state.json")
    store.save(UpdateState(phase="failed", run_id="old-run"))
    window = UpdaterWindow(load_policy(), store, tmp_path / "request.json", start_timer=False)

    window.refresh()
    old_run_keys = frozenset({("user-tools", "old-run")})
    store.save(UpdateState(phase="failed", run_id="new-run"))
    window.refresh()

    window._hide_if_phase("failed", old_run_keys)

    assert window.isVisible()
    window.deleteLater()


def test_terminal_hide_timer_ignores_disabled_task_state(tmp_path: Path) -> None:
    _application()
    policy_file = tmp_path / "project.yaml"
    policy_file.write_text(
        "version: 1\nupdates:\n  run_user_tools: false\n",
        encoding="utf-8",
    )
    user_store = StateStore(tmp_path / "user-state.json")
    user_store.save(UpdateState(phase="succeeded", run_id="old-user-run"))
    system_store = StateStore(tmp_path / "system-state.json")
    system_store.save(UpdateState(phase="failed", run_id="system-run"))
    window = UpdaterWindow(
        load_policy(policy_file),
        user_store,
        tmp_path / "request.json",
        start_timer=False,
        system_state_store=system_store,
    )
    window.refresh()
    expected_run_keys = frozenset({("system", "system-run")})

    window._hide_if_phase("failed", expected_run_keys)

    assert window.isHidden()
    window.refresh()
    assert window.isHidden()
    window.deleteLater()


def test_dismissing_terminal_state_without_run_id_does_not_respawn(tmp_path: Path) -> None:
    _application()
    store = StateStore(tmp_path / "state.json")
    store.save(UpdateState(phase="failed", message="Setup failed."))
    window = UpdaterWindow(load_policy(), store, tmp_path / "request.json", start_timer=False)

    window.refresh()
    window.close()
    window.refresh()

    assert window.isHidden()
    window.deleteLater()


def test_dismissing_unreadable_state_does_not_respawn_until_requested(tmp_path: Path) -> None:
    _application()
    state_path = tmp_path / "state.json"
    state_path.write_text("{", encoding="utf-8")
    store = StateStore(state_path)
    window = UpdaterWindow(load_policy(), store, tmp_path / "request.json", start_timer=False)

    window.refresh()
    assert window.isVisible()
    assert window.close_button.isVisible()
    window.close()
    window.refresh()
    assert window.isHidden()

    window.show_status()
    assert window.isVisible()
    window.deleteLater()


def test_admin_settings_dialog_builds_a_valid_policy_mapping(tmp_path: Path) -> None:
    _application()
    dialog = AdminSettingsDialog(load_policy())
    assert "scripts" in dialog.allow_custom_tools.text().lower()
    assert dialog.allow_custom_tools.isChecked()
    assert dialog.run_system_now_button.isEnabled()
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


def test_admin_run_now_button_starts_the_authorized_system_command(monkeypatch) -> None:
    _application()
    dialog = AdminSettingsDialog(load_policy())
    commands: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        admin_gui, "_trusted_admin_helper", lambda: "/usr/local/bin/user-updater-admin"
    )
    monkeypatch.setattr(
        admin_gui.shutil,
        "which",
        lambda name: "/usr/bin/pkexec" if name == "pkexec" else None,
    )
    monkeypatch.setattr(
        admin_gui.QProcess,
        "startDetached",
        lambda executable, arguments: (commands.append((executable, arguments)) or True, 1234),
    )
    monkeypatch.setattr(admin_gui.QMessageBox, "information", lambda *_args: None)

    dialog.run_system_now_button.click()

    assert commands == [
        ("/usr/bin/pkexec", ["/usr/local/bin/user-updater-admin", "run-now"])
    ]
    dialog.deleteLater()


def test_admin_settings_and_run_action_share_the_cached_polkit_helper(
    monkeypatch, tmp_path: Path
) -> None:
    _application()
    helper = "/usr/local/bin/user-updater-admin"
    commands: list[list[str]] = []
    saved: list[object] = []
    policy_file = tmp_path / "project.yaml"
    policy_file.write_text("version: 1\n", encoding="utf-8")

    class AcceptedDialog:
        def __init__(self, _policy, _parent) -> None:
            pass

        def exec(self):
            return admin_gui.QDialog.DialogCode.Accepted

        def policy_mapping(self):
            return {"version": 1}

    monkeypatch.setattr(admin_gui.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(admin_gui, "_trusted_admin_helper", lambda: helper)
    monkeypatch.setattr(
        admin_gui.shutil,
        "which",
        lambda name: "/usr/bin/pkexec" if name == "pkexec" else None,
    )
    monkeypatch.setattr(admin_gui, "AdminSettingsDialog", AcceptedDialog)

    def fake_run(arguments, **_kwargs):
        commands.append(arguments)
        return type("Completed", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(admin_gui.subprocess, "run", fake_run)
    admin_gui.open_admin_settings(None, policy_file, saved.append)

    assert commands == [
        ["/usr/bin/pkexec", helper, "authorize-settings"],
        ["/usr/bin/pkexec", helper, "write-policy"],
    ]
    assert len(saved) == 1


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


def test_system_minimal_view_compacts_failures_and_hides_idle_task(tmp_path: Path) -> None:
    _application()
    system_store = StateStore(tmp_path / "system.json")
    user_store = StateStore(tmp_path / "user.json")
    system_store.save(
        UpdateState(
            phase="idle",
            next_scheduled_at=(datetime.now().astimezone() + timedelta(days=1)).isoformat(),
        )
    )
    user_store.save(
        UpdateState(
            phase="failed",
            run_id="failed-user-run",
            message="The detailed failure should stay in Full details.",
        )
    )

    window = UpdaterWindow(
        load_policy(),
        user_store,
        tmp_path / "user-request.json",
        system_state_store=system_store,
        admin_enabled=True,
    )

    assert window.system_panel is not None
    assert window.system_panel.isHidden()
    assert window.user_panel.status.text() == "Your user tools: Failed"
    assert "detailed failure" not in window.message.text()
    assert "Full details" in window.message.text()
    assert window.height() < 350
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


def test_full_details_renders_ansi_color_without_showing_escape_codes(tmp_path: Path) -> None:
    _application()
    system_store = StateStore(tmp_path / "system.json")
    user_store = StateStore(tmp_path / "user.json")
    user_store.save(
        UpdateState(
            phase="running",
            run_id="ansi-run",
            last_output=("\x1b[31mfailed\x1b[0m plain",),
        )
    )

    window = FullDetailsWindow(system_store, user_store)
    text = window.user_tab.terminal.toPlainText()
    colored_word = window.user_tab.terminal.document().find("failed")

    assert text == "failed plain"
    assert colored_word.hasSelection()
    assert colored_word.charFormat().foreground().color() == QColor("#aa0000")
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
    assert "tools.d" in window.user_tab.custom_tools_checkbox.text()
    assert window.user_tab.custom_tools_checkbox.isChecked()
    window.user_tab.tool_checkboxes["flatpak"].setChecked(False)
    window.user_tab.custom_tools_checkbox.setChecked(False)
    window.user_tab.save_tools_button.click()

    assert preferences.load(("flatpak", "pipx")) == ("pipx",)
    assert preferences.load_all(("flatpak", "pipx")).custom_tools is False
    assert "saved" in window.user_tab.tool_preferences_status.text().lower()
    window.deleteLater()


def test_admin_policy_hides_and_disables_the_user_tools_d_checkbox(tmp_path: Path) -> None:
    _application()
    preferences = UserToolPreferenceStore(
        tmp_path / "config" / "tool_preferences.json",
        owner_uid=os.getuid(),
    )
    window = FullDetailsWindow(
        None,
        StateStore(tmp_path / "user.json"),
        system_enabled=False,
        admin_allowed_user_tools=("flatpak",),
        admin_allows_custom_tools=False,
        user_tool_preferences=preferences,
    )

    assert window.user_tab.custom_tools_checkbox.isHidden()
    assert preferences.load_all(("flatpak",), admin_allows_custom=False).custom_tools is False
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
