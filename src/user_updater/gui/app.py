"""Qt application entry point for system or personal installations."""

from __future__ import annotations

import os
import pwd
from pathlib import Path

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from ..paths import migrate_user_data_dirs, system_paths, user_paths
from ..policy import load_policy
from ..requests import task_request_path
from ..state import StateStore
from ..tool_preferences import UserToolPreferenceStore
from .admin import open_admin_settings, schedule_update_prompt, start_system_retry
from .full_window import FullDetailsWindow
from .window import UpdaterWindow


def run_gui(system_mode: bool = False) -> int:
    application = QApplication.instance() or QApplication([])
    application.setApplicationName("User Updater")
    application.setQuitOnLastWindowClosed(False)
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    if system_mode:
        migrate_user_data_dirs(home, owner_uid=os.getuid())
    paths = system_paths(home) if system_mode else user_paths(home)
    policy = load_policy(paths.policy if paths.policy.exists() else None)
    user_state_path = paths.user_state_dir / f"{os.getuid()}.json" if system_mode else paths.state
    user_state = StateStore(user_state_path, file_mode=0o644 if system_mode else 0o600)
    system_state = StateStore(paths.state) if system_mode else None
    user_request = task_request_path(home, "user-tools")
    system_request = task_request_path(home, "system") if system_mode else None
    user_tool_preferences = UserToolPreferenceStore(
        home / ".config" / "user-updater" / "tool_preferences.json",
        owner_uid=os.getuid(),
    )
    window = UpdaterWindow(
        policy,
        user_state,
        user_request,
        system_state_store=system_state,
        system_request_file=system_request,
        admin_enabled=system_mode,
    )
    full_window = FullDetailsWindow(
        system_state,
        user_state,
        system_enabled=system_mode and policy.updates.run_system,
        user_tools_enabled=policy.updates.run_user_tools,
        user_request_file=user_request,
        system_output_visible=True,
        system_manual_retry_limit=policy.retry.system.user_manual_attempts,
        user_manual_retry_limit=policy.retry.user_tools.user_manual_attempts,
        system_automatic_retry_limit=policy.retry.system.automatic_attempts,
        user_automatic_retry_limit=policy.retry.user_tools.automatic_attempts,
        admin_allowed_user_tools=policy.updates.user_tools,
        admin_allows_custom_tools=policy.updates.allow_custom_user_tools,
        user_tool_preferences=user_tool_preferences,
    )

    def show_full_details() -> None:
        window.set_full_mode(True)
        full_window.reset_section_expansion()
        full_window.show()
        full_window.position_bottom_right()
        full_window.raise_()
        full_window.activateWindow()

    def return_to_minimal() -> None:
        window.show_status()

    def finish_full_details() -> None:
        full_window.hide()
        window.show_status()

    def policy_saved(updated_policy) -> None:
        window.apply_policy(updated_policy)
        full_window.system_enabled = system_mode and updated_policy.updates.run_system
        full_window.system_manual_retry_limit = updated_policy.retry.system.user_manual_attempts
        full_window.user_manual_retry_limit = updated_policy.retry.user_tools.user_manual_attempts
        full_window.system_automatic_retry_limit = updated_policy.retry.system.automatic_attempts
        full_window.user_automatic_retry_limit = updated_policy.retry.user_tools.automatic_attempts
        full_window.apply_user_tool_policy(
            updated_policy.updates.run_user_tools,
            updated_policy.updates.user_tools,
            updated_policy.updates.allow_custom_user_tools,
        )
        full_window.refresh()

    window.full_mode_requested.connect(show_full_details)
    window.admin_requested.connect(
        lambda: open_admin_settings(
            window,
            paths.policy,
            policy_saved,
            on_schedule_prompt=lambda task_id: schedule_update_prompt(window, task_id),
        )
    )
    window.system_retry_requested.connect(lambda: start_system_retry(window))
    full_window.system_retry_requested.connect(lambda: start_system_retry(full_window))
    full_window.back_requested.connect(return_to_minimal)
    full_window.all_updates_done.connect(finish_full_details)

    tray: QSystemTrayIcon | None = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        tray = QSystemTrayIcon(QIcon(icon), application)
        tray.setToolTip("User Updater")
        menu = QMenu()
        show_action = QAction("Show update status", menu)
        show_action.triggered.connect(window.show_status)
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(application.quit)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda _reason: window.show_status())
        tray.show()

        def update_tray_actions(phase: str | None = None) -> None:
            active_phase = phase if phase is not None else window.phase
            active = active_phase in {"awaiting_response", "running"}
            quit_action.setEnabled(not active)
            tray.setToolTip("Updates running — do not power off" if active else "User Updater")

        window.phase_changed.connect(update_tray_actions)
        update_tray_actions()

    return application.exec()
