"""Frameless update prompt and running-status window."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..policy import Policy
from ..requests import UserRequest, save_request
from ..state import StateError, StateStore, UpdateState


class UpdaterWindow(QWidget):
    """Small, titleless status window that cannot hide an active update."""

    phase_changed = Signal(str)

    def __init__(
        self,
        policy: Policy,
        state_store: StateStore,
        request_file: Path,
        start_timer: bool = True,
    ) -> None:
        super().__init__(None)
        self.policy = policy
        self.state_store = state_store
        self.request_file = Path(request_file)
        self._phase = "idle"
        self._request_sent = False
        self._current_run_id: str | None = None
        self._dismissed_run_id: str | None = None
        self._build_ui()
        if start_timer:
            self._poll_timer = QTimer(self)
            self._poll_timer.timeout.connect(self.refresh)
            self._poll_timer.start(1000)
        self.refresh()

    def _build_ui(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setFixedSize(480, 190)
        self.setStyleSheet(
            "QWidget { background: #20242b; color: #f2f4f8; }"
            "QLabel#heading { font-size: 16px; font-weight: 700; }"
            "QLabel#message { color: #d2d8e0; }"
            "QPushButton { padding: 7px 12px; border-radius: 4px; }"
            "QPushButton#primary { background: #2778c4; color: white; }"
            "QProgressBar { border: 0; background: #343a44; height: 5px; }"
            "QProgressBar::chunk { background: #55a8f4; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)
        self.heading = QLabel("System updater")
        self.heading.setObjectName("heading")
        self.message = QLabel("Waiting for update status…")
        self.message.setObjectName("message")
        self.message.setWordWrap(True)
        self.countdown = QLabel("")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.output = QLabel("")
        self.output.setWordWrap(True)
        self.output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        controls = QHBoxLayout()
        self.defer_choice = QComboBox()
        self.defer_button = QPushButton("Reschedule")
        self.defer_button.setObjectName("defer_button")
        self.update_now_button = QPushButton("Update now")
        self.update_now_button.setObjectName("primary")
        self.update_now_button.setDefault(True)
        self.close_button = QPushButton("Close")
        self.close_button.hide()
        controls.addWidget(self.defer_choice, stretch=1)
        controls.addWidget(self.defer_button)
        controls.addWidget(self.update_now_button)
        controls.addWidget(self.close_button)

        layout.addWidget(self.heading)
        layout.addWidget(self.message)
        layout.addWidget(self.countdown)
        layout.addWidget(self.progress)
        layout.addWidget(self.output)
        layout.addLayout(controls)

        self.defer_button.clicked.connect(self._submit_defer)
        self.update_now_button.clicked.connect(self._submit_start_now)
        self.close_button.clicked.connect(self.close)

    def refresh(self) -> None:
        try:
            state = self.state_store.load()
        except StateError as exc:
            self._set_phase("failed")
            self.heading.setText("Updater status unavailable")
            self.message.setText(str(exc))
            self.show()
            return

        old_phase = self._phase
        self._phase = state.phase
        if state.run_id != self._current_run_id:
            self._request_sent = False
            self._current_run_id = state.run_id
            self._dismissed_run_id = None
        if state.phase != "awaiting_response":
            self._request_sent = False
        self.output.setText("\n".join(state.last_output[-4:]))
        self.message.setText(state.message)
        self.close_button.hide()
        self.defer_button.hide()
        self.defer_choice.hide()
        self.update_now_button.hide()
        self.progress.hide()
        self.countdown.hide()

        if state.phase == "awaiting_response":
            self.heading.setText("Update scheduled")
            self._show_response_controls(state)
            self.show()
        elif state.phase == "running":
            self.heading.setText("Updates are running")
            self.message.setText("Do not turn off or restart the computer until this finishes.")
            self.progress.show()
            if self._dismissed_run_id == state.run_id:
                self.hide()
            else:
                self.show()
        elif state.phase == "deferred":
            self.heading.setText("Update rescheduled")
            self.close_button.show()
            self.show()
            if old_phase != state.phase:
                QTimer.singleShot(20_000, lambda: self._hide_if_phase("deferred"))
        elif state.phase in {"succeeded", "failed"}:
            self.heading.setText(
                "Update complete" if state.phase == "succeeded" else "Update needs attention"
            )
            self.close_button.show()
            self.show()
            if old_phase != state.phase:
                QTimer.singleShot(30_000, lambda: self._hide_if_phase(state.phase))
        else:
            self.heading.setText("System updater")
            self.hide()

        if old_phase != self._phase:
            self.phase_changed.emit(self._phase)
            self._position_window()

    @property
    def phase(self) -> str:
        return self._phase

    def show_status(self) -> None:
        self._dismissed_run_id = None
        self.refresh()
        if self._phase != "idle":
            self.show()

    def _show_response_controls(self, state: UpdateState) -> None:
        self.update_now_button.show()
        remaining = _remaining_seconds(state.response_deadline)
        if remaining is None:
            self.countdown.setText("The update will begin automatically after the response window.")
        else:
            minutes, seconds = divmod(remaining, 60)
            self.countdown.setText(
                f"Starting automatically in {minutes}:{seconds:02d} unless rescheduled."
            )
        self.countdown.show()
        allowed = (
            self.policy.interaction.allow_deferral
            and state.deferrals_used < self.policy.interaction.max_deferrals_per_run
            and bool(self.policy.interaction.deferral_options_hours)
        )
        if allowed:
            self.defer_choice.clear()
            for hours in self.policy.interaction.deferral_options_hours:
                self.defer_choice.addItem(_duration_label(hours), hours)
            self.defer_choice.show()
            self.defer_button.show()
        if self._request_sent:
            self.update_now_button.setEnabled(False)
            self.defer_button.setEnabled(False)
            self.defer_choice.setEnabled(False)
            self.message.setText("Your response was sent. The updater is applying it.")
        else:
            self.update_now_button.setEnabled(True)
            self.defer_button.setEnabled(True)
            self.defer_choice.setEnabled(True)

    def _submit_defer(self) -> None:
        state = self.state_store.load()
        hours = self.defer_choice.currentData()
        if state.phase != "awaiting_response" or state.run_id is None or hours is None:
            return
        self._save_user_request(UserRequest(state.run_id, "defer", int(hours)))

    def _submit_start_now(self) -> None:
        state = self.state_store.load()
        if state.phase != "awaiting_response" or state.run_id is None:
            return
        self._save_user_request(UserRequest(state.run_id, "start_now"))

    def _save_user_request(self, request: UserRequest) -> None:
        try:
            save_request(self.request_file, request)
            self._request_sent = True
            self.refresh()
        except OSError as exc:
            self.message.setText(f"Could not send your response: {exc}")

    def _position_window(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.move(available.right() - self.width() - 24, available.bottom() - self.height() - 24)

    def _hide_if_phase(self, expected_phase: str) -> None:
        if self._phase == expected_phase:
            self.hide()

    def _set_phase(self, phase: str) -> None:
        self._phase = phase

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if self._phase == "awaiting_response":
            event.ignore()
        else:
            if self._phase == "running":
                self._dismissed_run_id = self._current_run_id
            event.accept()


def _duration_label(hours: int) -> str:
    if hours < 24:
        return f"Reschedule {hours} hour{'s' if hours != 1 else ''}"
    days, remainder = divmod(hours, 24)
    if remainder:
        return f"Reschedule {days} day(s), {remainder} hour(s)"
    return f"Reschedule {days} day{'s' if days != 1 else ''}"


def _remaining_seconds(deadline: str | None) -> int | None:
    if deadline is None:
        return None
    try:
        end = datetime.fromisoformat(deadline)
    except ValueError:
        return None
    if end.tzinfo is None:
        return None
    return max(0, int((end - datetime.now().astimezone()).total_seconds()))
