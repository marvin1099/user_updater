"""Qt application entry point for system or personal installations."""

from __future__ import annotations

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from ..paths import system_paths, user_paths
from ..policy import load_policy
from ..state import StateStore
from .window import UpdaterWindow


def run_gui(system_mode: bool = False) -> int:
    application = QApplication.instance() or QApplication([])
    application.setApplicationName("User Updater")
    application.setQuitOnLastWindowClosed(False)
    paths = system_paths() if system_mode else user_paths()
    policy = load_policy(paths.policy if paths.policy.exists() else None)
    window = UpdaterWindow(policy, StateStore(paths.state), paths.request)

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
