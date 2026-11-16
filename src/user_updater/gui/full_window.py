"""Expanded read-only update console with separate system and user tabs."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..state import StateError, StateStore, UpdateState
from .placement import WindowPlacement

_ACTIVE_PHASES = {"awaiting_response", "running", "deferred"}


class _TaskTab(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.status = QLabel(f"{title}: waiting")
        self.elapsed = QLabel("Elapsed: —")
        self.terminal = QPlainTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.terminal.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.terminal.setStyleSheet("QPlainTextEdit { background: #11151a; color: #dbe7f1; }")
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(self.status, stretch=1)
        heading.addWidget(self.elapsed)
        layout.addLayout(heading)
        layout.addWidget(self.terminal, stretch=1)

    def update_state(self, title: str, state: UpdateState) -> None:
        self.status.setText(f"{title}: {state.phase.replace('_', ' ').title()} — {state.message}")
        self.elapsed.setText(f"Elapsed: {_elapsed_text(state)}")
        text = "\n".join(state.last_output)
        if text != self.terminal.toPlainText():
            self.terminal.setPlainText(text)
            self.terminal.moveCursor(QTextCursor.MoveOperation.End)


class FullDetailsWindow(QWidget):
    """Titleless, always-on-top details view; closing returns to the minimal window."""

    back_requested = Signal()
    all_updates_done = Signal()

    def __init__(
        self,
        system_state_store: StateStore | None,
        user_state_store: StateStore,
        *,
        system_enabled: bool = True,
        user_tools_enabled: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.system_state_store = system_state_store
        self.user_state_store = user_state_store
        self.system_enabled = system_enabled
        self.user_tools_enabled = user_tools_enabled
        self._seen_active = False
        self._completion_emitted = False
        self.setWindowTitle("User Updater Details")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.resize(840, 560)
        self.setMinimumSize(640, 420)
        self.setStyleSheet("QWidget { background: #20242b; color: #f2f4f8; }")
        self._placement = WindowPlacement(
            self,
            title=self.windowTitle(),
            activate_on_show=True,
        )

        title = QLabel("Update details")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.tabs = QTabWidget()
        self.system_tab = _TaskTab("System updates")
        self.user_tab = _TaskTab("User tools")
        self.tabs.addTab(self.system_tab, "System updates")
        self.tabs.addTab(self.user_tab, "User tools")
        self.back_button = QPushButton("Back to minimal view")
        self.back_button.clicked.connect(self._return_to_minimal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(title)
        layout.addWidget(self.tabs, stretch=1)
        controls = QHBoxLayout()
        controls.addStretch(1)
        controls.addWidget(self.back_button)
        layout.addLayout(controls)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(500)
        self.refresh()

    def refresh(self) -> None:
        system_state = self._read_state(self.system_state_store, self.system_enabled)
        user_state = self._read_state(self.user_state_store, self.user_tools_enabled)
        self.system_tab.update_state("System updates", system_state)
        self.user_tab.update_state("Your user tools", user_state)
        states = [system_state, user_state]
        active = any(state.phase in _ACTIVE_PHASES for state in states)
        if active and self._completion_emitted:
            self._completion_emitted = False
        self._seen_active = self._seen_active or active
        if self.isVisible() and self._seen_active and not active and not self._completion_emitted:
            self._completion_emitted = True
            QTimer.singleShot(0, self.all_updates_done.emit)

    def position_bottom_right(self) -> None:
        """Place the details window at the bottom right when the compositor allows it."""
        self._placement.position_bottom_right()

    @staticmethod
    def _read_state(store: StateStore | None, enabled: bool) -> UpdateState:
        if store is None and enabled:
            return UpdateState(phase="idle", message="No machine-wide system task in this install.")
        if store is None or not enabled:
            if store is not None:
                try:
                    state = store.load()
                    if state.phase == "running":
                        return state
                except StateError:
                    pass
            return UpdateState(phase="disabled", message="Disabled by administrator policy.")
        try:
            return store.load()
        except StateError as exc:
            return UpdateState(phase="failed", message=f"Could not read update state: {exc}")

    def _return_to_minimal(self) -> None:
        self.hide()
        self.back_requested.emit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        event.accept()
        self.back_requested.emit()


def _elapsed_text(state: UpdateState) -> str:
    if state.started_at is None:
        return "—"
    try:
        started = datetime.fromisoformat(state.started_at)
        ended = (
            datetime.fromisoformat(state.finished_at)
            if state.finished_at
            else datetime.now(started.tzinfo)
        )
    except ValueError:
        return "unknown"
    seconds = max(0, int((ended - started).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
