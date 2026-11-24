"""Personal update settings for a user-only installation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..policy import Policy, PolicyError, policy_to_mapping, validate_policy_data, write_policy

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class UserSettingsDialog(QDialog):
    """Let a user edit settings that affect only their personal-tool updates."""

    run_user_now_requested = Signal()
    schedule_user_prompt_requested = Signal()

    def __init__(self, policy: Policy, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.policy = policy
        self.updated_policy: Policy | None = None
        self.setWindowTitle("User settings")
        self.resize(520, 620)

        content = QWidget(self)
        form = QFormLayout(content)
        self.run_user_tools = QCheckBox("Enable automatic personal-tool updates")
        self.run_user_tools.setChecked(policy.updates.run_user_tools)
        form.addRow(self.run_user_tools)

        schedule_group = QGroupBox("Personal-tool schedule", content)
        schedule_form = QFormLayout(schedule_group)
        days_widget = QWidget(schedule_group)
        days_layout = QGridLayout(days_widget)
        days_layout.setContentsMargins(0, 0, 0, 0)
        days_layout.setHorizontalSpacing(14)
        days_layout.setVerticalSpacing(2)
        self.day_checks: dict[str, QCheckBox] = {}
        for index, day in enumerate(_DAYS):
            check = QCheckBox(day.title(), days_widget)
            check.setChecked(day in policy.schedule.user_tools_days)
            self.day_checks[day] = check
            row, column = divmod(index, 4)
            days_layout.addWidget(check, row, column)
        schedule_form.addRow("Days", days_widget)

        self.start_time = QTimeEdit(QTime.fromString(policy.schedule.user_tools_time, "HH:mm"))
        self.start_time.setDisplayFormat("HH:mm")
        schedule_form.addRow("Local start time", self.start_time)
        self.response_minutes = _spin(policy.schedule.user_tools_response_minutes, 1, 60)
        schedule_form.addRow("Response window (minutes)", self.response_minutes)
        form.addRow(schedule_group)

        network_group = QGroupBox("Internet readiness")
        network_form = QFormLayout(network_group)
        self.internet_stability_seconds = _spin(policy.network.stable_seconds, 1, 3600)
        network_form.addRow(
            "Stable internet before updates (seconds)", self.internet_stability_seconds
        )
        network_note = QLabel(
            "Users are notified only after internet has been stable for this long. If it "
            "drops after confirmation, updates wait for a stable connection again."
        )
        network_note.setWordWrap(True)
        network_form.addRow(network_note)
        form.addRow(network_group)

        rescheduling_group = QGroupBox("Rescheduling")
        rescheduling_form = QFormLayout(rescheduling_group)
        self.allow_deferral = QCheckBox("Allow me to reschedule personal-tool updates")
        self.allow_deferral.setChecked(policy.interaction.allow_deferral)
        self.deferral_options = QLineEdit(
            ", ".join(str(value) for value in policy.interaction.deferral_options_hours)
        )
        self.max_deferral_hours = _spin(policy.interaction.max_user_tools_deferral_hours, 1, 168)
        rescheduling_form.addRow(self.allow_deferral)
        rescheduling_form.addRow("Allowed delays (hours)", self.deferral_options)
        rescheduling_form.addRow("Maximum delay (hours)", self.max_deferral_hours)
        self.allow_deferral.toggled.connect(self.deferral_options.setEnabled)
        self.allow_deferral.toggled.connect(self.max_deferral_hours.setEnabled)
        self.deferral_options.setEnabled(self.allow_deferral.isChecked())
        self.max_deferral_hours.setEnabled(self.allow_deferral.isChecked())
        form.addRow(rescheduling_group)

        retry_group = QGroupBox("Retry behavior")
        retry_form = QFormLayout(retry_group)
        self.automatic_retries = _spin(policy.retry.user_tools.automatic_attempts, 0, 5)
        self.retry_delay_minutes = _spin(policy.retry.user_tools.delay_minutes, 1, 1440)
        self.manual_retries = _spin(policy.retry.user_tools.user_manual_attempts, 0, 5)
        retry_form.addRow("Automatic retry attempts", self.automatic_retries)
        retry_form.addRow("Delay between attempts (minutes)", self.retry_delay_minutes)
        retry_form.addRow("User-requested retries per update", self.manual_retries)
        form.addRow(retry_group)

        self.automatic_window_positioning = QCheckBox("Automatically position updater windows")
        self.automatic_window_positioning.setChecked(policy.window.automatic_positioning)
        self.automatic_window_positioning.setToolTip(
            "Place compact and details windows at the bottom-right of the primary display."
        )
        form.addRow("Window positioning", self.automatic_window_positioning)

        note = QLabel(
            "Times use this computer's local timezone. Missed runs are coalesced and run "
            "when you next have a desktop session. Choose enabled tools and custom scripts "
            "from Full details. Save settings before using the action buttons; they use the "
            "saved policy."
        )
        note.setWordWrap(True)
        form.addRow(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.run_user_now_button = QPushButton("Run personal-tool updates now")
        self.run_user_now_button.setObjectName("runUserToolsNow")
        self.run_user_now_button.setToolTip(
            "Bypass the schedule and response window. Updates still wait for stable internet."
        )
        self.run_user_now_button.setEnabled(policy.updates.run_user_tools)
        self.run_user_now_button.clicked.connect(self.run_user_now_requested.emit)
        self.test_prompt_button = QPushButton("Test: show update prompt now")
        self.test_prompt_button.setObjectName("scheduleUserPromptNow")
        self.test_prompt_button.setToolTip(
            "Creates a real update prompt now. Stable internet is required before it appears; "
            "the normal response window still applies and updates run if not rescheduled."
        )
        self.test_prompt_button.setEnabled(policy.updates.run_user_tools)
        self.test_prompt_button.clicked.connect(self.schedule_user_prompt_requested.emit)
        self.run_user_tools.toggled.connect(self.run_user_now_button.setEnabled)
        self.run_user_tools.toggled.connect(self.test_prompt_button.setEnabled)
        action_buttons = QHBoxLayout()
        action_buttons.addWidget(self.run_user_now_button)
        action_buttons.addWidget(self.test_prompt_button)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        layout.addLayout(action_buttons)
        layout.addWidget(buttons)

    def policy_from_controls(self) -> Policy:
        days = tuple(day for day in _DAYS if self.day_checks[day].isChecked())
        if not days:
            raise PolicyError("select at least one personal-tool update day")
        try:
            options = tuple(
                int(value.strip())
                for value in self.deferral_options.text().split(",")
                if value.strip()
            )
        except ValueError as exc:
            raise PolicyError("reschedule delays must be comma-separated whole hours") from exc
        updated = replace(
            self.policy,
            schedule=replace(
                self.policy.schedule,
                user_tools_days=days,
                user_tools_time=self.start_time.time().toString("HH:mm"),
                user_tools_response_minutes=self.response_minutes.value(),
            ),
            network=replace(
                self.policy.network,
                stable_seconds=self.internet_stability_seconds.value(),
            ),
            interaction=replace(
                self.policy.interaction,
                allow_deferral=self.allow_deferral.isChecked(),
                deferral_options_hours=options,
                max_user_tools_deferral_hours=self.max_deferral_hours.value(),
            ),
            updates=replace(
                self.policy.updates,
                run_user_tools=self.run_user_tools.isChecked(),
            ),
            retry=replace(
                self.policy.retry,
                user_tools=replace(
                    self.policy.retry.user_tools,
                    automatic_attempts=self.automatic_retries.value(),
                    delay_minutes=self.retry_delay_minutes.value(),
                    user_manual_attempts=self.manual_retries.value(),
                ),
            ),
            window=replace(
                self.policy.window,
                automatic_positioning=self.automatic_window_positioning.isChecked(),
            ),
        )
        return validate_policy_data(policy_to_mapping(updated))

    def _validate_and_accept(self) -> None:
        try:
            self.updated_policy = self.policy_from_controls()
        except PolicyError as exc:
            QMessageBox.warning(self, "Invalid user settings", str(exc))
            return
        self.accept()


def _spin(value: int, minimum: int, maximum: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    return spin


def save_user_settings_policy(path: Path, policy: Policy) -> Policy:
    """Persist the user's settings privately, without invoking administrator tools."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return write_policy(path, policy_to_mapping(policy), file_mode=0o600)


def open_user_settings(
    parent: QWidget,
    policy_file: Path,
    policy: Policy,
    on_saved: Callable[[Policy], None],
    *,
    on_run_now: Callable[[], None] | None = None,
    on_schedule_prompt: Callable[[], None] | None = None,
) -> None:
    """Show and save the unprivileged user's personal settings editor."""
    dialog = UserSettingsDialog(policy, parent)
    if on_run_now is not None:
        dialog.run_user_now_requested.connect(on_run_now)
    if on_schedule_prompt is not None:
        dialog.schedule_user_prompt_requested.connect(on_schedule_prompt)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.updated_policy is None:
        return
    try:
        updated = save_user_settings_policy(policy_file, dialog.updated_policy)
    except (OSError, PolicyError) as exc:
        QMessageBox.warning(parent, "Could not save user settings", str(exc))
        return
    on_saved(updated)
