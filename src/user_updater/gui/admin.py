"""Polkit-gated administrator settings UI kept in the unprivileged desktop process."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Qt, QTime, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from ..policy import Policy, PolicyError, load_policy, policy_to_mapping, validate_policy_data
from .collapsible import CollapsibleSection

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TOOLS = ("flatpak", "pipx", "pip", "npm", "cargo", "gem")


class AdminHelperSession(QObject):
    """One polkit-authenticated root helper kept alive for the dialog lifetime."""

    ready = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        parent: QWidget,
        helper: str,
        pkexec: str | None,
    ) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self._helper = helper
        self._pkexec = pkexec
        self._stdout_buffer = bytearray()
        self._stderr_buffer = bytearray()
        self._callbacks: dict[int, Callable[[bool, str], None]] = {}
        self._next_request_id = 1
        self._ready = False
        self._closing = False
        self._failed = False
        self.finished.connect(self.deleteLater)

    @property
    def is_running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start(self) -> None:
        if self._pkexec is None:
            self.process.start(self._helper, ["session"])
        else:
            self.process.start(self._pkexec, [self._helper, "session"])

    def request(
        self,
        action: str,
        payload: dict[str, object],
        callback: Callable[[bool, str], None],
    ) -> None:
        if not self._ready or not self.is_running:
            callback(False, "The administrator session is not available.")
            return
        request_id = self._next_request_id
        self._next_request_id += 1
        encoded = (
            json.dumps({"id": request_id, "action": action, "payload": payload}) + "\n"
        ).encode("utf-8")
        if len(encoded) > 65_536:
            callback(False, "The administrator request is too large.")
            return
        self._callbacks[request_id] = callback
        if self.process.write(encoded) < 0:
            self._callbacks.pop(request_id, None)
            callback(False, "Could not send the administrator request.")

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self.is_running:
            self.process.closeWriteChannel()

    def _read_stdout(self) -> None:
        self._stdout_buffer.extend(bytes(self.process.readAllStandardOutput()))
        while b"\n" in self._stdout_buffer:
            line, _, self._stdout_buffer = self._stdout_buffer.partition(b"\n")
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._fail("The administrator helper returned an invalid response.")
                return
            if not isinstance(message, dict):
                self._fail("The administrator helper returned an invalid response.")
                return
            if message.get("event") == "ready":
                self._ready = True
                self.ready.emit()
                continue
            request_id = message.get("id")
            callback = self._callbacks.pop(request_id, None) if type(request_id) is int else None
            if callback is not None:
                callback(bool(message.get("ok")), str(message.get("message", "")))

    def _read_stderr(self) -> None:
        self._stderr_buffer.extend(bytes(self.process.readAllStandardError()))
        if len(self._stderr_buffer) > 65_536:
            del self._stderr_buffer[:-65_536]

    def _process_finished(self, exit_code: int, _exit_status) -> None:
        self._read_stdout()
        self._read_stderr()
        if not self._closing and not self._failed:
            message = self._stderr_buffer.decode("utf-8", errors="replace").strip()
            self._fail(message or f"The administrator session ended (status {exit_code}).")
        self._finish_pending("The administrator session closed before completing the action.")
        self.finished.emit()

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._fail(self.process.errorString())
            self.finished.emit()

    def _fail(self, message: str) -> None:
        if self._failed or self._closing:
            return
        self._failed = True
        if self.is_running:
            self.process.kill()
        self.failed.emit(message)
        self._finish_pending(message)

    def _finish_pending(self, message: str) -> None:
        callbacks, self._callbacks = self._callbacks, {}
        for callback in callbacks.values():
            callback(False, message)


class AdminSettingsDialog(QDialog):
    """Edit the constrained administrator policy without running Qt as root."""

    save_policy_requested = Signal(object)
    admin_action_requested = Signal(str)
    schedule_prompt_requested = Signal(str)

    def __init__(self, policy: Policy, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.policy = policy
        self.setWindowTitle("User Updater — Administrator settings")
        self.resize(520, 720)
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

        system_schedule = QGroupBox("System update schedule")
        system_form = QFormLayout(system_schedule)
        self.day_checks: dict[str, QCheckBox] = {}
        days_widget = QWidget()
        days_layout = QGridLayout(days_widget)
        days_layout.setContentsMargins(0, 0, 0, 0)
        days_layout.setHorizontalSpacing(14)
        days_layout.setVerticalSpacing(2)
        for index, day in enumerate(_DAYS):
            check = QCheckBox(day.title())
            check.setChecked(day in policy.schedule.days)
            self.day_checks[day] = check
            row, column = divmod(index, 4)
            days_layout.addWidget(check, row, column)
        self.system_time = QTimeEdit(QTime.fromString(policy.schedule.time, "HH:mm"))
        self.system_time.setDisplayFormat("HH:mm")
        self.system_response = _spin(policy.schedule.response_minutes, 1, 60)
        system_form.addRow("Days", days_widget)
        system_form.addRow("Local start time", self.system_time)
        system_form.addRow("Response window (minutes)", self.system_response)

        user_schedule = QGroupBox("Personal tool schedule")
        user_form = QFormLayout(user_schedule)
        self.user_day_checks: dict[str, QCheckBox] = {}
        user_days_widget = QWidget()
        user_days_layout = QGridLayout(user_days_widget)
        user_days_layout.setContentsMargins(0, 0, 0, 0)
        user_days_layout.setHorizontalSpacing(14)
        user_days_layout.setVerticalSpacing(2)
        for index, day in enumerate(_DAYS):
            check = QCheckBox(day.title())
            check.setChecked(day in policy.schedule.user_tools_days)
            self.user_day_checks[day] = check
            row, column = divmod(index, 4)
            user_days_layout.addWidget(check, row, column)
        self.user_tools_time = QTimeEdit(
            QTime.fromString(policy.schedule.user_tools_time, "HH:mm")
        )
        self.user_tools_time.setDisplayFormat("HH:mm")
        self.user_response = _spin(policy.schedule.user_tools_response_minutes, 1, 60)
        user_form.addRow("Days", user_days_widget)
        user_form.addRow("Local start time", self.user_tools_time)
        user_form.addRow("Response window (minutes)", self.user_response)
        user_note = QLabel(
            "Missed runs are coalesced and start once when that user next has a desktop session."
        )
        user_note.setWordWrap(True)
        user_form.addRow(user_note)
        user_schedule.setEnabled(policy.updates.run_user_tools)
        self.run_user_tools.toggled.connect(user_schedule.setEnabled)

        self.schedule_group = QGroupBox("Update schedules")
        schedules_layout = QVBoxLayout(self.schedule_group)
        schedules_layout.addWidget(system_schedule)
        schedules_layout.addWidget(user_schedule)
        form.addRow(self.schedule_group)

        network_group = QGroupBox("Network readiness")
        network_form = QFormLayout(network_group)
        self.internet_stability_seconds = _spin(policy.network.stable_seconds, 1, 3600)
        self.internet_stability_seconds.setObjectName("internetStabilitySeconds")
        network_form.addRow(
            "Stable internet before updates (seconds)", self.internet_stability_seconds
        )
        network_note = QLabel(
            "Internet must remain available for this long before users are notified and before "
            "system or personal-tool commands and hooks start. A failed check restarts the timer."
        )
        network_note.setWordWrap(True)
        network_form.addRow(network_note)
        form.addRow(network_group)

        window_group = QGroupBox("Window positioning")
        window_layout = QVBoxLayout(window_group)
        self.automatic_window_positioning = QCheckBox("Automatically position updater windows")
        self.automatic_window_positioning.setObjectName("automaticWindowPositioning")
        self.automatic_window_positioning.setChecked(policy.window.automatic_positioning)
        self.automatic_window_positioning.setToolTip(
            "Place compact and details windows at the bottom-right of the primary display."
        )
        window_note = QLabel(
            "Turn this off to let your desktop compositor or window rules choose the position. "
            "On layer-shell desktops, restart User Updater for the change to take full effect."
        )
        window_note.setWordWrap(True)
        window_layout.addWidget(self.automatic_window_positioning)
        window_layout.addWidget(window_note)
        form.addRow(window_group)

        scripts_group = QGroupBox("System update script hooks")
        scripts_layout = QVBoxLayout(scripts_group)
        before_group = QGroupBox("Before system update")
        before_layout = QVBoxLayout(before_group)
        self.before_builder_scripts = QCheckBox("Run scripts as the temporary builder")
        self.before_builder_scripts.setObjectName("beforeBuilderScripts")
        self.before_builder_scripts.setChecked(policy.system_scripts.before.builder)
        self.before_builder_scripts.setToolTip(
            "Run root-owned scripts in /etc/user-updater/scripts/before/builder.d/ "
            "as the temporary updater account."
        )
        self.before_root_scripts = QCheckBox("Run scripts as root")
        self.before_root_scripts.setObjectName("beforeRootScripts")
        self.before_root_scripts.setChecked(policy.system_scripts.before.root)
        self.before_root_scripts.setToolTip(
            "Run root-owned scripts in /etc/user-updater/scripts/before/root.d/ as root."
        )
        before_layout.addWidget(self.before_builder_scripts)
        before_layout.addWidget(self.before_root_scripts)
        after_group = QGroupBox("After the system update")
        after_layout = QVBoxLayout(after_group)
        self.after_builder_scripts = QCheckBox("Run scripts as the temporary builder")
        self.after_builder_scripts.setObjectName("afterBuilderScripts")
        self.after_builder_scripts.setChecked(policy.system_scripts.after.builder)
        self.after_builder_scripts.setToolTip(
            "Run root-owned scripts in /etc/user-updater/scripts/after/builder.d/ "
            "as the temporary updater account."
        )
        self.after_root_scripts = QCheckBox("Run scripts as root")
        self.after_root_scripts.setObjectName("afterRootScripts")
        self.after_root_scripts.setChecked(policy.system_scripts.after.root)
        self.after_root_scripts.setToolTip(
            "Run root-owned scripts in /etc/user-updater/scripts/after/root.d/ as root."
        )
        after_layout.addWidget(self.after_builder_scripts)
        after_layout.addWidget(self.after_root_scripts)
        scripts_layout.addWidget(before_group)
        scripts_layout.addWidget(after_group)
        scripts_note = QLabel(
            "Only executable, root-owned scripts that are not writable by other users run, "
            "in filename order. Order: root-before, builder-before, update, builder-after, "
            "root-after. A failing before script skips the update; after scripts run when the "
            "update attempt finishes. Root hooks execute with full root privileges."
        )
        scripts_note.setWordWrap(True)
        scripts_layout.addWidget(scripts_note)
        scripts_group.setEnabled(policy.updates.run_system)
        self.run_system.toggled.connect(scripts_group.setEnabled)
        form.addRow(scripts_group)

        retry_group = QGroupBox("Retry behavior")
        retry_layout = QVBoxLayout(retry_group)
        retry_note = QLabel(
            "Automatic retries run after a failure. User-requested retries are capped "
            "per scheduled update; system retries still require polkit authorization."
        )
        retry_note.setWordWrap(True)
        retry_layout.addWidget(retry_note)
        system_retry_group = QGroupBox("System updates")
        system_retry_form = QFormLayout(system_retry_group)
        self.system_automatic_retries = _spin(policy.retry.system.automatic_attempts, 0, 5)
        self.system_retry_delay = _spin(policy.retry.system.delay_minutes, 1, 1440)
        self.system_user_retries = _spin(policy.retry.system.user_manual_attempts, 0, 5)
        system_retry_form.addRow("Automatic retry attempts", self.system_automatic_retries)
        system_retry_form.addRow("Delay between attempts (minutes)", self.system_retry_delay)
        system_retry_form.addRow("User-requested retries per run", self.system_user_retries)
        retry_layout.addWidget(system_retry_group)

        user_retry_group = QGroupBox("Personal user tools")
        user_retry_form = QFormLayout(user_retry_group)
        self.user_tools_automatic_retries = _spin(policy.retry.user_tools.automatic_attempts, 0, 5)
        self.user_tools_retry_delay = _spin(policy.retry.user_tools.delay_minutes, 1, 1440)
        self.user_tools_user_retries = _spin(policy.retry.user_tools.user_manual_attempts, 0, 5)
        user_retry_form.addRow("Automatic retry attempts", self.user_tools_automatic_retries)
        user_retry_form.addRow("Delay between attempts (minutes)", self.user_tools_retry_delay)
        user_retry_form.addRow("User-requested retries per run", self.user_tools_user_retries)
        retry_layout.addWidget(user_retry_group)
        form.addRow(retry_group)

        testing_content = QGroupBox()
        testing_form = QFormLayout(testing_content)
        self.system_forced_failures = _spin(policy.testing.system.fail_next, 0, 100)
        self.user_forced_failures = _spin(policy.testing.user_tools.fail_next, 0, 100)
        self.system_forced_successes = _spin(policy.testing.system.succeed_next, 0, 100)
        self.user_forced_successes = _spin(policy.testing.user_tools.succeed_next, 0, 100)
        testing_form.addRow("Next system updates to force-fail", self.system_forced_failures)
        testing_form.addRow("Next user-tool updates to force-fail", self.user_forced_failures)
        testing_form.addRow("Next system updates to force-success", self.system_forced_successes)
        testing_form.addRow("Next user-tool updates to force-success", self.user_forced_successes)
        self.schedule_user_prompt_button = QPushButton("Schedule personal-tool update now")
        self.schedule_system_prompt_button = QPushButton("Schedule system update now")
        self.schedule_user_prompt_button.setToolTip(
            "Create a real personal-tool update prompt for each active desktop user. "
            "This replaces any existing postponement. Updates start after the response window "
            "unless a permitted delay is confirmed."
        )
        self.schedule_system_prompt_button.setToolTip(
            "Create a real system-update prompt. Updates start after the response window "
            "unless a permitted delay is confirmed. This replaces any existing postponement."
        )
        self.schedule_user_prompt_button.setEnabled(policy.updates.run_user_tools)
        self.schedule_system_prompt_button.setEnabled(policy.updates.run_system)
        self.run_user_tools.toggled.connect(self.schedule_user_prompt_button.setEnabled)
        self.run_system.toggled.connect(self.schedule_system_prompt_button.setEnabled)
        self.schedule_user_prompt_button.clicked.connect(
            lambda: self.schedule_prompt_requested.emit("user-tools")
        )
        self.schedule_system_prompt_button.clicked.connect(
            lambda: self.schedule_prompt_requested.emit("system")
        )
        schedule_buttons = QWidget(testing_content)
        schedule_buttons_layout = QVBoxLayout(schedule_buttons)
        schedule_buttons_layout.setContentsMargins(0, 0, 0, 0)
        schedule_buttons_layout.setSpacing(4)
        schedule_buttons_layout.addWidget(self.schedule_user_prompt_button)
        schedule_buttons_layout.addWidget(self.schedule_system_prompt_button)
        testing_form.addRow("Schedule a due update", schedule_buttons)
        testing_note = QLabel(
            "Save a forced success/failure outcome before scheduling a test; it skips update "
            "commands when the run starts. Without one, this schedules a real due update: it "
            "appears in the compact updater and starts when its response window ends unless a "
            "permitted delay is confirmed, replacing any previous postponement. Personal-tool "
            "prompts are scheduled for all active desktop users."
        )
        testing_note.setWordWrap(True)
        testing_form.addRow(testing_note)
        self.testing_section = CollapsibleSection("Testing", testing_content, expanded=False)
        self.testing_section.setObjectName("testingSection")
        self.testing_section.content.setObjectName("testingSectionContent")
        form.addRow(self.testing_section)

        interaction = QGroupBox("Rescheduling")
        interaction_form = QFormLayout(interaction)
        self.allow_deferral = QCheckBox("Allow users to reschedule personal-tool updates")
        self.allow_deferral.setChecked(policy.interaction.allow_deferral)
        self.max_user_tools_deferral = _spin(
            policy.interaction.max_user_tools_deferral_hours, 1, 168
        )
        self.allow_system_deferral = QCheckBox("Allow users to reschedule system updates")
        self.allow_system_deferral.setChecked(policy.interaction.allow_system_deferral)
        self.max_system_deferral = _spin(policy.interaction.max_system_deferral_hours, 1, 168)
        self.deferral_options = QLineEdit(
            ", ".join(str(value) for value in policy.interaction.deferral_options_hours)
        )
        interaction_form.addRow(self.allow_deferral)
        interaction_form.addRow("Personal-tool time limit (hours)", self.max_user_tools_deferral)
        interaction_form.addRow(self.allow_system_deferral)
        interaction_form.addRow("System-update time limit (hours)", self.max_system_deferral)
        interaction_form.addRow("Allowed delays (hours)", self.deferral_options)
        form.addRow(interaction)

        tools_group = QGroupBox("Personal tools available to users")
        tools_layout = QVBoxLayout(tools_group)
        tools_widget = QWidget(tools_group)
        tool_checks_layout = QHBoxLayout(tools_widget)
        tool_checks_layout.setContentsMargins(0, 0, 0, 0)
        self.tool_checks: dict[str, QCheckBox] = {}
        for tool in _TOOLS:
            check = QCheckBox(tool)
            check.setChecked(tool in policy.updates.user_tools)
            self.tool_checks[tool] = check
            tool_checks_layout.addWidget(check)
        tools_layout.addWidget(tools_widget)
        tools_layout.addWidget(self.allow_custom_tools)
        scripts_note = QLabel(
            "When enabled, executable scripts in each user's "
            "~/.config/user-updater/tools.d directory run as that user during "
            "personal-tool updates."
        )
        scripts_note.setWordWrap(True)
        tools_layout.addWidget(scripts_note)
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
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.run_system_now_button = QPushButton("Run system updates now")
        self.run_system_now_button.setObjectName("runSystemNow")
        self.run_system_now_button.setEnabled(policy.updates.run_system)
        self.run_system_now_button.clicked.connect(
            lambda: self.admin_action_requested.emit("run-system-now")
        )
        self.run_system.toggled.connect(self.run_system_now_button.setEnabled)
        buttons.addButton(self.run_system_now_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.run_user_tools_now_button = QPushButton("Run user updates now")
        self.run_user_tools_now_button.setObjectName("runUserToolsNow")
        self.run_user_tools_now_button.setToolTip(
            "Run personal-tool updates for all active local desktop users."
        )
        self.run_user_tools_now_button.setEnabled(policy.updates.run_user_tools)
        self.run_user_tools_now_button.clicked.connect(
            lambda: self.admin_action_requested.emit("run-user-tools-now")
        )
        self.run_user_tools.toggled.connect(self.run_user_tools_now_button.setEnabled)
        buttons.addButton(self.run_user_tools_now_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(buttons)

    def policy_mapping(self) -> dict[str, object]:
        selected_days = tuple(day for day in _DAYS if self.day_checks[day].isChecked())
        if not selected_days:
            raise PolicyError("select at least one system update day")
        selected_user_days = tuple(
            day for day in _DAYS if self.user_day_checks[day].isChecked()
        )
        if not selected_user_days:
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
                days=selected_days,
                time=self.system_time.time().toString("HH:mm"),
                response_minutes=self.system_response.value(),
                user_tools_days=selected_user_days,
                user_tools_time=self.user_tools_time.time().toString("HH:mm"),
                user_tools_response_minutes=self.user_response.value(),
            ),
            network=replace(
                self.policy.network,
                stable_seconds=self.internet_stability_seconds.value(),
            ),
            interaction=replace(
                self.policy.interaction,
                allow_deferral=self.allow_deferral.isChecked(),
                allow_system_deferral=self.allow_system_deferral.isChecked(),
                deferral_options_hours=options,
                max_user_tools_deferral_hours=self.max_user_tools_deferral.value(),
                max_system_deferral_hours=self.max_system_deferral.value(),
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
            testing=replace(
                self.policy.testing,
                system=replace(
                    self.policy.testing.system,
                    fail_next=self.system_forced_failures.value(),
                    succeed_next=self.system_forced_successes.value(),
                ),
                user_tools=replace(
                    self.policy.testing.user_tools,
                    fail_next=self.user_forced_failures.value(),
                    succeed_next=self.user_forced_successes.value(),
                ),
            ),
            window=replace(
                self.policy.window,
                automatic_positioning=self.automatic_window_positioning.isChecked(),
            ),
            system_scripts=replace(
                self.policy.system_scripts,
                before=replace(
                    self.policy.system_scripts.before,
                    builder=self.before_builder_scripts.isChecked(),
                    root=self.before_root_scripts.isChecked(),
                ),
                after=replace(
                    self.policy.system_scripts.after,
                    builder=self.after_builder_scripts.isChecked(),
                    root=self.after_root_scripts.isChecked(),
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
            mapping = self.policy_mapping()
        except PolicyError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return
        self.save_policy_requested.emit(mapping)


def open_admin_settings(
    parent: QWidget,
    policy_file: Path,
    on_saved,
) -> None:
    """Use one polkit-authenticated helper session for the entire settings dialog."""
    existing_dialog = getattr(parent, "_admin_settings_dialog", None)
    if existing_dialog is not None and existing_dialog.isVisible():
        existing_dialog.raise_()
        existing_dialog.activateWindow()
        return
    existing_session = getattr(parent, "_admin_settings_session", None)
    if existing_session is not None and existing_session.is_running:
        return

    try:
        pkexec = None
        if os.geteuid() != 0:
            pkexec = shutil.which("pkexec")
            if pkexec is None:
                raise RuntimeError("pkexec is required to open administrator settings")
        helper = _trusted_admin_helper()
        session = AdminHelperSession(parent, helper, pkexec)
        parent._admin_settings_session = session

        def clear_session() -> None:
            if getattr(parent, "_admin_settings_session", None) is session:
                parent._admin_settings_session = None

        def show_dialog() -> None:
            if getattr(parent, "_admin_settings_session", None) is not session:
                return
            try:
                dialog = AdminSettingsDialog(load_policy(policy_file))
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(parent, "Administrator settings", str(exc))
                session.close()
                return

            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            parent._admin_settings_dialog = dialog

            def save_policy(mapping: object) -> None:
                dialog.save_button.setEnabled(False)

                def saved(ok: bool, message: str) -> None:
                    if not ok:
                        dialog.save_button.setEnabled(True)
                        QMessageBox.warning(
                            dialog,
                            "Administrator settings",
                            message or "Could not save administrator policy.",
                        )
                        return
                    try:
                        on_saved(load_policy(policy_file))
                    except (OSError, RuntimeError, ValueError) as exc:
                        dialog.save_button.setEnabled(True)
                        QMessageBox.warning(dialog, "Administrator settings", str(exc))
                        return
                    dialog.accept()

                session.request("write-policy", {"mapping": mapping}, saved)

            def action_finished(ok: bool, message: str) -> None:
                if ok:
                    parent.refresh()
                else:
                    QMessageBox.warning(
                        dialog,
                        "Administrator action",
                        message or "The administrator action failed.",
                    )

            dialog.save_policy_requested.connect(save_policy)
            dialog.admin_action_requested.connect(
                lambda action: session.request(action, {}, action_finished)
            )
            dialog.schedule_prompt_requested.connect(
                lambda task_id: session.request(
                    "schedule-prompt",
                    {"task_id": task_id},
                    action_finished,
                )
            )

            def clear_dialog(_result: int) -> None:
                if getattr(parent, "_admin_settings_dialog", None) is dialog:
                    parent._admin_settings_dialog = None
                session.close()

            dialog.finished.connect(clear_dialog)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

        def session_failed(message: str) -> None:
            clear_session()
            dialog = getattr(parent, "_admin_settings_dialog", None)
            QMessageBox.warning(
                dialog if dialog is not None else parent,
                "Administrator settings",
                message or "Could not start the polkit administrator session.",
            )

        session.ready.connect(show_dialog)
        session.failed.connect(session_failed)
        session.finished.connect(clear_session)
        session.start()
    except (OSError, RuntimeError, ValueError) as exc:
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


def _trusted_admin_helper() -> str:
    """Resolve the dedicated root-owned helper covered by the app's polkit action."""
    executable_path = _admin_helper_path()
    candidate = executable_path.resolve(strict=True)
    installed_module = Path(__file__).resolve(strict=True)
    for installed_file in (candidate, installed_module):
        for path in (installed_file, *installed_file.parents):
            metadata = path.stat()
            if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise RuntimeError(
                    "administrator actions require a root-owned, non-writable system installation"
                )
    # Preserve the installed entry-point path: polkit matches the path passed to pkexec.
    return str(executable_path)


def _admin_helper_path() -> Path:
    """Use the helper beside the GUI's entry point so its path matches the polkit rule."""
    executable = Path(sys.argv[0])
    if not executable.is_absolute():
        located = shutil.which(str(executable))
        executable = Path(located) if located is not None else Path.cwd() / executable
    return executable.absolute().with_name("user-updater-admin")
