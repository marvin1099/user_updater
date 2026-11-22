"""Expanded read-only update console with collapsible task and history sections."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QTextCharFormat
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..history import HistoryError, RunHistoryStore, RunRecord, history_path_for_state
from ..requests import UserRequest, save_request
from ..state import StateError, StateStore, UpdateState
from ..tool_preferences import ToolPreferenceError, UserToolPreferenceStore
from .collapsible import CollapsibleSection
from .placement import WindowPlacement

_ACTIVE_PHASES = {"awaiting_response", "running", "deferred"}
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
_TERMINAL_FOREGROUND = QColor("#dbe7f1")
_TERMINAL_BACKGROUND = QColor("#11151a")


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
        self.tools_group = QGroupBox("Personal tools enabled for your account")
        self.tool_checks_widget = QWidget(self.tools_group)
        self.tool_check_layout = QHBoxLayout(self.tool_checks_widget)
        self.tool_check_layout.setContentsMargins(0, 0, 0, 0)
        self.tool_checkboxes: dict[str, QCheckBox] = {}
        self.custom_tools_checkbox = QCheckBox("Run my scripts from ~/.config/user_updater/tools.d")
        self.custom_tools_checkbox.setObjectName("customToolScripts")
        self.tool_preferences_status = QLabel("")
        self.save_tools_button = QPushButton("Save tool choices")
        tools_layout = QVBoxLayout(self.tools_group)
        tools_layout.addWidget(self.tool_checks_widget)
        tools_layout.addWidget(self.custom_tools_checkbox)
        tools_controls = QHBoxLayout()
        tools_controls.addWidget(self.tool_preferences_status, stretch=1)
        tools_controls.addWidget(self.save_tools_button)
        tools_layout.addLayout(tools_controls)
        self.tools_group.hide()
        self.terminal = QPlainTextEdit()
        self._terminal_source = ""
        self.retry_button = QPushButton("Retry failed update")
        self.retry_button.setObjectName("retry")
        self.retry_button.hide()
        self.terminal.setReadOnly(True)
        self.terminal.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.terminal.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.terminal.setStyleSheet("QPlainTextEdit { background: #11151a; color: #dbe7f1; }")
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(self.status, stretch=1)
        heading.addWidget(self.elapsed)
        layout.addLayout(heading)
        layout.addWidget(self.next_run)
        layout.addWidget(self.retry_info)
        layout.addWidget(self.tools_group)
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
        self.system_tab = _TaskTab("System updates")
        self.user_tab = _TaskTab("User tools")
        self.history_view = QPlainTextEdit()
        self.history_view.setObjectName("runHistory")
        self.history_view.setReadOnly(True)
        self.history_view.setPlaceholderText("No completed update runs recorded.")
        self.system_section = CollapsibleSection("System updates", self.system_tab)
        self.user_section = CollapsibleSection("User tools", self.user_tab)
        self.history_section = CollapsibleSection("Run history", self.history_view)
        sections_widget = QWidget()
        sections_layout = QVBoxLayout(sections_widget)
        sections_layout.setContentsMargins(0, 0, 0, 0)
        sections_layout.setSpacing(6)
        sections_layout.addWidget(self.system_section)
        sections_layout.addWidget(self.user_section)
        sections_layout.addWidget(self.history_section)
        sections_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("detailsScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setWidget(sections_widget)
        self.system_tab.retry_button.clicked.connect(self.system_retry_requested.emit)
        self.user_tab.retry_button.clicked.connect(self._submit_user_retry)
        self.user_tab.save_tools_button.clicked.connect(self._save_user_tool_preferences)
        self._configure_user_tool_choices()
        self.back_button = QPushButton("Back to minimal view")
        self.back_button.clicked.connect(self._return_to_minimal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(title)
        layout.addWidget(scroll, stretch=1)
        controls = QHBoxLayout()
        controls.addStretch(1)
        controls.addWidget(self.back_button)
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
        self._seen_active = self._seen_active or active
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
            if state.phase == "running"
        )
        if expanded == self._expanded_active_sections:
            return
        self._expanded_active_sections = expanded
        self.system_section.set_expanded("system" in expanded)
        self.user_section.set_expanded("user" in expanded)
        self.history_section.set_expanded(not expanded)

    def position_bottom_right(self) -> None:
        """Place the details window at the bottom right when the compositor allows it."""
        self._placement.position_bottom_right()

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
        self.user_tab.tools_group.setVisible(visible)

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
        self.history_view.setPlainText("\n".join(lines))

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


class _TerminalStyle:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.foreground = QColor(_TERMINAL_FOREGROUND)
        self.background = QColor(_TERMINAL_BACKGROUND)
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
    style = _TerminalStyle()
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
            style.foreground = QColor(_TERMINAL_FOREGROUND)
        elif 40 <= code <= 47:
            style.background = QColor(_ANSI_COLORS[code - 40])
        elif 100 <= code <= 107:
            style.background = QColor(_ANSI_COLORS[code - 100 + 8])
        elif code == 49:
            style.background = QColor(_TERMINAL_BACKGROUND)
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
