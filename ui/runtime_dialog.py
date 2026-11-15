"""GPU 加速包的下载与提示：torch 的 CUDA 版不进安装包，首次使用时按需下载。"""
from __future__ import annotations

import sys

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QVBoxLayout, QDialog, Qt,
)

from app import runtime_pack as pack

from . import theme


class _PackWorker(QThread):
    progress = Signal(float)
    stage = Signal(str)
    done = Signal(bool, str)

    def run(self):
        try:
            pack.download_gpu(on_progress=self.progress.emit,
                              on_stage=self.stage.emit)
            self.done.emit(True, "")
        except Exception as exc:
            self.done.emit(False, str(exc))


class RuntimeDialog(QDialog):
    """下载 / 重装 GPU 加速包。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(520)
        self._worker: _PackWorker | None = None
        self._installed = pack.gpu_ready()

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

        title = QLabel("GPU 加速")
        title.setObjectName("DialogTitle")
        body.addWidget(title)

        if self._installed:
            text = ("GPU 加速包已安装。人声分离会自动使用 NVIDIA 显卡；"
                    "如果显卡驱动更新后无法使用，可以在这里重新下载。")
        else:
            text = (
                f"检测到 NVIDIA 显卡，可以下载 GPU 加速包（{pack.GPU_SIZE_TEXT}），"
                "人声分离和语音转写会快 5～10 倍。\n\n"
                "不下载也能正常使用，只是全部由 CPU 计算，长素材会慢很多。\n"
                "下载后解压约 3.5 GB，只占本地空间，不需要重新下载。")
        self.message = QLabel(text)
        self.message.setObjectName("DialogBody")
        self.message.setWordWrap(True)
        body.addWidget(self.message)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {theme.s('text2')}; font-size: 12px;")
        self.status.setVisible(False)
        body.addWidget(self.status)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        body.addWidget(self.bar)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.btn_later = QPushButton("关闭" if self._installed else "不再提示")
        self.btn_later.clicked.connect(self.reject)
        self.btn_go = QPushButton("重新下载" if self._installed else "下载 GPU 包")
        self.btn_go.setObjectName("Primary")
        self.btn_go.clicked.connect(self._start)
        buttons.addWidget(self.btn_later)
        buttons.addWidget(self.btn_go)
        body.addLayout(buttons)
        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_popped", False):
            self._popped = True
            from .widgets import animate_pop
            animate_pop(self)

    def closeEvent(self, event):
        # 下载中不允许关窗，否则线程会往已销毁的控件发信号
        if self._worker is not None and self._worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)

    def _start(self):
        if self._worker is not None:
            return
        self.btn_go.setEnabled(False)
        self.btn_later.setEnabled(False)
        self.bar.setVisible(True)
        self.status.setVisible(True)
        self.status.setText("准备下载…")
        self._worker = _PackWorker(self)
        self._worker.progress.connect(self._on_progress)
        self._worker.stage.connect(self.status.setText)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_progress(self, value: float):
        self.bar.setValue(int(max(0.0, min(1.0, value)) * 1000))

    def _on_done(self, ok: bool, message: str):
        self.btn_go.setEnabled(True)
        self.btn_later.setEnabled(True)
        if ok:
            self.bar.setValue(1000)
            self.status.setText("下载完成，重启软件后生效")
            self.message.setText("GPU 加速包已就绪。重启软件后人声分离会自动使用显卡。")
            self.btn_go.setText("立即重启")
            try:
                self.btn_go.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.btn_go.clicked.connect(self._restart)
        else:
            self.bar.setVisible(False)
            self.status.setText(f"下载失败：{message}")

    def _restart(self):
        from PySide6.QtCore import QProcess

        QProcess.startDetached(sys.executable, sys.argv[1:])
        QApplication.quit()


def maybe_offer(parent=None) -> None:
    """首次启动：有 N 卡、没装 GPU 包、用户没勾过不再提示时才问。"""
    if not getattr(sys, "frozen", False) or pack.gpu_ready():
        return
    settings = QSettings("SpeakerSplit", "SpeakerSplit")
    if str(settings.value("gpu_prompt_skip", "0")) == "1":
        return
    if not pack.has_nvidia_gpu():
        return
    dialog = RuntimeDialog(parent)
    if dialog.exec() != QDialog.Accepted and dialog._worker is None:
        settings.setValue("gpu_prompt_skip", "1")
