"""Minimal frameless prompt/status window for the desktop user."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..policy import Policy
from ..requests import UserRequest, save_request
from ..state import StateError, StateStore, UpdateState
from .placement import WindowPlacement


class _TaskPanel(QWidget):
    def __init__(
        self,
        title: str,
        policy: Policy,
        request_file: Path,
        parent: QWidget | None = None,
        *,
        allow_user_response: bool = True,
    ) -> None:
        super().__init__(parent)
        self.title = title
        self.policy = policy
        self.allow_user_response = allow_user_response
        self.request_file = Path(request_file)
        self._state = UpdateState()
        self._request_sent = False
        self._run_id: str | None = None

        self.status = QLabel(f"{title}: waiting")
        self.status.setWordWrap(False)
        self.countdown = QLabel("")
        self.defer_choice = QComboBox()
        self.defer_button = QPushButton("Reschedule")
        self.update_now_button = QPushButton("Update now")
        self.update_now_button.setObjectName("primary")
        controls = QHBoxLayout()
        controls.addWidget(self.defer_choice, stretch=1)
        controls.addWidget(self.defer_button)
        controls.addWidget(self.update_now_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(self.status)
        layout.addWidget(self.countdown)
        layout.addLayout(controls)

        self.defer_button.clicked.connect(self._submit_defer)
        self.update_now_button.clicked.connect(self._submit_start_now)
        self.hide()

    def apply_state(self, state: UpdateState) -> None:
        self._state = state
        if state.run_id != self._run_id:
            self._run_id = state.run_id
            self._request_sent = False
        if state.phase != "awaiting_response":
            self._request_sent = False

        self.status.setText(f"{self.title}: {state.phase.replace('_', ' ').title()}")
        self.status.setToolTip(state.message)
        show_panel = state.phase != "idle" or bool(state.last_output)
        self.setVisible(show_panel)
        response_open = state.phase == "awaiting_response"
        self.countdown.setVisible(response_open)
        self.defer_choice.setVisible(response_open and self.allow_user_response)
        self.defer_button.setVisible(response_open and self.allow_user_response)
        self.update_now_button.setVisible(response_open and self.allow_user_response)
        if response_open:
            remaining = _remaining_seconds(state.response_deadline)
            if remaining is None:
                self.countdown.setText("No response means the update starts automatically.")
            else:
                minutes, seconds = divmod(remaining, 60)
                self.countdown.setText(f"Automatic start in {minutes}:{seconds:02d}.")
            allowed = self.allow_user_response and (
                self.policy.interaction.allow_deferral
                and state.deferrals_used < self.policy.interaction.max_deferrals_per_run
                and bool(self.policy.interaction.deferral_options_hours)
            )
            self.defer_choice.clear()
            for hours in self.policy.interaction.deferral_options_hours:
                self.defer_choice.addItem(_duration_label(hours), hours)
            self.defer_choice.setVisible(allowed)
            self.defer_button.setVisible(allowed)
            for control in (self.defer_choice, self.defer_button, self.update_now_button):
                control.setEnabled(not self._request_sent)
            if self._request_sent:
                self.status.setText(f"{self.title}: response sent; waiting for the updater.")

    def _submit_defer(self) -> None:
        if not self.allow_user_response:
            return
        hours = self.defer_choice.currentData()
        if self._state.run_id is None or hours is None:
            return
        self._save(UserRequest(self._state.run_id, "defer", int(hours)))

    def _submit_start_now(self) -> None:
        if not self.allow_user_response:
            return
        if self._state.run_id is None:
            return
        self._save(UserRequest(self._state.run_id, "start_now"))

    def _save(self, request: UserRequest) -> None:
        try:
            save_request(self.request_file, request)
            self._request_sent = True
            self.apply_state(self._state)
        except (OSError, ValueError) as exc:
            self.status.setText(f"{self.title}: could not send response: {exc}")


class UpdaterWindow(QWidget):
    """One compact prompt for both independent system and per-user jobs."""

    phase_changed = Signal(str)
    full_mode_requested = Signal()
    admin_requested = Signal()

    def __init__(
        self,
        policy: Policy,
        state_store: StateStore,
        request_file: Path,
        start_timer: bool = True,
        *,
        system_state_store: StateStore | None = None,
        system_request_file: Path | None = None,
        admin_enabled: bool = False,
    ) -> None:
        super().__init__(None)
        self.policy = policy
        self.state_store = state_store
        self.system_state_store = system_state_store
        self._phase = "idle"
        self._full_mode = False
        self._dismissed_runs: set[tuple[str, str | None]] = set()
        self.setWindowTitle("User Updater Status")
        self._build_ui(request_file, system_request_file, admin_enabled)
        self._placement = WindowPlacement(self, title=self.windowTitle())
        if start_timer:
            self._poll_timer = QTimer(self)
            self._poll_timer.timeout.connect(self.refresh)
            self._poll_timer.start(1000)
        self.refresh()

    def _build_ui(
        self,
        request_file: Path,
        system_request_file: Path | None,
        admin_enabled: bool,
    ) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(540)
        self.setMinimumHeight(180)
        self.setStyleSheet(
            "QWidget { background: #20242b; color: #f2f4f8; }"
            "QLabel#heading { font-size: 16px; font-weight: 700; }"
            "QLabel#message { color: #d2d8e0; }"
            "QPushButton { padding: 6px 10px; border-radius: 4px; }"
            "QPushButton#primary { background: #2778c4; color: white; }"
            "QComboBox { padding: 5px; }"
        )

        self.heading = QLabel("System updater" if self.system_state_store else "User updater")
        self.heading.setObjectName("heading")
        self.message = QLabel("Waiting for update status…")
        self.message.setObjectName("message")
        self.message.setWordWrap(True)
        self.system_panel = None
        if self.system_state_store is not None:
            system_path = system_request_file or Path(request_file).with_name("system-request.json")
            self.system_panel = _TaskPanel(
                "System update",
                self.policy,
                system_path,
                self,
                allow_user_response=False,
            )
        self.user_panel = _TaskPanel("Your user tools", self.policy, request_file, self)

        self.full_button = QPushButton("Full details")
        self.full_button.clicked.connect(self.full_mode_requested.emit)
        self.admin_button = QPushButton("Admin settings")
        self.admin_button.setVisible(admin_enabled)
        self.admin_button.clicked.connect(self.admin_requested.emit)
        self.close_button = QPushButton("Close")
        self.close_button.hide()
        buttons = QHBoxLayout()
        buttons.addWidget(self.full_button)
        buttons.addWidget(self.admin_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        layout.addWidget(self.heading)
        layout.addWidget(self.message)
        if self.system_panel is not None:
            layout.addWidget(self.system_panel)
        layout.addWidget(self.user_panel)
        layout.addLayout(buttons)

        self.close_button.clicked.connect(self.close)
        # Backwards-compatible button access for user-only clients and tests.
        self.defer_choice = self.user_panel.defer_choice
        self.defer_button = self.user_panel.defer_button
        self.update_now_button = self.user_panel.update_now_button

    @property
    def phase(self) -> str:
        return self._phase

    def set_full_mode(self, enabled: bool) -> None:
        self._full_mode = enabled
        if enabled:
            self.hide()
        else:
            self.show_status()

    def apply_policy(self, policy: Policy) -> None:
        self.policy = policy
        self.user_panel.policy = policy
        if self.system_panel is not None:
            self.system_panel.policy = policy
        self.refresh()

    def show_status(self) -> None:
        self._full_mode = False
        self._dismissed_runs.clear()
        self.refresh()
        if self._phase != "idle":
            self.show()
            QTimer.singleShot(0, self._position_window)

    def refresh(self) -> None:
        try:
            user_state = self.state_store.load()
            system_state = self.system_state_store.load() if self.system_state_store else None
        except StateError as exc:
            self.heading.setText("Updater status unavailable")
            self.message.setText(str(exc))
            self._phase = "failed"
            if not self._full_mode:
                self.show()
                QTimer.singleShot(0, self._position_window)
            return

        if not self.policy.updates.run_user_tools and user_state.phase != "running":
            user_state = UpdateState(phase="disabled", message="Disabled by administrator policy.")
        if (
            not self.policy.updates.run_system
            and system_state is not None
            and system_state.phase != "running"
        ):
            system_state = UpdateState(
                phase="disabled", message="Disabled by administrator policy."
            )

        self.user_panel.apply_state(user_state)
        if self.system_panel is not None and system_state is not None:
            self.system_panel.apply_state(system_state)

        states = {"user-tools": user_state}
        if system_state is not None:
            states["system"] = system_state
        dismissible = {"awaiting_response", "running", "deferred", "succeeded", "failed"}
        live_run_keys = {
            (task, state.run_id)
            for task, state in states.items()
            if state.run_id is not None and state.phase in dismissible
        }
        self._dismissed_runs.intersection_update(live_run_keys)

        running = [(task, state) for task, state in states.items() if state.phase == "running"]
        awaiting = any(state.phase == "awaiting_response" for state in states.values())
        deferred = any(state.phase == "deferred" for state in states.values())
        previous_phase = self._phase
        if running:
            self._phase = "running"
            self.heading.setText("Updates are running")
            self.message.setText(
                "Do not turn off or restart the computer until all updates finish."
            )
        elif awaiting:
            self._phase = "awaiting_response"
            self.heading.setText("Update scheduled")
            self.message.setText("Respond to each scheduled task below. No response means proceed.")
        elif deferred:
            self._phase = "deferred"
            self.heading.setText("Update rescheduled")
            self.message.setText("A permitted update was postponed until its displayed time.")
        else:
            errors = [state for state in states.values() if state.phase == "failed"]
            completed = [state for state in states.values() if state.phase == "succeeded"]
            self._phase = "failed" if errors else "succeeded" if completed else "idle"
            self.heading.setText(
                "Update needs attention"
                if errors
                else "Update complete"
                if completed
                else "System updater"
            )
            if errors:
                noun = "task" if len(errors) == 1 else "tasks"
                self.message.setText(
                    f"{len(errors)} update {noun} failed. Open Full details to review the output."
                )
            elif completed:
                self.message.setText("Enabled updates completed successfully.")
            else:
                self.message.setText("Waiting for the next scheduled update.")

        response_count = sum(state.phase == "awaiting_response" for state in states.values())
        visible_count = sum(
            state.phase != "idle" or bool(state.last_output) for state in states.values()
        )
        self.setFixedHeight(max(180, min(350, 150 + 38 * visible_count + 45 * response_count)))

        self.close_button.setVisible(self._phase in {"deferred", "succeeded", "failed"})
        if self._phase == "idle":
            self.hide()
        elif not self._full_mode:
            dismissible_states = [
                (task, state) for task, state in states.items() if state.phase in dismissible
            ]
            dismissed = bool(dismissible_states) and all(
                (task, state.run_id) in self._dismissed_runs for task, state in dismissible_states
            )
            if dismissed:
                self.hide()
            else:
                self.show()
                if previous_phase != self._phase:
                    QTimer.singleShot(0, self._position_window)

        if previous_phase != self._phase:
            self.phase_changed.emit(self._phase)
            if self._phase in {"succeeded", "failed"}:
                QTimer.singleShot(30_000, lambda phase=self._phase: self._hide_if_phase(phase))

    def _position_window(self) -> None:
        self._placement.position_bottom_right()

    def _hide_if_phase(self, expected_phase: str) -> None:
        if self._phase == expected_phase and not self._full_mode:
            self.hide()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if self._phase in {"awaiting_response", "running", "deferred", "succeeded", "failed"}:
            for task, state in (
                ("system", self.system_state_store),
                ("user-tools", self.state_store),
            ):
                if state is None:
                    continue
                try:
                    update = state.load()
                except StateError:
                    continue
                if update.phase in {
                    "awaiting_response",
                    "running",
                    "deferred",
                    "succeeded",
                    "failed",
                }:
                    self._dismissed_runs.add((task, update.run_id))
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
