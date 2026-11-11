"""设置浮层：主窗口内的设置面板，避免独立无边框窗口的闪黑与错位。"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QThread, QVariantAnimation, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from app import separation_models as sm
from app import speaker_models as spk
from app import __version__

from . import theme
from .widgets import SpringButton

PAGE_NAMES = ["通用", "人声分离", "文字识别", "声纹识别", "外观"]


class _Downloader(QThread):
    progress = Signal(int, int)
    done = Signal(bool, str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.fn(self.progress.emit)
            self.done.emit(True, "")
        except Exception as exc:
            self.done.emit(False, str(exc))


class SettingsOverlay(QWidget):
    """覆盖在主窗口上的设置面板：半透明遮罩 + 居中卡片。"""

    CARD_W, CARD_H = 840, 590

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self._settings = QSettings("SpeakerSplit", "SpeakerSplit")
        self._alpha = 0.0
        self._closing = False
        self.hide()

        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.card = QFrame()
        self.card.setObjectName("SettingsCard")
        self.card.setFixedSize(self.CARD_W, self.CARD_H)
        row.addWidget(self.card)
        row.addStretch(1)
        self.outer.addLayout(row)
        self.outer.addStretch(1)

        body = QHBoxLayout(self.card)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        side_panel = QWidget()
        side_panel.setFixedWidth(200)
        sv = QVBoxLayout(side_panel)
        sv.setContentsMargins(8, 14, 8, 8)
        sv.setSpacing(2)
        nav_title = QLabel("设置")
        nav_title.setStyleSheet(
            "font-size: 18px; font-weight: 700; letter-spacing: -0.3px;")
        nav_title.setContentsMargins(8, 0, 0, 10)
        sv.addWidget(nav_title)

        self.side = QListWidget()
        self.side.setStyleSheet(
            "QListWidget { background: transparent; border: none; padding: 0; }"
            "QListWidget::item { padding: 9px 12px; border-radius: 8px; }")
        for name in PAGE_NAMES:
            self.side.addItem(QListWidgetItem(name))
        self.side.setCurrentRow(0)
        sv.addWidget(self.side, 1)

        version = QLabel(f"版本 {__version__}")
        version.setObjectName("VersionLabel")
        version.setContentsMargins(8, 8, 0, 0)
        sv.addWidget(version)
        body.addWidget(side_panel)

        right = QVBoxLayout()
        right.setContentsMargins(22, 18, 22, 18)
        right.setSpacing(12)
        title_row = QHBoxLayout()
        self.title = QLabel(PAGE_NAMES[0])
        self.title.setObjectName("DialogTitle")
        title_row.addWidget(self.title)
        title_row.addStretch(1)
        close = QPushButton("关闭")
        close.setObjectName("Primary")
        close.clicked.connect(self.close_)
        title_row.addWidget(close)
        right.addLayout(title_row)

        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        body.addLayout(right, 1)

        self.side.currentRowChanged.connect(self._switch)

        self._fade = QVariantAnimation(self)
        self._fade.setDuration(180)
        self._fade.valueChanged.connect(self._on_alpha)
        self._fade.finished.connect(self._on_fade_end)

    # ---------- 显示 / 隐藏 ----------
    def open(self, index: int | None = None):
        self._rebuild_pages(index)
        self.setGeometry(self.main.rect())
        self._closing = False
        self.raise_()
        self.show()
        self.setFocus()
        self._fade.stop()
        self._fade.setStartValue(self._alpha)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def close_(self):
        if not self.isVisible():
            return
        self._closing = True
        self._fade.stop()
        self._fade.setStartValue(self._alpha)
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_alpha(self, value):
        self._alpha = float(value)
        self.update()

    def _on_fade_end(self):
        if self._closing:
            self.hide()

    def paintEvent(self, event):
        p = QColor(20, 24, 36, 70) if not theme.dark else QColor(0, 0, 0, 120)
        p.setAlphaF(p.alphaF() * self._alpha)
        from PySide6.QtGui import QPainter
        painter = QPainter(self)
        painter.fillRect(self.rect(), p)

    def mousePressEvent(self, e):
        if not self.card.geometry().contains(e.position().toPoint()):
            self.close_()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_()
            return
        super().keyPressEvent(e)

    # ---------- 页面 ----------
    def _rebuild_pages(self, index: int | None = None):
        keep = self.side.currentRow() if index is None else index
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()
        self.stack.addWidget(self._page_general())
        self.stack.addWidget(self._page_separation())
        self.stack.addWidget(self._page_asr())
        self.stack.addWidget(self._page_speaker())
        self.stack.addWidget(self._page_appearance())
        keep = max(0, min(len(PAGE_NAMES) - 1, keep))
        self.side.setCurrentRow(keep)
        self._switch(keep)

    def _page_general(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(14)

        lay.addWidget(self._label("模型下载源", bold=True))
        self.src_box = QComboBox()
        self.src_box.addItem("自动（优先国内镜像）", "auto")
        self.src_box.addItem("HuggingFace 官方", "hf")
        self.src_box.addItem("hf-mirror 镜像", "mirror")
        current = self._settings.value("download_source", "auto")
        self.src_box.setCurrentIndex(max(0, ["auto", "hf", "mirror"].index(current)))
        self.src_box.currentIndexChanged.connect(self._save_source)
        lay.addWidget(self.src_box)
        lay.addWidget(self._label("自动模式会先尝试官方，失败后改用 hf-mirror。"))

        lay.addSpacing(8)
        lay.addWidget(self._label("模型目录", bold=True))
        from app.modelstore import MODELS_DIR

        path = QLabel(str(MODELS_DIR))
        path.setWordWrap(True)
        path.setStyleSheet(f"color: {theme.s('text2')};")
        lay.addWidget(path)
        btn = SpringButton("打开模型目录")
        btn.clicked.connect(self._open_models)
        lay.addWidget(btn, 0, Qt.AlignLeft)
        lay.addStretch(1)
        return page

    def _page_separation(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(8)

        lay.addWidget(self._label("处理方式", bold=True))
        self.clean_box = QComboBox()
        self.clean_box.addItem("去人声（先分离背景音乐，识别更准）", True)
        self.clean_box.addItem("保留原声（不做人声分离）", False)
        self.clean_box.setCurrentIndex(0 if self.main.clean_mode else 1)
        self.clean_box.currentIndexChanged.connect(self._save_clean)
        lay.addWidget(self.clean_box)

        streams = self.main.media_info.audio_streams if self.main.media_info else []
        if len(streams) > 1:
            self.track_box = QComboBox()
            for s in streams:
                self.track_box.addItem(s.describe(), s.ordinal)
            idx = next((i for i, s in enumerate(streams)
                        if s.ordinal == self.main.track_index), 0)
            self.track_box.setCurrentIndex(idx)
            self.track_box.currentIndexChanged.connect(self._save_track)
            lay.addWidget(self.track_box)

        lay.addSpacing(8)
        lay.addWidget(self._label("人声分离模型", bold=True))
        lay.addWidget(self._label("效果分来自公开评测；大小与速度为实测。"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        rows = QVBoxLayout(holder)
        rows.setSpacing(4)
        self._model_rows = {}
        for spec in sm.MODELS:
            rows.addWidget(self._model_row(spec))
        rows.addStretch(1)
        scroll.setWidget(holder)
        lay.addWidget(scroll, 1)
        return page

    def _model_row(self, spec) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)
        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(spec.name)
        name.setStyleSheet("font-weight: 600;")
        meta = QLabel(f"效果 {spec.sdr:.2f} · {spec.size_text} · {spec.speed_text} · {spec.vram_text}")
        meta.setStyleSheet(f"color: {theme.s('text2')}; font-size: 12px;")
        info.addWidget(name)
        info.addWidget(meta)
        lay.addLayout(info, 1)

        status = QLabel("已下载" if sm.is_downloaded(spec) else "未下载")
        status.setStyleSheet(
            f"color: {theme.s('accent') if sm.is_downloaded(spec) else theme.s('text3')}; font-size: 12px;")
        lay.addWidget(status)

        use = SpringButton("使用", "ghost")
        use.clicked.connect(lambda: self._use_model(spec))
        lay.addWidget(use)

        dl = SpringButton("下载", "ghost")
        dl.setVisible(not sm.is_downloaded(spec))
        dl.clicked.connect(lambda: self._download(
            lambda cb, s=spec: sm.download_model(s, on_progress=cb), dl, status))
        lay.addWidget(dl)

        self._model_rows[spec.key] = (status, dl)
        return row

    def _page_asr(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)
        lay.addWidget(self._label("文字识别模型", bold=True))
        lay.addWidget(self._label(
            "标准：large-v3-turbo，速度快，适合大部分素材。\n"
            "高精度：large-v3，方言与错字更少，速度慢约 3.6 倍，首次需下载约 3 G。"))
        self.asr_box = QComboBox()
        self.asr_box.addItem("标准（large-v3-turbo）", "large-v3-turbo")
        self.asr_box.addItem("高精度（large-v3）", "large-v3")
        current = self.main.whisper_model
        self.asr_box.setCurrentIndex(1 if current == "large-v3" else 0)
        self.asr_box.currentIndexChanged.connect(self._save_asr)
        lay.addWidget(self.asr_box)
        lay.addStretch(1)
        return page

    def _page_speaker(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(8)

        lay.addWidget(self._label("声纹嵌入模型", bold=True))
        lay.addWidget(self._label(
            "用来判断两段声音是不是同一个人。切换后下次处理生效，首次使用会自动下载。"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        rows = QVBoxLayout(holder)
        rows.setSpacing(4)
        for spec in spk.MODELS:
            rows.addWidget(self._spk_row(spec))
        rows.addStretch(1)
        scroll.setWidget(holder)
        lay.addWidget(scroll, 1)

        lay.addWidget(self._label("说话人日志（分割）", bold=True))
        lay.addWidget(self._label(
            "分割固定使用 sherpa-onnx 的 pyannote segmentation 3.0，与上面的声纹模型配合聚类。"
            "pyannote.audio、NVIDIA Sortformer、DiariZen 属于其他运行时（需要 PyTorch/NeMo 与授权），"
            "当前版本没有集成，不能在这里切换。"))
        return page

    def _spk_row(self, spec) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)
        info = QVBoxLayout()
        info.setSpacing(2)
        tags = " · ".join(spec.tags)
        name = QLabel(spec.name + (f"（{tags}）" if tags else ""))
        name.setStyleSheet("font-weight: 600;")
        meta = QLabel(f"{spec.size_text} · {spec.note}" if spec.note else spec.size_text)
        meta.setStyleSheet(f"color: {theme.s('text2')}; font-size: 12px;")
        info.addWidget(name)
        info.addWidget(meta)
        lay.addLayout(info, 1)

        current = self.main.spk_model_key == spec.key
        downloaded = spk.is_downloaded(spec)
        status = QLabel("使用中" if current else ("已下载" if downloaded else "未下载"))
        color = (theme.s('accent') if current
                 else theme.s('text2') if downloaded else theme.s('text3'))
        status.setStyleSheet(f"color: {color}; font-size: 12px;")
        lay.addWidget(status)

        use = SpringButton("使用", "ghost")
        use.setVisible(not current)
        use.clicked.connect(lambda: self._use_spk_model(spec))
        lay.addWidget(use)

        dl = SpringButton("下载", "ghost")
        dl.setVisible(not downloaded)
        dl.clicked.connect(lambda: self._download(
            lambda cb, s=spec: spk.download_model(s, on_progress=cb), dl, status))
        lay.addWidget(dl)
        return row

    def _use_spk_model(self, spec):
        self.main.spk_model_key = spec.key
        self._settings.setValue("spk_model", spec.key)
        self._rebuild_pages(self.side.currentRow())

    def _page_appearance(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)
        lay.addWidget(self._label("外观", bold=True))
        lay.addWidget(self._label("切换时会有圆形扩散过渡；也可以点标题栏左侧的太阳/月亮按钮快速切换。"))

        row = QHBoxLayout()
        row.setSpacing(8)
        self._mode_buttons = {}
        for key, text in (("light", "浅色"), ("dark", "深色"), ("system", "跟随系统")):
            btn = SpringButton(text)
            btn.clicked.connect(lambda _=False, k=key: self._set_mode(k))
            row.addWidget(btn)
            self._mode_buttons[key] = btn
        row.addStretch(1)
        lay.addLayout(row)
        self._refresh_mode_buttons()
        lay.addStretch(1)
        return page

    # ---------- 行为 ----------
    def _switch(self, index: int):
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
            self.title.setText(PAGE_NAMES[index])

    def _label(self, text: str, bold: bool = False) -> QLabel:
        lab = QLabel(text)
        lab.setWordWrap(True)
        if bold:
            lab.setStyleSheet("font-weight: 600;")
        else:
            lab.setStyleSheet(f"color: {theme.s('text2')}; font-size: 12.5px;")
        return lab

    def _open_models(self):
        import os
        import subprocess
        from app.modelstore import MODELS_DIR

        try:
            os.startfile(str(MODELS_DIR))
        except Exception:
            subprocess.Popen(["explorer", str(MODELS_DIR)])

    def _save_source(self, index: int):
        self._settings.setValue("download_source", self.src_box.currentData())

    def _save_asr(self, index: int):
        self.main.whisper_model = self.asr_box.currentData() or "large-v3-turbo"

    def _save_clean(self, index: int):
        self.main.clean_mode = bool(self.clean_box.currentData())

    def _save_track(self, index: int):
        data = self.track_box.currentData()
        self.main.track_index = int(data if data is not None else 0)

    def _use_model(self, spec):
        self.main.sep_model_key = spec.key

    def _download(self, fn, button: QPushButton, status: QLabel):
        button.setText("下载中…")
        button.setEnabled(False)
        worker = _Downloader(fn, self)
        worker.progress.connect(lambda done, total: status.setText(
            f"{done / 1048576:.0f} MB" + (f" / {total / 1048576:.0f} MB" if total else "")))
        worker.done.connect(lambda ok, msg: self._download_done(ok, msg, button, status))
        worker.start()
        self._worker = worker

    def _download_done(self, ok: bool, message: str,
                       button: QPushButton, status: QLabel):
        if ok:
            status.setText("已下载")
            status.setStyleSheet(f"color: {theme.s('accent')}; font-size: 12px;")
            button.setVisible(False)
            self._rebuild_pages(self.side.currentRow())
        else:
            status.setText("下载失败")
            status.setStyleSheet("color: #FF3B30; font-size: 12px;")
            button.setText("下载")
            button.setEnabled(True)

    def _set_mode(self, key: str):
        self._settings.setValue("appearance", key)
        if key == "light":
            self.main.set_dark(False)
        elif key == "dark":
            self.main.set_dark(True)
        else:
            self.main.apply_system_theme()
        index = self.side.currentRow()
        self._rebuild_pages(index)

    def _refresh_mode_buttons(self):
        current = self._settings.value("appearance", "system")
        for key, btn in self._mode_buttons.items():
            btn.setEnabled(key != current)
