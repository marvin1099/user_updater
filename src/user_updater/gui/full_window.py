"""Expanded read-only update console with collapsible task and history sections."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QPalette,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..history import HistoryError, RunHistoryStore, RunRecord, history_path_for_state
from ..logging_config import current_log_path
from ..requests import UserRequest, save_request
from ..state import StateError, StateStore, UpdateState
from ..tool_preferences import ToolPreferenceError, UserToolPreferenceStore
from .collapsible import CollapsibleSection
from .placement import WindowPlacement

_ACTIVE_PHASES = {"awaiting_response", "waiting_for_internet", "running", "deferred"}
_SGR_SEQUENCE = re.compile(r"\x1b\[([0-9;]*)m")
_ANSI_COLORS = (
    "#000000",
    "#aa0000",
    "#00aa00",
    "#aa5500",
    "#0000aa",
    "#aa00aa",
    "#00aaaa",
    "#aaaaaa",
    "#555555",
    "#ff5555",
    "#55ff55",
    "#ffff55",
    "#5555ff",
    "#ff55ff",
    "#55ffff",
    "#ffffff",
)
class _TaskTab(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.status = QLabel(f"{title}: waiting")
        self.elapsed = QLabel("Elapsed: —")
        self.next_run = QLabel("Next scheduled: —")
        self.retry_info = QLabel("")
        self.retry_info.setObjectName("automaticRetryInfo")
        self.retry_info.setWordWrap(True)
        self.retry_info.hide()
        self.tools_group = QGroupBox()
        self.tool_checks_widget = QWidget(self.tools_group)
        self.tool_check_layout = QHBoxLayout(self.tool_checks_widget)
        self.tool_check_layout.setContentsMargins(0, 0, 0, 0)
        self.tool_checkboxes: dict[str, QCheckBox] = {}
        self.custom_tools_checkbox = QCheckBox("Run my scripts from ~/.config/user-updater/tools.d")
        self.custom_tools_checkbox.setObjectName("customToolScripts")
        self.tool_preferences_status = QLabel("")
        self.save_tools_button = QPushButton("Save tool choices")
        tools_layout = QVBoxLayout(self.tools_group)
        tools_layout.addWidget(self.tool_checks_widget)
        tools_controls = QHBoxLayout()
        tools_controls.addWidget(self.custom_tools_checkbox)
        tools_controls.addWidget(self.tool_preferences_status, stretch=1)
        tools_controls.addWidget(self.save_tools_button)
        tools_layout.addLayout(tools_controls)
        self.tools_section = CollapsibleSection(
            "Personal tools enabled for your account",
            self.tools_group,
            expanded=False,
        )
        self.terminal = QPlainTextEdit()
        self._terminal_source = ""
        self.retry_button = QPushButton("Retry failed update")
        self.retry_button.setObjectName("retry")
        self.retry_button.hide()
        self.terminal.setReadOnly(True)
        self.terminal.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.terminal.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(self.status, stretch=1)
        heading.addWidget(self.elapsed)
        layout.addLayout(heading)
        layout.addWidget(self.next_run)
        layout.addWidget(self.retry_info)
        layout.addWidget(self.tools_section)
        layout.addWidget(self.terminal, stretch=1)
        controls = QHBoxLayout()
        controls.addStretch(1)
        controls.addWidget(self.retry_button)
        layout.addLayout(controls)

    def update_state(
        self,
        title: str,
        state: UpdateState,
        *,
        retry_enabled: bool = False,
        retry_remaining: int = 0,
        retry_pending: bool = False,
        automatic_retry_limit: int = 0,
    ) -> None:
        self.status.setText(f"{title}: {state.phase.replace('_', ' ').title()} — {state.message}")
        if retry_pending:
            self.status.setText(f"{title}: retry requested — waiting for the updater.")
        self.elapsed.setText(f"Elapsed: {_elapsed_text(state)}")
        self.next_run.setText(f"Next scheduled: {_format_datetime(state.next_scheduled_at)}")
        retry_info = ""
        if state.phase == "failed" and automatic_retry_limit > 0:
            if (
                state.retry_after is not None
                and state.automatic_retries_used < automatic_retry_limit
            ):
                retry_info = (
                    f"Automatic retry {state.automatic_retries_used + 1} of "
                    f"{automatic_retry_limit} scheduled for {_format_datetime(state.retry_after)}."
                )
            elif state.automatic_retries_used >= automatic_retry_limit:
                retry_info = (
                    f"Automatic retries exhausted "
                    f"({state.automatic_retries_used} of {automatic_retry_limit} used)."
                )
        self.retry_info.setText(retry_info)
        self.retry_info.setVisible(bool(retry_info))
        can_retry = retry_enabled and state.phase == "failed"
        self.retry_button.setVisible(can_retry)
        if can_retry:
            self.retry_button.setText(f"Retry failed update ({retry_remaining} left)")
        self.retry_button.setEnabled(not retry_pending)
        text = "\n".join(state.last_output[-200:])
        if text != self._terminal_source:
            self._terminal_source = text
            _render_terminal_text(self.terminal, text)


class FullDetailsWindow(QWidget):
    """Titleless, always-on-top details view; closing returns to the minimal window."""

    back_requested = Signal()
    all_updates_done = Signal()
    system_retry_requested = Signal()

    def __init__(
        self,
        system_state_store: StateStore | None,
        user_state_store: StateStore,
        *,
        system_enabled: bool = True,
        user_tools_enabled: bool = True,
        user_request_file: Path | None = None,
        log_file: Path | None = None,
        user_log_file: Path | None = None,
        system_output_visible: bool = True,
        system_manual_retry_limit: int = 0,
        user_manual_retry_limit: int = 0,
        system_automatic_retry_limit: int = 0,
        user_automatic_retry_limit: int = 0,
        admin_allowed_user_tools: tuple[str, ...] = (),
        admin_allows_custom_tools: bool = True,
        user_tool_preferences: UserToolPreferenceStore | None = None,
        system_history_store: RunHistoryStore | None = None,
        user_history_store: RunHistoryStore | None = None,
        positioning_enabled: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.system_state_store = system_state_store
        self.user_state_store = user_state_store
        self.system_enabled = system_enabled
        self.user_tools_enabled = user_tools_enabled
        self.system_output_visible = system_output_visible
        self.system_manual_retry_limit = system_manual_retry_limit
        self.user_manual_retry_limit = user_manual_retry_limit
        self.system_automatic_retry_limit = system_automatic_retry_limit
        self.user_automatic_retry_limit = user_automatic_retry_limit
        self.user_request_file = Path(user_request_file) if user_request_file else None
        self.log_file = Path(log_file) if log_file is not None else None
        self.user_log_file = Path(user_log_file) if user_log_file is not None else None
        self._active_log_date: date | None = None
        self._active_user_log_date: date | None = None
        self._history_text: str | None = None
        self.user_tool_preferences = user_tool_preferences
        self.admin_allowed_user_tools = tuple(admin_allowed_user_tools)
        self.admin_allows_custom_tools = admin_allows_custom_tools
        self.system_history_store = system_history_store or (
            RunHistoryStore(history_path_for_state(system_state_store.path), file_mode=0o644)
            if system_state_store is not None
            else None
        )
        self.user_history_store = user_history_store or RunHistoryStore(
            history_path_for_state(user_state_store.path), file_mode=user_state_store.file_mode
        )
        self._system_retry_requested_for: str | None = None
        self._user_retry_requested_for: str | None = None
        self._seen_active = False
        self._completion_emitted = False
        self._expanded_active_sections: frozenset[str] | None = None
        self.setWindowTitle("User Updater Details")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAutoFillBackground(True)
        self.resize(840, 680)
        self.setMinimumSize(640, 500)
        self._placement = WindowPlacement(
            self,
            title=self.windowTitle(),
            activate_on_show=True,
            enabled=positioning_enabled,
        )

        title = QLabel("Update details")
        title_font = QFont(title.font())
        title_font.setPointSize(max(title_font.pointSize() + 2, 12))
        title_font.setBold(True)
        title.setFont(title_font)
        self.system_tab = _TaskTab("System updates")
        self.system_tab.tools_section.hide()
        self.user_tab = _TaskTab("User tools")
        self.history_view = QPlainTextEdit()
        self.history_view.setObjectName("runHistory")
        self.history_view.setReadOnly(True)
        self.history_view.setPlaceholderText("No completed update runs recorded.")
        self.system_section = CollapsibleSection(
            "System updates", self.system_tab, header_inside=False, checkbox_header=True
        )
        self.system_section.header.setVisible(system_state_store is not None)
        self.system_section.setVisible(
            system_state_store is not None and self.system_section.is_expanded()
        )
        self.user_section = CollapsibleSection(
            "User tools", self.user_tab, header_inside=False, checkbox_header=True
        )
        self.history_section = CollapsibleSection(
            "Run history", self.history_view, header_inside=False, checkbox_header=True
        )
        self.empty_state = QLabel("Click on a checkbox above to see info")
        self.empty_state.setObjectName("emptyDetailsMessage")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for section in (self.system_section, self.user_section, self.history_section):
            section.header.setObjectName("detailsSectionToggle")
            section.header.toggled.connect(self._update_empty_state)
        self.system_section.header.toggled.connect(
            lambda expanded: expanded and self._scroll_terminal_to_bottom_left(self.system_tab)
        )
        self.user_section.header.toggled.connect(
            lambda expanded: expanded and self._scroll_terminal_to_bottom_left(self.user_tab)
        )
        self.user_tab.tools_section.header.toggled.connect(
            lambda expanded: expanded and self._scroll_terminal_to_bottom_left(self.user_tab)
        )
        section_tabs = QHBoxLayout()
        section_tabs.setContentsMargins(0, 0, 0, 0)
        section_tabs.setSpacing(4)
        section_tabs.addWidget(self.system_section.header)
        section_tabs.addWidget(self.user_section.header)
        section_tabs.addWidget(self.history_section.header)
        section_tabs.addStretch(1)
        sections_layout = QVBoxLayout()
        sections_layout.setContentsMargins(0, 0, 0, 0)
        sections_layout.setSpacing(6)
        sections_layout.addWidget(self.empty_state, alignment=Qt.AlignmentFlag.AlignTop)
        sections_layout.addWidget(self.system_section, stretch=1)
        sections_layout.addWidget(self.user_section, stretch=1)
        sections_layout.addWidget(self.history_section, stretch=1)
        self.system_tab.retry_button.clicked.connect(self.system_retry_requested.emit)
        self.user_tab.retry_button.clicked.connect(self._submit_user_retry)
        self.user_tab.save_tools_button.clicked.connect(self._save_user_tool_preferences)
        self._configure_user_tool_choices()
        self.back_button = QPushButton("Back to minimal view")
        self.back_button.clicked.connect(self._return_to_minimal)
        self.open_log_button = QPushButton("Open current log")
        self.open_log_button.setObjectName("openCurrentLog")
        self.open_log_button.setToolTip("Open the current updater log in your default viewer.")
        self.open_log_button.clicked.connect(self._open_current_log)
        self.open_user_log_button = QPushButton("Open last user-tools log")
        self.open_user_log_button.setObjectName("openUserToolsLog")
        self.open_user_log_button.setToolTip(
            "Open the private log containing output from your personal-tool updates."
        )
        self.open_user_log_button.setVisible(self.user_log_file is not None)
        self.open_user_log_button.clicked.connect(self._open_user_tools_log)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(title)
        layout.addLayout(section_tabs)
        layout.addLayout(sections_layout, stretch=1)
        controls = QHBoxLayout()
        controls.addStretch(1)
        controls.addWidget(self.back_button)
        controls.addWidget(self.open_log_button)
        if self.user_log_file is not None:
            controls.addWidget(self.open_user_log_button)
        layout.addLayout(controls)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(500)
        self.refresh()

    def refresh(self) -> None:
        system_state = self._read_state(
            self.system_state_store,
            self.system_enabled,
            include_output=self.system_output_visible,
        )
        user_state = self._read_state(self.user_state_store, self.user_tools_enabled)
        self._active_log_date = _active_log_date(system_state, user_state)
        self._active_user_log_date = _state_log_date(user_state)
        self._update_open_log_button()
        if user_state.run_id != self._user_retry_requested_for:
            self._user_retry_requested_for = None
        if self._system_retry_requested_for is not None and (
            system_state.run_id != self._system_retry_requested_for
            or system_state.phase != "failed"
        ):
            self._system_retry_requested_for = None
        self.system_tab.update_state(
            "System updates",
            system_state,
            retry_enabled=(
                self.system_enabled
                and system_state.manual_retries_used < self.system_manual_retry_limit
            ),
            retry_remaining=max(
                0, self.system_manual_retry_limit - system_state.manual_retries_used
            ),
            retry_pending=(
                system_state.run_id is not None
                and system_state.run_id == self._system_retry_requested_for
            ),
            automatic_retry_limit=self.system_automatic_retry_limit,
        )
        self.user_tab.update_state(
            "Your user tools",
            user_state,
            retry_enabled=(
                self.user_tools_enabled
                and self.user_request_file is not None
                and user_state.manual_retries_used < self.user_manual_retry_limit
            ),
            retry_remaining=max(0, self.user_manual_retry_limit - user_state.manual_retries_used),
            retry_pending=(
                user_state.run_id is not None
                and user_state.run_id == self._user_retry_requested_for
            ),
            automatic_retry_limit=self.user_automatic_retry_limit,
        )
        self._refresh_history()
        states = [system_state, user_state]
        active = any(state.phase in _ACTIVE_PHASES for state in states)
        self._sync_sections(system_state, user_state)
        if active and self._completion_emitted:
            self._completion_emitted = False
        if self.isVisible():
            self._seen_active = self._seen_active or active
        else:
            self._seen_active = False
        if self.isVisible() and self._seen_active and not active and not self._completion_emitted:
            self._completion_emitted = True
            QTimer.singleShot(0, self.all_updates_done.emit)

    def reset_section_expansion(self) -> None:
        """Apply the requested default expansion whenever details are opened."""
        self._expanded_active_sections = None
        self.refresh()

    def _sync_sections(self, system_state: UpdateState, user_state: UpdateState) -> None:
        expanded = frozenset(
            name
            for name, state in (("system", system_state), ("user", user_state))
            if state.phase in {"running", "waiting_for_internet"}
        )
        if expanded == self._expanded_active_sections:
            return
        self._expanded_active_sections = expanded
        self.system_section.set_expanded("system" in expanded)
        self.user_section.set_expanded("user" in expanded)
        self.history_section.set_expanded(not expanded)
        self._update_empty_state()

    def _update_empty_state(self) -> None:
        sections_open = any(
            section.is_expanded()
            for section in (self.system_section, self.user_section, self.history_section)
        )
        self.empty_state.setVisible(not sections_open)

    @staticmethod
    def _scroll_terminal_to_bottom_left(tab: _TaskTab) -> None:
        def scroll() -> None:
            terminal = tab.terminal
            terminal.verticalScrollBar().setValue(terminal.verticalScrollBar().maximum())
            terminal.horizontalScrollBar().setValue(terminal.horizontalScrollBar().minimum())

        QTimer.singleShot(0, scroll)

    def position_bottom_right(self) -> None:
        """Place the details window at the bottom right when the compositor allows it."""
        self._placement.position_bottom_right()

    def set_positioning_enabled(self, enabled: bool) -> None:
        """Apply the administrator's automatic-placement preference."""
        self._placement.set_enabled(enabled)

    @staticmethod
    def _read_state(
        store: StateStore | None,
        enabled: bool,
        *,
        include_output: bool = True,
    ) -> UpdateState:
        if store is None and enabled:
            return UpdateState(phase="idle", message="No machine-wide system task in this install.")
        if store is None or not enabled:
            if store is not None:
                try:
                    state = store.load()
                    if state.phase == "running":
                        return state if include_output else replace(state, last_output=())
                except StateError:
                    pass
            return UpdateState(phase="disabled", message="Disabled by administrator policy.")
        try:
            state = store.load()
            return state if include_output else replace(state, last_output=())
        except StateError as exc:
            return UpdateState(phase="failed", message=f"Could not read update state: {exc}")

    def _submit_user_retry(self) -> None:
        try:
            state = self.user_state_store.load()
            if state.phase != "failed" or state.run_id is None or self.user_request_file is None:
                return
            if state.manual_retries_used >= self.user_manual_retry_limit:
                return
            save_request(self.user_request_file, UserRequest(state.run_id, "retry"))
            self._user_retry_requested_for = state.run_id
            self.refresh()
        except (OSError, StateError, ValueError) as exc:
            self.user_tab.status.setText(f"Your user tools: could not request retry: {exc}")

    def apply_user_tool_policy(
        self,
        enabled: bool,
        admin_allowed_tools: tuple[str, ...],
        admin_allows_custom_tools: bool = True,
    ) -> None:
        """Refresh user-selectable choices after an administrator policy change."""
        self.user_tools_enabled = enabled
        self.admin_allowed_user_tools = tuple(admin_allowed_tools)
        self.admin_allows_custom_tools = admin_allows_custom_tools
        self._configure_user_tool_choices()
        self.refresh()

    def _configure_user_tool_choices(self) -> None:
        while self.user_tab.tool_check_layout.count():
            item = self.user_tab.tool_check_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.user_tab.tool_checkboxes.clear()
        tools = self.admin_allowed_user_tools
        selected = set(tools)
        custom_tools_selected = self.admin_allows_custom_tools
        if self.user_tool_preferences is not None:
            try:
                preferences = self.user_tool_preferences.load_all(
                    tools,
                    admin_allows_custom=self.admin_allows_custom_tools,
                )
                selected = set(preferences.tools)
                custom_tools_selected = preferences.custom_tools
                self.user_tab.tool_preferences_status.setText("")
            except ToolPreferenceError as exc:
                self.user_tab.tool_preferences_status.setText(
                    f"Preferences unavailable; using admin defaults: {exc}"
                )
        self.user_tab.custom_tools_checkbox.setChecked(custom_tools_selected)
        self.user_tab.custom_tools_checkbox.setVisible(self.admin_allows_custom_tools)
        for tool in tools:
            check = QCheckBox(tool)
            check.setChecked(tool in selected)
            self.user_tab.tool_checkboxes[tool] = check
            self.user_tab.tool_check_layout.addWidget(check)
        visible = (
            (bool(tools) or self.admin_allows_custom_tools)
            and self.user_tools_enabled
            and self.user_tool_preferences is not None
        )
        self.user_tab.tools_section.setVisible(visible)

    def _save_user_tool_preferences(self) -> None:
        if self.user_tool_preferences is None or not self.user_tools_enabled:
            return
        selected = tuple(
            tool for tool, check in self.user_tab.tool_checkboxes.items() if check.isChecked()
        )
        try:
            self.user_tool_preferences.save(
                selected,
                self.admin_allowed_user_tools,
                custom_tools=self.user_tab.custom_tools_checkbox.isChecked(),
                admin_allows_custom=self.admin_allows_custom_tools,
            )
            self.user_tab.tool_preferences_status.setText(
                "Saved; choices apply to the next personal-tool run."
            )
        except ToolPreferenceError as exc:
            self.user_tab.tool_preferences_status.setText(f"Could not save choices: {exc}")

    def _refresh_history(self) -> None:
        records: list[RunRecord] = []
        errors: list[str] = []
        for store in (self.system_history_store, self.user_history_store):
            if store is None:
                continue
            try:
                records.extend(store.load())
            except HistoryError as exc:
                errors.append(str(exc))
        records.sort(key=_history_timestamp, reverse=True)
        lines = [
            f"{_format_datetime(record.finished_at)}  {record.task}  "
            f"{record.phase}: {record.message}"
            for record in records
        ]
        lines.extend(f"History unavailable: {error}" for error in errors)
        text = "\n".join(lines)
        if text == self._history_text:
            return
        vertical_position = self.history_view.verticalScrollBar().value()
        horizontal_position = self.history_view.horizontalScrollBar().value()
        self.history_view.setPlainText(text)
        self.history_view.verticalScrollBar().setValue(vertical_position)
        self.history_view.horizontalScrollBar().setValue(horizontal_position)
        self._history_text = text

    def _update_open_log_button(self) -> None:
        path = self._current_log_path()
        self.open_log_button.setEnabled(
            path is not None and path.is_file() and not path.is_symlink()
        )
        user_path = self._current_user_log_path()
        self.open_user_log_button.setEnabled(
            user_path is not None and user_path.is_file() and not user_path.is_symlink()
        )

    def _current_log_path(self) -> Path | None:
        if self.log_file is None:
            return None
        return current_log_path(self.log_file, self._active_log_date)

    def _current_user_log_path(self) -> Path | None:
        if self.user_log_file is None:
            return None
        return current_log_path(self.user_log_file, self._active_user_log_date)

    def _open_current_log(self) -> None:
        path = self._current_log_path()
        if path is None or path.is_symlink() or not path.is_file():
            QMessageBox.information(
                self,
                "Current log",
                "The current updater log is not available.",
            )
            self._update_open_log_button()
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(
                self,
                "Open current log",
                f"Could not open the updater log:\n{path}",
            )

    def _open_user_tools_log(self) -> None:
        path = self._current_user_log_path()
        if path is None or path.is_symlink() or not path.is_file():
            QMessageBox.information(
                self,
                "User-tools log",
                "The personal-tool update log is not available.",
            )
            self._update_open_log_button()
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(
                self,
                "Open user-tools log",
                f"Could not open the personal-tool update log:\n{path}",
            )

    def _return_to_minimal(self) -> None:
        self.hide()
        self.back_requested.emit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        event.accept()
        self.back_requested.emit()


def _elapsed_text(state: UpdateState) -> str:
    if state.phase == "waiting_for_internet" or state.started_at is None:
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


def _active_log_date(*states: UpdateState) -> date | None:
    """Use the start date of the latest active task to find its pinned daily log."""
    active_starts: list[datetime] = []
    for state in states:
        if state.phase != "running" or state.started_at is None:
            continue
        try:
            active_starts.append(datetime.fromisoformat(state.started_at).astimezone())
        except (OverflowError, OSError, ValueError):
            continue
    return max(active_starts).date() if active_starts else None


def _state_log_date(state: UpdateState) -> date | None:
    """Use the latest personal-tool run's start date to select its log."""
    timestamp = state.started_at or state.finished_at
    if timestamp is None:
        return None
    try:
        return datetime.fromisoformat(timestamp).astimezone().date()
    except (OverflowError, OSError, ValueError):
        return None


class _TerminalStyle:
    def __init__(self, foreground: QColor, background: QColor) -> None:
        self.default_foreground = QColor(foreground)
        self.default_background = QColor(background)
        self.reset()

    def reset(self) -> None:
        self.foreground = QColor(self.default_foreground)
        self.background = QColor(self.default_background)
        self.bold = False
        self.dim = False
        self.italic = False
        self.underline = False
        self.strike = False
        self.inverse = False

    def format(self) -> QTextCharFormat:
        text_format = QTextCharFormat()
        foreground = self.background if self.inverse else self.foreground
        background = self.foreground if self.inverse else self.background
        if self.dim:
            foreground = foreground.darker(150)
        text_format.setForeground(foreground)
        text_format.setBackground(background)
        text_format.setFontWeight(QFont.Weight.Bold if self.bold else QFont.Weight.Normal)
        text_format.setFontItalic(self.italic)
        text_format.setFontUnderline(self.underline)
        text_format.setFontStrikeOut(self.strike)
        return text_format


def _render_terminal_text(terminal: QPlainTextEdit, text: str) -> None:
    """Render the read-only output pane with ANSI SGR colors and text styles."""
    terminal.clear()
    cursor = terminal.textCursor()
    palette = terminal.palette()
    style = _TerminalStyle(
        palette.color(QPalette.ColorRole.Text),
        palette.color(QPalette.ColorRole.Base),
    )
    lines = text.split("\n")
    for line_number, line in enumerate(lines):
        offset = 0
        for match in _SGR_SEQUENCE.finditer(line):
            if offset < match.start():
                cursor.insertText(line[offset : match.start()], style.format())
            _apply_sgr(style, match.group(1))
            offset = match.end()
        if offset < len(line):
            cursor.insertText(line[offset:], style.format())
        if line_number < len(lines) - 1:
            cursor.insertText("\n", style.format())
    terminal.setTextCursor(cursor)
    terminal.ensureCursorVisible()


def _apply_sgr(style: _TerminalStyle, parameters: str) -> None:
    codes = [int(value) if value else 0 for value in parameters.split(";")]
    index = 0
    while index < len(codes):
        code = codes[index]
        if code == 0:
            style.reset()
        elif code == 1:
            style.bold = True
        elif code == 2:
            style.dim = True
        elif code == 3:
            style.italic = True
        elif code == 4:
            style.underline = True
        elif code == 7:
            style.inverse = True
        elif code == 9:
            style.strike = True
        elif code in (22, 23, 24, 27, 29):
            if code == 22:
                style.bold = style.dim = False
            elif code == 23:
                style.italic = False
            elif code == 24:
                style.underline = False
            elif code == 27:
                style.inverse = False
            else:
                style.strike = False
        elif 30 <= code <= 37:
            style.foreground = QColor(_ANSI_COLORS[code - 30])
        elif 90 <= code <= 97:
            style.foreground = QColor(_ANSI_COLORS[code - 90 + 8])
        elif code == 39:
            style.foreground = QColor(style.default_foreground)
        elif 40 <= code <= 47:
            style.background = QColor(_ANSI_COLORS[code - 40])
        elif 100 <= code <= 107:
            style.background = QColor(_ANSI_COLORS[code - 100 + 8])
        elif code == 49:
            style.background = QColor(style.default_background)
        elif code in (38, 48) and index + 1 < len(codes):
            color, consumed = _extended_color(codes, index + 1)
            if color is not None:
                if code == 38:
                    style.foreground = color
                else:
                    style.background = color
            index += consumed
        index += 1


def _extended_color(codes: list[int], start: int) -> tuple[QColor | None, int]:
    mode = codes[start]
    if mode == 5 and start + 1 < len(codes):
        return _ansi_256_color(codes[start + 1]), 2
    if mode == 2 and start + 3 < len(codes):
        red, green, blue = (min(255, max(0, value)) for value in codes[start + 1 : start + 4])
        return QColor(red, green, blue), 4
    return None, 1


def _ansi_256_color(index: int) -> QColor:
    index = min(255, max(0, index))
    if index < 16:
        return QColor(_ANSI_COLORS[index])
    if index < 232:
        value = index - 16
        red, green, blue = value // 36, (value // 6) % 6, value % 6

        def component(channel: int) -> int:
            return 0 if channel == 0 else 55 + 40 * channel

        return QColor(component(red), component(green), component(blue))
    gray = 8 + 10 * (index - 232)
    return QColor(gray, gray, gray)


def _format_datetime(value: str | None) -> str:
    if value is None:
        return "—"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    except (OverflowError, OSError, ValueError):
        return "unknown"


def _history_timestamp(record: RunRecord) -> float:
    if record.finished_at is None:
        return 0.0
    try:
        return datetime.fromisoformat(record.finished_at).timestamp()
    except (OverflowError, OSError, ValueError):
        return 0.0
