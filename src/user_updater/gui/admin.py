"""Polkit-gated administrator settings UI kept in the unprivileged desktop process."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QProcess, QTime, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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

from ..policy import Policy, PolicyError, load_policy, policy_to_mapping, validate_policy_data

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TOOLS = ("flatpak", "pipx", "pip", "npm", "cargo", "gem")


class AdminSettingsDialog(QDialog):
    """Edit the constrained administrator policy without running Qt as root."""

    def __init__(self, policy: Policy, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.policy = policy
        self.setWindowTitle("User Updater — Administrator settings")
        self.resize(560, 720)
        content = QWidget(self)
        form = QFormLayout(content)

        self.run_system = QCheckBox("Run machine-wide system updates")
        self.run_system.setChecked(policy.updates.run_system)
        self.run_user_tools = QCheckBox("Update logged-in users' personal tools")
        self.run_user_tools.setChecked(policy.updates.run_user_tools)
        self.allow_custom_tools = QCheckBox("Allow user-provided update scripts in tools.d")
        self.allow_custom_tools.setChecked(policy.updates.allow_custom_user_tools)
        form.addRow(self.run_system)
        form.addRow(self.run_user_tools)
        form.addRow(self.allow_custom_tools)
        scripts_note = QLabel(
            "When enabled, executable scripts in each user's "
            "~/.config/user_updater/tools.d directory run as that user during "
            "personal-tool updates."
        )
        scripts_note.setWordWrap(True)
        form.addRow(scripts_note)

        system_schedule = QGroupBox("System update schedule")
        system_form = QFormLayout(system_schedule)
        self.day_checks: dict[str, QCheckBox] = {}
        days_widget = QWidget()
        days_layout = QHBoxLayout(days_widget)
        days_layout.setContentsMargins(0, 0, 0, 0)
        for day in _DAYS:
            check = QCheckBox(day.title())
            check.setChecked(day in policy.schedule.days)
            self.day_checks[day] = check
            days_layout.addWidget(check)
        self.system_time = QTimeEdit(QTime.fromString(policy.schedule.time, "HH:mm"))
        self.system_time.setDisplayFormat("HH:mm")
        self.system_response = _spin(policy.schedule.response_minutes, 1, 60)
        system_form.addRow("Days", days_widget)
        system_form.addRow("Local start time", self.system_time)
        system_form.addRow("Response window (minutes)", self.system_response)
        form.addRow(system_schedule)

        user_schedule = QGroupBox("Personal user-tool schedule")
        user_form = QFormLayout(user_schedule)
        self.user_interval = _spin(policy.schedule.user_tools_interval_hours, 1, 720)
        self.user_response = _spin(policy.schedule.user_tools_response_minutes, 1, 60)
        user_form.addRow("Repeat interval (hours)", self.user_interval)
        user_form.addRow("Response window (minutes)", self.user_response)
        form.addRow(user_schedule)

        retry_group = QGroupBox("Retry behavior")
        retry_layout = QVBoxLayout(retry_group)
        retry_layout.addWidget(
            QLabel(
                "Automatic retries run after a failure. User-requested retries are capped "
                "per scheduled update; system retries still require polkit authorization."
            )
        )
        system_retry_group = QGroupBox("System updates")
        system_retry_form = QFormLayout(system_retry_group)
        self.system_automatic_retries = _spin(
            policy.retry.system.automatic_attempts, 0, 5
        )
        self.system_retry_delay = _spin(policy.retry.system.delay_minutes, 1, 1440)
        self.system_user_retries = _spin(policy.retry.system.user_manual_attempts, 0, 5)
        system_retry_form.addRow("Automatic retry attempts", self.system_automatic_retries)
        system_retry_form.addRow("Delay between attempts (minutes)", self.system_retry_delay)
        system_retry_form.addRow("User-requested retries per run", self.system_user_retries)
        retry_layout.addWidget(system_retry_group)

        user_retry_group = QGroupBox("Personal user tools")
        user_retry_form = QFormLayout(user_retry_group)
        self.user_tools_automatic_retries = _spin(
            policy.retry.user_tools.automatic_attempts, 0, 5
        )
        self.user_tools_retry_delay = _spin(policy.retry.user_tools.delay_minutes, 1, 1440)
        self.user_tools_user_retries = _spin(policy.retry.user_tools.user_manual_attempts, 0, 5)
        user_retry_form.addRow("Automatic retry attempts", self.user_tools_automatic_retries)
        user_retry_form.addRow("Delay between attempts (minutes)", self.user_tools_retry_delay)
        user_retry_form.addRow("User-requested retries per run", self.user_tools_user_retries)
        retry_layout.addWidget(user_retry_group)
        form.addRow(retry_group)

        interaction = QGroupBox("Rescheduling")
        interaction_form = QFormLayout(interaction)
        self.allow_deferral = QCheckBox("Allow users to reschedule personal-tool updates")
        self.allow_deferral.setChecked(policy.interaction.allow_deferral)
        self.deferral_options = QLineEdit(
            ", ".join(str(value) for value in policy.interaction.deferral_options_hours)
        )
        self.max_deferrals = _spin(policy.interaction.max_deferrals_per_run, 0, 10)
        interaction_form.addRow(self.allow_deferral)
        interaction_form.addRow("Allowed delays (hours)", self.deferral_options)
        interaction_form.addRow("Maximum per scheduled run", self.max_deferrals)
        form.addRow(interaction)

        tools_group = QGroupBox("Personal tools available to users")
        tools_layout = QHBoxLayout(tools_group)
        self.tool_checks: dict[str, QCheckBox] = {}
        for tool in _TOOLS:
            check = QCheckBox(tool)
            check.setChecked(tool in policy.updates.user_tools)
            self.tool_checks[tool] = check
            tools_layout.addWidget(check)
        form.addRow(tools_group)
        tools_note = QLabel(
            "This is the administrator allow-list. Users may turn off any listed tool "
            "for their own account, but cannot enable tools removed here."
        )
        tools_note.setWordWrap(True)
        form.addRow(tools_note)

        self.builder_sudo_mode = QComboBox()
        self.builder_sudo_mode.addItem("Package manager only (recommended)", "package-manager")
        self.builder_sudo_mode.addItem("Unrestricted passwordless sudo", "unrestricted")
        self.builder_sudo_mode.setCurrentIndex(
            self.builder_sudo_mode.findData(policy.security.builder_sudo_mode)
        )
        form.addRow("Temporary system updater permissions", self.builder_sudo_mode)
        warning = QLabel(
            "Unrestricted sudo allows the temporary updater account to run any command as root."
        )
        warning.setWordWrap(True)
        form.addRow(warning)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.run_system_now_button = QPushButton("Run system updates now")
        self.run_system_now_button.setObjectName("runSystemNow")
        self.run_system_now_button.setEnabled(policy.updates.run_system)
        self.run_system_now_button.clicked.connect(lambda: start_system_update_now(self))
        self.run_system.toggled.connect(self.run_system_now_button.setEnabled)
        buttons.addButton(
            self.run_system_now_button, QDialogButtonBox.ButtonRole.ActionRole
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(buttons)

    def policy_mapping(self) -> dict[str, object]:
        selected_days = tuple(day for day in _DAYS if self.day_checks[day].isChecked())
        if not selected_days:
            raise PolicyError("select at least one system update day")
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
                days=selected_days,
                time=self.system_time.time().toString("HH:mm"),
                response_minutes=self.system_response.value(),
                user_tools_interval_hours=self.user_interval.value(),
                user_tools_response_minutes=self.user_response.value(),
            ),
            interaction=replace(
                self.policy.interaction,
                allow_deferral=self.allow_deferral.isChecked(),
                deferral_options_hours=options,
                max_deferrals_per_run=self.max_deferrals.value(),
            ),
            updates=replace(
                self.policy.updates,
                run_system=self.run_system.isChecked(),
                run_user_tools=self.run_user_tools.isChecked(),
                user_tools=tuple(tool for tool in _TOOLS if self.tool_checks[tool].isChecked()),
                allow_custom_user_tools=self.allow_custom_tools.isChecked(),
            ),
            retry=replace(
                self.policy.retry,
                system=replace(
                    self.policy.retry.system,
                    automatic_attempts=self.system_automatic_retries.value(),
                    delay_minutes=self.system_retry_delay.value(),
                    user_manual_attempts=self.system_user_retries.value(),
                ),
                user_tools=replace(
                    self.policy.retry.user_tools,
                    automatic_attempts=self.user_tools_automatic_retries.value(),
                    delay_minutes=self.user_tools_retry_delay.value(),
                    user_manual_attempts=self.user_tools_user_retries.value(),
                ),
            ),
            security=replace(
                self.policy.security,
                builder_sudo_mode=str(self.builder_sudo_mode.currentData()),
            ),
        )
        return policy_to_mapping(validate_policy_data(policy_to_mapping(updated)))

    def _validate_and_accept(self) -> None:
        try:
            self.policy_mapping()
        except PolicyError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return
        self.accept()


def open_admin_settings(
    parent: QWidget,
    policy_file: Path,
    on_saved,
) -> None:
    """Authenticate before showing settings, then save through the narrow root helper."""
    try:
        executable = _trusted_system_executable()
        pkexec = shutil.which("pkexec")
        if os.geteuid() != 0:
            if pkexec is None:
                raise RuntimeError("pkexec is required to open administrator settings")
            authentication = subprocess.run(
                [pkexec, executable, "admin-auth-check"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if authentication.returncode != 0:
                detail = (
                    authentication.stderr.strip() or "Administrator authorization was cancelled."
                )
                QMessageBox.information(parent, "Administrator settings", detail)
                return

        policy = load_policy(policy_file)
        dialog = AdminSettingsDialog(policy, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        mapping = dialog.policy_mapping()

        if os.geteuid() == 0:
            saved = validate_policy_data(mapping)
            from ..policy import write_policy

            write_policy(policy_file, mapping)
            on_saved(saved)
            return

        assert pkexec is not None
        result = subprocess.run(
            [pkexec, executable, "admin-policy-write"],
            input=json.dumps(mapping),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "Could not save administrator policy."
            QMessageBox.warning(parent, "Administrator settings", detail)
            return
        on_saved(load_policy(policy_file))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        QMessageBox.warning(parent, "Administrator settings", str(exc))


def start_system_retry(parent: QWidget) -> None:
    """Start a detached, polkit-authorized retry of a failed system task."""
    try:
        executable = _trusted_system_executable()
        pkexec = shutil.which("pkexec")
        if pkexec is None:
            raise RuntimeError("pkexec is required to retry machine-wide updates")
    except (OSError, RuntimeError, ValueError) as exc:
        QMessageBox.warning(parent, "System update retry", str(exc))
        return

    try:
        state = parent.system_state_store.load()
    except (AttributeError, OSError, ValueError) as exc:
        QMessageBox.warning(parent, "System update retry", str(exc))
        return

    started, pid = QProcess.startDetached(pkexec, [executable, "retry", "--system"])
    if not started:
        QMessageBox.warning(parent, "System update retry", "Could not start pkexec.")
        return
    pending_run_id = state.run_id
    parent._system_retry_requested_for = pending_run_id
    parent.refresh()
    monitor = QTimer(parent)
    monitor.setInterval(750)

    def check_process() -> None:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            monitor.stop()
            monitor.deleteLater()
            if getattr(parent, "_system_retry_requested_for", None) == pending_run_id:
                parent._system_retry_requested_for = None
                parent.refresh()
        except PermissionError:
            pass

    monitor.timeout.connect(check_process)
    monitor.start()


def start_system_update_now(parent: QWidget) -> None:
    """Start a fresh machine-wide update after polkit authorization."""
    try:
        executable = _trusted_system_executable()
        pkexec = shutil.which("pkexec")
        if pkexec is None:
            raise RuntimeError("pkexec is required to start machine-wide updates")
    except (OSError, RuntimeError, ValueError) as exc:
        QMessageBox.warning(parent, "System updates", str(exc))
        return

    started, _pid = QProcess.startDetached(pkexec, [executable, "run-now", "--system"])
    if not started:
        QMessageBox.warning(parent, "System updates", "Could not start pkexec.")
        return
    QMessageBox.information(
        parent,
        "System updates",
        "Machine-wide updates were started. Follow progress in Full details.",
    )


def _spin(value: int, minimum: int, maximum: int) -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    return widget


def _trusted_system_executable() -> str:
    """Refuse to ask pkexec to run a user-writable updater installation."""
    candidate = Path(sys.argv[0])
    if not candidate.is_absolute():
        located = shutil.which(str(candidate))
        candidate = Path(located) if located else Path.cwd() / candidate
    candidate = candidate.resolve(strict=True)
    installed_module = Path(__file__).resolve(strict=True)
    for installed_file in (candidate, installed_module):
        for path in (installed_file, *installed_file.parents):
            metadata = path.stat()
            if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise RuntimeError(
                    "administrator settings require a root-owned, non-writable system installation"
                )
    return str(candidate)
