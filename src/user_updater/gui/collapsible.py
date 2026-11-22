"""Small collapsible section used by the desktop update views."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class CollapsibleSection(QWidget):
    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        expanded: bool = False,
        header_inside: bool = True,
        checkbox_header: bool = False,
        expanded_border: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.header_inside = header_inside
        self.expanded_border = expanded_border
        if expanded_border:
            self.setObjectName("testingSection")
        if checkbox_header:
            self.header = QCheckBox(title, self if header_inside else None)
        else:
            self.header = QToolButton(self if header_inside else None)
            self.header.setText(title)
        self.header.setObjectName("sectionHeader")
        self.header.setCheckable(True)
        if isinstance(self.header, QToolButton):
            self.header.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                if header_inside
                else Qt.ToolButtonStyle.ToolButtonTextOnly
            )
        self.header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.content = content
        self.content.setParent(self)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        if expanded_border:
            layout.setContentsMargins(6, 5, 6, 6)
        else:
            layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        if header_inside:
            header_layout = QHBoxLayout()
            header_layout.setContentsMargins(0, 0, 0, 0)
            header_layout.addWidget(self.header, alignment=Qt.AlignmentFlag.AlignLeft)
            header_layout.addStretch(1)
            layout.addLayout(header_layout)
        layout.addWidget(self.content)

        self.header.toggled.connect(self._sync_expanded)
        self.set_expanded(expanded)

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(expanded)
        self._sync_expanded(expanded)

    def _sync_expanded(self, expanded: bool) -> None:
        if self.header_inside and isinstance(self.header, QToolButton):
            self.header.setArrowType(
                Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
            )
        elif isinstance(self.header, QToolButton):
            self.header.setArrowType(Qt.ArrowType.NoArrow)
        self.content.setVisible(expanded)
        if not self.header_inside:
            self.setVisible(expanded)
        if self.expanded_border:
            self.setStyleSheet(
                "QWidget#testingSection { border: 1px solid #657180; border-radius: 5px; }"
                if expanded
                else ""
            )
