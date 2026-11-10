"""人声分离模型选择对话框：列出全部模型，未下载的可直接下载。"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout,
    QWidget,
)

from app import separation_models as sm

from . import dialogs, theme


class _Downloader(QThread):
    progress = Signal(int, int)
    done = Signal(bool, str)

    def __init__(self, spec: sm.SepModel, parent=None):
        super().__init__(parent)
        self.spec = spec

    def run(self):
        try:
            sm.download_model(self.spec, on_progress=self.progress.emit)
            self.done.emit(True, "")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, str(exc))


class ModelDialog(QDialog):
    def __init__(self, parent, current_key: str):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(640)
        self.selected_key: str | None = None
        self._downloader: _Downloader | None = None
        self._current_key = current_key

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

        body = QVBoxLayout(card)
        body.setContentsMargins(24, 22, 24, 18)
        body.setSpacing(10)

        title = QLabel("人声分离模型")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        body.addWidget(title)

        desc = QLabel("效果分越高越干净；大小、速度为这台电脑实测（30 秒素材）。")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {theme.s('text2')}; font-size: 12px;")
        body.addWidget(desc)

        self.list = QListWidget()
        self.list.setMinimumHeight(420)
        self.list.setStyleSheet("QListWidget { background: transparent; border: none; }")
        self.list.currentRowChanged.connect(self._sync_buttons)
        body.addWidget(self.list)

        for spec in sm.MODELS:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, spec.key)
            widget = self._row(spec)
            item.setSizeHint(QSize(0, 58))
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
            if spec.key == current_key:
                self.list.setCurrentRow(self.list.count() - 1)

        self.progress_text = QLabel("")
        self.progress_text.setStyleSheet(f"color: {theme.s('text2')}; font-size: 12px;")
        self.progress_text.setVisible(False)
        body.addWidget(self.progress_text)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        body.addWidget(self.progress)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_use = QPushButton("使用此模型")
        self.btn_use.setObjectName("Primary")
        self.btn_use.clicked.connect(self._use_selected)
        buttons.addWidget(self.btn_cancel)
        buttons.addWidget(self.btn_use)
        body.addLayout(buttons)

        self._sync_buttons()
        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_popped", False):
            self._popped = True
            from .widgets import animate_pop
            animate_pop(self)

    # ---------- 行 ----------
    def _row(self, spec: sm.SepModel) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(12, 7, 12, 7)
        row.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(2)
        name = QLabel(spec.name)
        name.setStyleSheet("font-size: 13px; font-weight: 600;")
        meta = QLabel(
            f"效果 {spec.sdr:.2f} · {spec.size_text} · {spec.speed_text} · {spec.vram_text}")
        meta.setStyleSheet(f"color: {theme.s('text2')}; font-size: 12px;")
        left.addWidget(name)
        left.addWidget(meta)
        row.addLayout(left, 1)

        status = QLabel()
        status.setObjectName("ModelStatus")
        self._set_status(status, spec)
        status.setProperty("specKey", spec.key)
        row.addWidget(status)
        return widget

    def _set_status(self, label: QLabel, spec: sm.SepModel) -> None:
        if sm.is_downloaded(spec):
            label.setText("已下载")
            label.setStyleSheet(f"color: {theme.s('accent')}; font-size: 12px;")
        else:
            label.setText("未下载")
            label.setStyleSheet(f"color: {theme.s('text3')}; font-size: 12px;")

    def _refresh_status(self, key: str) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.UserRole) != key:
                continue
            widget = self.list.itemWidget(item)
            if widget is None:
                continue
            label = widget.findChild(QLabel, "ModelStatus")
            if label is not None:
                self._set_status(label, sm.BY_KEY[key])

    # ---------- 操作 ----------
    def _selected_spec(self) -> sm.SepModel | None:
        item = self.list.currentItem()
        if item is None:
            return None
        return sm.BY_KEY.get(item.data(Qt.UserRole))

    def _sync_buttons(self) -> None:
        if not hasattr(self, "btn_use"):
            return
        self.btn_use.setEnabled(self._selected_spec() is not None)

    def _use_selected(self):
        spec = self._selected_spec()
        if spec is None or self._downloader is not None:
            return

        if spec.kind == "demucs" or sm.is_downloaded(spec):
            self.selected_key = spec.key
            self.accept()
            return

        ok = dialogs.confirm(
            self, "尚未下载这个模型",
            f"该电脑未下载「{spec.name}」（约 {spec.size_text}）。\n\n"
            f"点击确认会自动下载到程序目录，需要联网；下载完即可直接使用。",
            "确认下载", "取消")
        if ok:
            self._start_download(spec)

    def _start_download(self, spec: sm.SepModel):
        self.progress_text.setText(f"正在下载「{spec.name}」…")
        self.progress_text.setVisible(True)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.btn_use.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.list.setEnabled(False)

        self._downloader = _Downloader(spec, self)
        self._downloader.progress.connect(self._on_progress)
        self._downloader.done.connect(lambda ok, msg: self._on_done(spec, ok, msg))
        self._downloader.start()

    def _on_progress(self, done: int, total: int):
        if total > 0:
            self.progress.setValue(int(done * 1000 / total))
            self.progress_text.setText(
                f"正在下载「{self._downloader.spec.name}」… "
                f"{done / 1048576:.1f} / {total / 1048576:.1f} MB")
        else:
            self.progress_text.setText(
                f"正在下载「{self._downloader.spec.name}」… {done / 1048576:.1f} MB")

    def _on_done(self, spec: sm.SepModel, ok: bool, message: str):
        self._downloader = None
        self.progress.setVisible(False)
        self.progress_text.setVisible(False)
        self.list.setEnabled(True)
        self.btn_cancel.setEnabled(True)
        self._sync_buttons()

        if not ok:
            dialogs.error(self, "下载失败", message[-1500:])
            return

        self._refresh_status(spec.key)
        self.selected_key = spec.key
        self.accept()
