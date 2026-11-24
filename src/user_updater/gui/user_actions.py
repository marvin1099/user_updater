"""Launch unprivileged user-update actions from the desktop client."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QMessageBox, QWidget


def start_personal_update(parent: QWidget) -> None:
    """Start a fresh personal-tool update without waiting for its completion in the GUI."""
    _start_user_command(
        parent,
        ["run-now", "--user"],
        "Starting your personal-tool update. It will wait for stable internet.",
    )


def schedule_personal_prompt(parent: QWidget) -> None:
    """Queue a due personal-tool prompt while preserving its normal response window."""
    _start_user_command(
        parent,
        ["schedule-prompt", "--user"],
        "Scheduling an update prompt. It will appear after internet is stable.",
    )


def _start_user_command(parent: QWidget, arguments: list[str], status: str) -> None:
    executable = Path(sys.argv[0])
    if not executable.is_absolute():
        located = shutil.which(str(executable))
        executable = Path(located) if located is not None else Path.cwd() / executable
    started, _pid = QProcess.startDetached(str(executable), arguments)
    if not started:
        QMessageBox.warning(parent, "User Updater", "Could not start the requested action.")
        return
    message = getattr(parent, "message", None)
    if message is not None:
        message.setText(status)
