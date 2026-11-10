"""苹果风对话框，替代系统 QMessageBox / QInputDialog。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout,
)

from . import theme


class _Base(QDialog):
    def __init__(self, parent, title: str, message: str, width: int = 420):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(width)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 22, 22, 22)

        card = QFrame()
        card.setObjectName("DialogCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(48)
        shadow.setColor(theme.c("shadow"))
        shadow.setOffset(0, 10)
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        self.body = QVBoxLayout(card)
        self.body.setContentsMargins(24, 22, 24, 18)
        self.body.setSpacing(10)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("DialogTitle")
        self.body.addWidget(self.title_label)

        self.message_label = QLabel(message)
        self.message_label.setObjectName("DialogBody")
        self.message_label.setWordWrap(True)
        self.message_label.setMaximumHeight(300)
        self.body.addWidget(self.message_label)

        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        self.buttons.setContentsMargins(0, 8, 0, 0)
        self.buttons.addStretch(1)
        self.body.addLayout(self.buttons)

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_popped", False):
            self._popped = True
            from .widgets import animate_pop
            animate_pop(self)

    def add_button(self, text: str, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        if primary:
            button.setObjectName("Primary")
        button.setMinimumWidth(78)
        self.buttons.addWidget(button)
        return button


def error(parent, title: str, message: str) -> None:
    dialog = _Base(parent, title, message)
    ok = dialog.add_button("好", primary=True)
    ok.clicked.connect(dialog.accept)
    ok.setDefault(True)
    dialog.adjustSize()
    dialog.exec()


def info(parent, title: str, message: str) -> None:
    error(parent, title, message)


def confirm(parent, title: str, message: str, ok_text: str = "确定",
            cancel_text: str = "取消") -> bool:
    dialog = _Base(parent, title, message)
    cancel = dialog.add_button(cancel_text)
    ok = dialog.add_button(ok_text, primary=True)
    cancel.clicked.connect(dialog.reject)
    ok.clicked.connect(dialog.accept)
    ok.setDefault(True)
    dialog.adjustSize()
    return dialog.exec() == QDialog.Accepted


def ask_text(parent, title: str, message: str, default: str = "") -> tuple[str | None, bool]:
    dialog = _Base(parent, title, message)
    edit = QLineEdit(default)
    edit.setMinimumHeight(36)
    dialog.body.insertWidget(dialog.body.count() - 1, edit)
    cancel = dialog.add_button("取消")
    ok = dialog.add_button("确定", primary=True)
    cancel.clicked.connect(dialog.reject)
    ok.clicked.connect(dialog.accept)
    edit.returnPressed.connect(dialog.accept)
    edit.selectAll()
    edit.setFocus()
    dialog.adjustSize()
    if dialog.exec() == QDialog.Accepted:
        return edit.text().strip(), True
    return None, False
