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
from ..rescheduling import available_reschedule_hours
from ..state import StateError, StateStore, UpdateState
from .placement import WindowPlacement

_DISMISSIBLE_PHASES = frozenset({"awaiting_response", "running", "deferred", "succeeded", "failed"})
_TERMINAL_PHASES = frozenset({"succeeded", "failed"})


def _set_widget_visible(widget: QWidget, visible: bool) -> None:
    """Change explicit visibility only when needed, avoiding focus churn on refresh."""
    if widget.isHidden() == visible:
        widget.setVisible(visible)


class _TaskPanel(QWidget):
    def __init__(
        self,
        title: str,
        policy: Policy,
        request_file: Path,
        parent: QWidget | None = None,
        *,
        task_id: str = "user-tools",
        allow_start_now: bool = True,
    ) -> None:
        super().__init__(parent)
        self.title = title
        self.policy = policy
        self.task_id = task_id
        self.allow_start_now = allow_start_now
        self.request_file = Path(request_file)
        self._state = UpdateState()
        self._request_sent = False
        self._run_id: str | None = None

        self.status = QLabel(f"{title}: waiting")
        self.status.setWordWrap(False)
        self.elapsed = QLabel("")
        self.elapsed.setObjectName("elapsed")
        self.elapsed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.elapsed.hide()
        self.countdown = QLabel("")
        self.countdown.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.defer_choice = QComboBox()
        self.run_label = QLabel("Run in")
        self.defer_button = QPushButton("Confirm")
        self.defer_button.setObjectName("primary")
        self.defer_choice.currentIndexChanged.connect(self._update_run_label)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(5)
        status_row.addWidget(self.elapsed)
        status_row.addWidget(self.status, stretch=1)
        status_row.addWidget(self.run_label)
        status_row.addWidget(self.defer_choice)
        status_row.addWidget(self.defer_button)
        status_row.addWidget(self.countdown)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addLayout(status_row)

        self.defer_button.clicked.connect(self._submit_defer)
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
        running = state.phase == "running"
        self.elapsed.setVisible(running)
        if running:
            self.elapsed.setText(_elapsed_text(state.started_at))
        show_panel = state.phase != "idle" or bool(state.last_output)
        self.setVisible(show_panel)
        response_open = state.phase == "awaiting_response"
        _set_widget_visible(self.countdown, response_open)
        if response_open:
            remaining = _remaining_seconds(state.response_deadline)
            if remaining is None:
                self.countdown.setText("Autostarts when the response window ends.")
            else:
                minutes, seconds = divmod(remaining, 60)
                self.countdown.setText(f"Autostarts in: {minutes}:{seconds:02d}")
            allowed_hours = available_reschedule_hours(
                self.policy,
                self.task_id,
                state.scheduled_for or state.next_scheduled_at,
                datetime.now().astimezone(),
            )
            options = [(_duration_label(hours), hours) for hours in allowed_hours]
            if self.allow_start_now:
                options.append(("now", "now"))
            existing_options = [
                (self.defer_choice.itemText(index), self.defer_choice.itemData(index))
                for index in range(self.defer_choice.count())
            ]
            if options != existing_options:
                selected = self.defer_choice.currentData()
                had_focus = self.defer_choice.hasFocus()
                signals_were_blocked = self.defer_choice.blockSignals(True)
                try:
                    self.defer_choice.clear()
                    for label, value in options:
                        self.defer_choice.addItem(label, value)
                    selected_index = self.defer_choice.findData(selected)
                    if selected_index >= 0:
                        self.defer_choice.setCurrentIndex(selected_index)
                    elif self.defer_choice.count():
                        self.defer_choice.setCurrentIndex(0)
                finally:
                    self.defer_choice.blockSignals(signals_were_blocked)
                self._update_run_label(self.defer_choice.currentIndex())
                if had_focus:
                    self.defer_choice.setFocus(Qt.FocusReason.OtherFocusReason)

            can_confirm = self.defer_choice.count() > 0
            _set_widget_visible(self.run_label, can_confirm)
            _set_widget_visible(self.defer_choice, can_confirm)
            _set_widget_visible(self.defer_button, can_confirm)
            for control in (self.defer_choice, self.defer_button):
                control.setEnabled(not self._request_sent)
            self.status.setText(f"{self.title}: Waiting")
            if self._request_sent:
                self.status.setText(f"{self.title}: response sent; waiting for the updater.")
        else:
            _set_widget_visible(self.run_label, False)
            _set_widget_visible(self.defer_choice, False)
            _set_widget_visible(self.defer_button, False)

    def _submit_defer(self) -> None:
        selection = self.defer_choice.currentData()
        if selection == "now":
            self._submit_start_now()
            return
        hours = selection
        if self._state.run_id is None or hours is None:
            return
        if int(hours) not in available_reschedule_hours(
            self.policy,
            self.task_id,
            self._state.scheduled_for or self._state.next_scheduled_at,
            datetime.now().astimezone(),
        ):
            return
        self._save(UserRequest(self._state.run_id, "defer", int(hours)))

    def _update_run_label(self, _index: int) -> None:
        self.run_label.setText("Run at" if self.defer_choice.currentData() == "now" else "Run in")

    def _submit_start_now(self) -> None:
        if not self.allow_start_now:
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
    system_retry_requested = Signal()

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
        self._status_error_dismissed = False
        self._system_retry_requested_for: str | None = None
        self._user_retry_requested_for: str | None = None
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
                task_id="system",
                allow_start_now=False,
            )
        self.user_panel = _TaskPanel(
            "Your user tools",
            self.policy,
            request_file,
            self,
            task_id="user-tools",
        )

        self.full_button = QPushButton("Full details")
        self.full_button.clicked.connect(self.full_mode_requested.emit)
        self.admin_button = QPushButton("Admin settings")
        self.admin_button.setVisible(admin_enabled)
        self.admin_button.clicked.connect(self.admin_requested.emit)
        self.close_button = QPushButton("Close")
        self.close_button.hide()
        self.system_retry_button = QPushButton("Retry system update")
        self.system_retry_button.setObjectName("retrySystem")
        self.system_retry_button.hide()
        self.user_retry_button = QPushButton("Retry user updates")
        self.user_retry_button.setObjectName("retryUserTools")
        self.user_retry_button.hide()
        self.system_retry_button.clicked.connect(self.system_retry_requested.emit)
        self.user_retry_button.clicked.connect(self._submit_user_retry)
        buttons = QHBoxLayout()
        buttons.addWidget(self.full_button)
        buttons.addWidget(self.admin_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        retry_buttons = QHBoxLayout()
        retry_buttons.addStretch(1)
        retry_buttons.addWidget(self.system_retry_button)
        retry_buttons.addWidget(self.user_retry_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.heading)
        layout.addWidget(self.message)
        if self.system_panel is not None:
            layout.addWidget(self.system_panel)
        layout.addWidget(self.user_panel)
        layout.addLayout(buttons)
        layout.addLayout(retry_buttons)

        self.close_button.clicked.connect(self.close)
        # Backwards-compatible control access for user-only clients and tests.
        self.defer_choice = self.user_panel.defer_choice
        self.defer_button = self.user_panel.defer_button
        self.retry_buttons = retry_buttons

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
        self._status_error_dismissed = False
        self.refresh()
        if self._phase != "idle":
            self.show()
            QTimer.singleShot(0, self._position_window)

    def refresh(self) -> None:
        try:
            user_state = self.state_store.load()
            system_state = self.system_state_store.load() if self.system_state_store else None
        except StateError as exc:
            previous_phase = self._phase
            self.heading.setText("Updater status unavailable")
            self.message.setText(str(exc))
            self._phase = "failed"
            self.close_button.setVisible(True)
            if previous_phase != self._phase:
                self.phase_changed.emit(self._phase)
            if not self._full_mode and not self._status_error_dismissed:
                self.show()
                self._fit_height_to_contents()
                if previous_phase != self._phase:
                    QTimer.singleShot(0, self._position_window)
            return

        self._status_error_dismissed = False
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
        if system_state is not None and (
            system_state.run_id != self._system_retry_requested_for
            or system_state.phase != "failed"
        ):
            self._system_retry_requested_for = None
        if user_state.run_id != self._user_retry_requested_for:
            self._user_retry_requested_for = None
        self._update_retry_button(
            self.system_retry_button,
            system_state,
            self.policy.retry.system.user_manual_attempts,
            self._system_retry_requested_for,
            "Retry system update",
        )
        self._update_retry_button(
            self.user_retry_button,
            user_state,
            self.policy.retry.user_tools.user_manual_attempts,
            self._user_retry_requested_for,
            "Retry user updates",
        )

        states = {"user-tools": user_state}
        if system_state is not None:
            states["system"] = system_state
        live_run_keys = {
            (task, state.run_id)
            for task, state in states.items()
            if state.phase in _DISMISSIBLE_PHASES
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

        self.close_button.setVisible(self._phase in {"deferred", "succeeded", "failed"})
        if self._phase == "idle":
            self.hide()
        elif not self._full_mode:
            dismissible_states = [
                (task, state)
                for task, state in states.items()
                if state.phase in _DISMISSIBLE_PHASES
            ]
            dismissed = bool(dismissible_states) and all(
                (task, state.run_id) in self._dismissed_runs for task, state in dismissible_states
            )
            if dismissed:
                self.hide()
            else:
                self.show()
                self._fit_height_to_contents()
                if previous_phase != self._phase:
                    QTimer.singleShot(0, self._position_window)

        if previous_phase != self._phase:
            self.phase_changed.emit(self._phase)
            if self._phase in _TERMINAL_PHASES:
                terminal_run_keys = frozenset(
                    (task, state.run_id)
                    for task, state in states.items()
                    if state.phase in _TERMINAL_PHASES
                )
                QTimer.singleShot(
                    30_000,
                    lambda phase=self._phase, keys=terminal_run_keys: self._hide_if_phase(
                        phase, keys
                    ),
                )

    def _position_window(self) -> None:
        self._placement.position_bottom_right()

    @staticmethod
    def _update_retry_button(
        button: QPushButton,
        state: UpdateState | None,
        retry_limit: int,
        pending_run_id: str | None,
        label: str,
    ) -> None:
        remaining = max(0, retry_limit - state.manual_retries_used) if state else 0
        visible = state is not None and state.phase == "failed" and remaining > 0
        button.setVisible(visible)
        if visible:
            pending = state.run_id is not None and state.run_id == pending_run_id
            button.setText("Retry requested" if pending else f"{label} ({remaining} left)")
            button.setEnabled(not pending)

    def _submit_user_retry(self) -> None:
        try:
            state = self.state_store.load()
            if (
                state.phase != "failed"
                or state.run_id is None
                or state.manual_retries_used >= self.policy.retry.user_tools.user_manual_attempts
            ):
                return
            save_request(self.user_panel.request_file, UserRequest(state.run_id, "retry"))
            self._user_retry_requested_for = state.run_id
            self.refresh()
        except (OSError, StateError, ValueError) as exc:
            self.user_panel.status.setText(f"Your user tools: could not request retry: {exc}")

    def _fit_height_to_contents(self) -> None:
        layout = self.layout()
        layout.activate()
        self.setMinimumHeight(0)
        self.resize(self.width(), layout.minimumSize().height())

    def _hide_if_phase(
        self,
        expected_phase: str,
        expected_run_keys: frozenset[tuple[str, str | None]] | None = None,
    ) -> None:
        if self._phase == expected_phase and not self._full_mode:
            if (
                expected_run_keys is not None
                and self._terminal_run_keys({task for task, _run_id in expected_run_keys})
                != expected_run_keys
            ):
                return
            self._dismiss_current_runs()
            self.hide()

    def _terminal_run_keys(
        self, tasks: set[str] | None = None
    ) -> frozenset[tuple[str, str | None]]:
        keys: set[tuple[str, str | None]] = set()
        for task, store in (
            ("system", self.system_state_store),
            ("user-tools", self.state_store),
        ):
            if store is None or (tasks is not None and task not in tasks):
                continue
            try:
                state = store.load()
            except StateError:
                continue
            if state.phase in _TERMINAL_PHASES:
                keys.add((task, state.run_id))
        return frozenset(keys)

    def _dismiss_current_runs(self) -> None:
        for task, store in (
            ("system", self.system_state_store),
            ("user-tools", self.state_store),
        ):
            if store is None:
                continue
            try:
                state = store.load()
            except StateError:
                continue
            if state.phase in _DISMISSIBLE_PHASES:
                self._dismissed_runs.add((task, state.run_id))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if self._phase in _DISMISSIBLE_PHASES:
            self._dismiss_current_runs()
        self._status_error_dismissed = True
        event.accept()


def _duration_label(hours: int) -> str:
    if hours < 24:
        return f"{hours}h"
    days, remainder = divmod(hours, 24)
    if remainder:
        return f"{days}d {remainder}h"
    return f"{days}d"


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


def _elapsed_text(started_at: str | None) -> str:
    if started_at is None:
        return "—:—:—"
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return "—:—:—"
    if started.tzinfo is None:
        return "—:—:—"
    elapsed = max(0, int((datetime.now(started.tzinfo) - started).total_seconds()))
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
