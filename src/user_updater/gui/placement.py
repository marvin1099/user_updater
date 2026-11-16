"""Best-effort Wayland placement adapters for updater windows."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QProcess, QTimer, qVersion
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

_ACTIVE_PROCESSES: set[QProcess] = set()


def is_wayland_session() -> bool:
    return QGuiApplication.platformName().lower() == "wayland"


def is_kde_session() -> bool:
    desktops = (
        os.environ.get("XDG_CURRENT_DESKTOP", "")
        + ":"
        + os.environ.get("XDG_SESSION_DESKTOP", "")
        + ":"
        + os.environ.get("DESKTOP_SESSION", "")
    )
    return any(
        "kde" in desktop.lower() or "plasma" in desktop.lower()
        for desktop in desktops.split(":")
    )


def _kdotool_move_arguments(pid: int, title: str, x: int, y: int) -> list[str]:
    """Build a PID-and-title-scoped move command; never target another app's window."""
    return [
        "--quiet",
        "search",
        "--all",
        "--pid",
        str(pid),
        "--title",
        title,
        "windowmove",
        str(x),
        str(y),
    ]


class WindowPlacement:
    """Position a Qt window using layer-shell, KWin's kdotool, or Qt fallback."""

    def __init__(
        self,
        window: QWidget,
        *,
        title: str,
        margin: int = 20,
        activate_on_show: bool = False,
    ) -> None:
        self.window = window
        self.title = title
        self.margin = margin
        self.activate_on_show = activate_on_show
        self._layer_shell_enabled = self._configure_layer_shell()
        self._kdotool = (
            shutil.which("kdotool") if is_wayland_session() and is_kde_session() else None
        )

    def position_bottom_right(self) -> None:
        if self._layer_shell_enabled:
            # LayerShellQt anchors the mapped surface itself, including after output changes.
            return

        screen = self.window.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = available.x() + available.width() - self.window.width() - self.margin
        y = available.y() + available.height() - self.window.height() - self.margin

        # Retain the ordinary xdg-shell/X11 request as a fallback. On KDE Wayland,
        # kdotool applies the compositor-side move after KWin has registered the window.
        self.window.move(x, y)
        if self._kdotool:
            QTimer.singleShot(120, lambda: self._move_with_kdotool(x, y))

    def _configure_layer_shell(self) -> bool:
        if not is_wayland_session() or is_kde_session():
            return False

        try:
            from . import _layer_shell_build
        except ImportError:
            return False

        if _version_prefix(_layer_shell_build.QT_VERSION) != _version_prefix(qVersion()):
            logger.debug(
                "Not enabling LayerShellQt bridge: build Qt %s does not match PySide6 Qt %s",
                _layer_shell_build.QT_VERSION,
                qVersion(),
            )
            return False

        try:
            from shiboken6 import Shiboken

            from . import _layer_shell

            if _version_prefix(_layer_shell.qt_version()) != _version_prefix(qVersion()):
                return False
            widget_pointer = Shiboken.getCppPointer(self.window)[0]
            return bool(
                _layer_shell.configure_window(
                    widget_pointer,
                    self.window.width(),
                    self.window.height(),
                    self.margin,
                    int(self.activate_on_show),
                )
            )
        except (RuntimeError, TypeError, ValueError):
            logger.debug("Could not configure wlr-layer-shell for updater window", exc_info=True)
            return False

    def _move_with_kdotool(self, x: int, y: int) -> None:
        if not self._kdotool:
            return
        application = QApplication.instance()
        process = QProcess(application)
        process.setProgram(str(Path(self._kdotool)))
        process.setArguments(_kdotool_move_arguments(os.getpid(), self.title, x, y))
        _ACTIVE_PROCESSES.add(process)

        def release(*_args: object) -> None:
            if process not in _ACTIVE_PROCESSES:
                return
            _ACTIVE_PROCESSES.discard(process)
            process.deleteLater()

        def on_error(error: QProcess.ProcessError) -> None:
            logger.debug("kdotool failed to start: %s", error)
            if error == QProcess.ProcessError.FailedToStart:
                release()

        process.finished.connect(release)
        process.errorOccurred.connect(on_error)
        process.start()


def _version_prefix(version: str) -> tuple[str, str]:
    parts = version.split(".")
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")
