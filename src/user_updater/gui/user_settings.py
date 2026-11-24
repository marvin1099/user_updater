"""Personal schedule settings for a user-only installation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QMessageBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..policy import Policy, PolicyError, policy_to_mapping, validate_policy_data, write_policy

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class UserScheduleDialog(QDialog):
    """Let a user edit only the personal-tool weekdays and local start time."""

    def __init__(self, policy: Policy, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.policy = policy
        self.updated_policy: Policy | None = None
        self.setWindowTitle("Personal schedule settings")
        self.resize(420, 280)

        form = QFormLayout()
        days_widget = QWidget(self)
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
        form.addRow("Days", days_widget)

        self.start_time = QTimeEdit(QTime.fromString(policy.schedule.user_tools_time, "HH:mm"))
        self.start_time.setDisplayFormat("HH:mm")
        form.addRow("Local start time", self.start_time)

        note = QLabel(
            "Times use this computer's local timezone. Missed runs are coalesced and run "
            "when you next have a desktop session."
        )
        note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def policy_from_controls(self) -> Policy:
        days = tuple(day for day in _DAYS if self.day_checks[day].isChecked())
        if not days:
            raise PolicyError("select at least one personal-tool update day")
        updated = replace(
            self.policy,
            schedule=replace(
                self.policy.schedule,
                user_tools_days=days,
                user_tools_time=self.start_time.time().toString("HH:mm"),
            ),
        )
        return validate_policy_data(policy_to_mapping(updated))

    def _validate_and_accept(self) -> None:
        try:
            self.updated_policy = self.policy_from_controls()
        except PolicyError as exc:
            QMessageBox.warning(self, "Invalid schedule", str(exc))
            return
        self.accept()


def save_user_schedule_policy(path: Path, policy: Policy) -> Policy:
    """Persist the user's policy privately, without invoking administrator tools."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return write_policy(path, policy_to_mapping(policy), file_mode=0o600)


def open_user_schedule_settings(
    parent: QWidget,
    policy_file: Path,
    policy: Policy,
    on_saved: Callable[[Policy], None],
) -> None:
    """Show and save the unprivileged user's personal schedule editor."""
    dialog = UserScheduleDialog(policy, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.updated_policy is None:
        return
    try:
        updated = save_user_schedule_policy(policy_file, dialog.updated_policy)
    except (OSError, PolicyError) as exc:
        QMessageBox.warning(parent, "Could not save schedule", str(exc))
        return
    on_saved(updated)
