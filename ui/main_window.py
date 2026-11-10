"""主窗口：液态玻璃风格。"""
from __future__ import annotations

import math
import sys
import time

import numpy as np
import soundfile as sf
import traceback
from pathlib import Path

from PySide6.QtCore import (
    QPoint, QPointF, QRectF, QSettings, QSize, Qt, QThread, QTimer, QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QKeySequence, QLinearGradient, QMouseEvent, QPainter,
    QRadialGradient, QShortcut,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QPushButton, QSizeGrip, QSizePolicy, QSlider,
    QStackedWidget, QVBoxLayout, QWidget,
)

from app import export, media, separation_models as sm, speaker_models as spk
from app.align import Utterance
from app.pipeline import run as run_pipeline
from . import dialogs, theme
from .editor import EditorView
from .menus import GlassMenu
from .settings_dialog import SettingsOverlay
from .widgets import (
    AppLogo, DropZone, FileChip, GearButton, ImportButton, LibraryList,
    MorphSlider, PersonRow, PlayButton, SegmentTable, ShineProgress,
    SkipButton, SpringButton, StepRail, SwapButton, ThemeToggle,
    ThemeTransition, Ticker, TimelineWidget, TitleBar, ViewSwitch,
    VolumeButton, fmt_short, fmt_tenths, fmt_time, make_card, make_shadow,
)

STAGE_TEXT = {
    "probe": "读取素材",
    "extract": "提取音轨",
    "separate": "分离人声",
    "transcribe": "转写文字",
    "diarize": "识别说话人",
    "export": "按人导出",
    "done": "完成",
}

# 流水线阶段 → 侧栏步骤耗时归组（0 导入 / 1 清理 / 2 分析）
STAGE_GROUP = {"probe": 0, "extract": 1, "separate": 1,
               "transcribe": 2, "diarize": 2}


class ProbeWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.done.emit(media.probe(self.path))
        except Exception as exc:
            self.failed.emit(str(exc))


class WaveformWorker(QThread):
    done = Signal(object)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        self.done.emit(compute_waveform(self.path))


def compute_waveform(path, bucket_sec: float = 0.1):
    if not path:
        return None
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        step = max(1, int(sr * bucket_sec))
        n = len(data) // step
        if n == 0:
            return None
        block = np.abs(data[: n * step]).reshape(n, step).max(axis=1)
        peak = float(block.max()) or 1.0
        return (block / peak).astype(np.float32)
    except Exception:
        return None


class PipelineWorker(QThread):
    stageChanged = Signal(str, str)
    progressChanged = Signal(float)
    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(self, source: Path, out_dir: Path, options: dict, parent=None):
        super().__init__(parent)
        # 声纹/分割模型在长音频上需要更大的栈，默认 1MB 会导致进程直接崩溃
        self.setStackSize(64 * 1024 * 1024)
        self.source = source
        self.out_dir = out_dir
        self.options = options

    def run(self):
        try:
            project = run_pipeline(
                self.source, self.out_dir,
                on_stage=lambda k, t: self.stageChanged.emit(k, t),
                on_progress=self.progressChanged.emit,
                **self.options)
            self.succeeded.emit(project)
        except Exception as exc:
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SpeakerSplit")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.resize(1240, 840)
        self.setAcceptDrops(True)

        self.source: Path | None = None
        self.media_info: media.MediaInfo | None = None
        self.project: dict | None = None
        self.segments: list[dict] = []
        self.speaker_names: list[str] = []
        self.colors: dict[str, str] = {}
        self.worker: PipelineWorker | None = None
        self._current_filter: str | None = None
        self._loading_table = False
        self._exported = False
        self.sep_model_key = sm.DEFAULT_KEY
        self.spk_model_key = QSettings("SpeakerSplit", "SpeakerSplit").value(
            "spk_model", spk.DEFAULT_KEY)
        self._play_until: float | None = None
        self._editor_for = None

        self._proc_t0 = None
        self._stage_t0 = None
        self._stage_name = ""
        self._group_elapsed = [0.0, 0.0, 0.0]
        self._stage_group = None
        self._stage_t0_group = None
        self._stage_timer = QTimer(self)
        self._stage_timer.setInterval(500)
        self._stage_timer.timeout.connect(self._tick_stage)

        self._ambient_t = 0.0
        self._ambient_timer = QTimer(self)
        self._ambient_timer.setInterval(100)
        self._ambient_timer.timeout.connect(self._tick_ambient)
        self._ambient_timer.start()
        self._ticker = Ticker.instance()
        self._ticker.activeChanged.connect(self._on_anim_active)

        self._build_ui()
        self._transition = ThemeTransition(self)
        self.settings = SettingsOverlay(self)
        shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        shortcut.activated.connect(self.open_settings)
        self._build_player()
        self._sync_state()
        self._refresh_empty_state()

    # ------------------------------------------------------------ 背景
    def _paint_background(self, p: QPainter):
        p.fillRect(self.rect(), theme.c("bg"))

        phase = self._ambient_t * 2 * math.pi
        if theme.dark:
            blobs = (
                (0.10, 0.05, 0.75, QColor(40, 90, 200, 120)),
                (0.95, 0.15, 0.65, QColor(110, 60, 190, 100)),
                (0.55, 1.05, 0.70, QColor(180, 60, 110, 80)),
                (0.02, 0.98, 0.55, QColor(20, 120, 120, 70)),
            )
        else:
            blobs = (
                (0.10, 0.05, 0.75, QColor(95, 160, 255, 110)),
                (0.95, 0.15, 0.65, QColor(198, 139, 255, 95)),
                (0.55, 1.05, 0.70, QColor(255, 143, 177, 85)),
                (0.02, 0.98, 0.55, QColor(91, 224, 196, 75)),
            )
        for i, (cx, cy, radius, color) in enumerate(blobs):
            dx = math.sin(phase + i * 1.6) * 60
            dy = math.cos(phase * 0.8 + i * 1.2) * 40
            scale = 1 + 0.10 * math.sin(phase + i)
            grad = QRadialGradient(self.width() * cx + dx, self.height() * cy + dy,
                                   max(self.width(), self.height()) * radius / 2 * scale)
            grad.setColorAt(0, color)
            grad.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(self.rect(), grad)

    def paint_background_into(self, p: QPainter, src_rect):
        """给子控件（如表格）做不透明背景快照用，只画背景层。"""
        p.save()
        p.translate(-src_rect.x(), -src_rect.y())
        self._paint_background(p)
        p.restore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._paint_background(p)
        self._draw_card_shadows(p)
        # 最外一圈画成近乎透明的黑，系统阴影才会贴着圆角
        edge = QColor(0, 0, 0, 1)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, 1, edge)
        p.fillRect(0, h - 1, w, 1, edge)
        p.fillRect(0, 0, 1, h, edge)
        p.fillRect(w - 1, 0, 1, h, edge)

    # ------------------------------------------------------------ 构建
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.titlebar = TitleBar(self)
        outer.addWidget(self.titlebar)

        # ---- 标题栏内容 ----
        self.btn_settings = GearButton()
        self.btn_settings.setToolTip("设置")
        self.btn_settings.clicked.connect(self.open_settings)
        self.titlebar.left_lay.addWidget(self.btn_settings)
        self.titlebar.left_lay.addWidget(AppLogo(24))

        brand = QLabel("SpeakerSplit")
        brand.setStyleSheet("font-size: 14px; font-weight: 600; letter-spacing: -0.2px;")
        self.titlebar.left_lay.addWidget(brand)

        self.pill = QLabel("")
        self.pill.setVisible(False)
        self.titlebar.left_lay.addWidget(self.pill)

        self.view_switch = ViewSwitch(("检查", "编辑"))
        self.view_switch.set_item_enabled(1, False)
        self.view_switch.changed.connect(self._on_view_switch)
        self.titlebar.center_lay.addWidget(self.view_switch)

        self.file_chip = FileChip()
        self.titlebar.right_lay.addWidget(self.file_chip)

        self.btn_open = SwapButton()
        self.btn_open.setToolTip("换文件")
        self.btn_open.clicked.connect(self.choose_file)
        self.btn_open.setVisible(False)
        self.titlebar.right_lay.addWidget(self.btn_open)

        self.btn_theme = ThemeToggle()
        self.btn_theme.setToolTip("切换浅色 / 深色")
        self.btn_theme.clicked.connect(self.toggle_theme)
        self.titlebar.right_lay.addWidget(self.btn_theme)

        self.btn_primary = SpringButton("选择文件", "primary")
        self.btn_primary.setMinimumWidth(96)
        self.btn_primary.setFixedHeight(32)
        self.btn_primary.clicked.connect(self.primary_action)
        self.btn_primary.setVisible(False)
        self.titlebar.right_lay.addWidget(self.btn_primary)
        self.titlebar.add_window_buttons()

        self.clean_mode = True
        self.track_index = 0
        self.whisper_model = "large-v3-turbo"

        content = QWidget()
        outer.addWidget(content, 1)
        col = QVBoxLayout(content)
        col.setContentsMargins(20, 8, 20, 14)
        col.setSpacing(12)

        # ---- 进度 ----
        self.progress_wrap = QWidget()
        self.progress_wrap.setFixedHeight(20)
        pw = QHBoxLayout(self.progress_wrap)
        pw.setContentsMargins(2, 0, 2, 0)
        pw.setSpacing(12)
        self.stage_label = QLabel("")
        self.stage_label.setFixedWidth(84)
        self.stage_label.setObjectName("StageMeta")
        self.progress = ShineProgress()
        self.pct_label = QLabel("0%")
        self.pct_label.setFixedWidth(36)
        self.pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pct_label.setObjectName("StageMeta")
        pw.addWidget(self.stage_label)
        pw.addWidget(self.progress, 1)
        pw.addWidget(self.pct_label)
        self.progress_wrap.setVisible(False)
        col.addWidget(self.progress_wrap)

        # ---- 主体 ----
        body = QHBoxLayout()
        body.setSpacing(14)

        # 左栏：导入 + 处理步骤 + 说话人
        sidebar, sl = make_card((0, 0, 0, 0), 0)
        self.sidebar = sidebar
        sidebar.setFixedWidth(236)
        sl.setContentsMargins(12, 12, 12, 10)
        sl.setSpacing(10)

        self.btn_import = ImportButton()
        self.btn_import.clicked.connect(self.choose_file)
        sl.addWidget(self.btn_import)

        proc_head = QHBoxLayout()
        proc_head.setContentsMargins(4, 2, 4, 0)
        proc_title = QLabel("处理步骤")
        proc_title.setObjectName("SideTitle")
        self.lbl_proc_total = QLabel("")
        self.lbl_proc_total.setObjectName("SideMeta")
        proc_head.addWidget(proc_title)
        proc_head.addStretch(1)
        proc_head.addWidget(self.lbl_proc_total)
        sl.addLayout(proc_head)

        self.step_rail = StepRail()
        sl.addWidget(self.step_rail)

        spk_head = QHBoxLayout()
        spk_head.setContentsMargins(4, 8, 4, 0)
        self.spk_title = QLabel("说话人")
        self.spk_title.setObjectName("SideTitle")
        self.lbl_spk_total = QLabel("")
        self.lbl_spk_total.setObjectName("SideMeta")
        spk_head.addWidget(self.spk_title)
        spk_head.addStretch(1)
        spk_head.addWidget(self.lbl_spk_total)
        sl.addLayout(spk_head)

        self.people = QListWidget()
        self.people.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.people.setMouseTracking(True)
        self.people.setContextMenuPolicy(Qt.CustomContextMenu)
        self.people.customContextMenuRequested.connect(self.person_menu)
        self.people.setStyleSheet(
            "QListWidget { background: transparent; border: none; }"
            "QListWidget::item { background: transparent; }"
            "QListWidget::item:selected { background: transparent; }")
        sl.addWidget(self.people, 1)

        self.people_empty = QLabel("处理完成后\n这里显示人物")
        self.people_empty.setAlignment(Qt.AlignCenter)
        self.people_empty.setObjectName("Hint")
        sl.addWidget(self.people_empty, 1)

        self.library = LibraryList()
        self.library.setVisible(False)
        sl.addWidget(self.library, 1)

        self.people_hint = QLabel("单击筛选 · Ctrl 多选合并 · 右键改名")
        self.people_hint.setWordWrap(True)
        self.people_hint.setObjectName("SideHint")
        self.people_hint.setVisible(False)
        sl.addWidget(self.people_hint)

        body.addWidget(sidebar)

        # 右栏：空状态或时间轴 + 片段
        main_col = QVBoxLayout()
        main_col.setSpacing(12)

        self.drop_zone = DropZone()
        self.drop_zone.button.clicked.connect(self.choose_file)
        main_col.addWidget(self.drop_zone, 1)

        right = QVBoxLayout()
        right.setSpacing(12)

        tl_card, tl = make_card((0, 0, 0, 0), 0)
        self.tl_card = tl_card
        tl_head = QHBoxLayout()
        tl_head.setContentsMargins(18, 6, 18, 0)
        tl_head.setSpacing(10)
        tl_title = QLabel("时间轴")
        tl_title.setObjectName("SectionTitle")
        tl_hint = QLabel("点击跳转")
        tl_hint.setObjectName("Hint")
        self.lbl_duration = QLabel("")
        self.lbl_duration.setObjectName("Hint")
        self.btn_reset_view = SpringButton("显示全部", "ghost")
        self.btn_reset_view.setVisible(False)
        self.btn_reset_view.clicked.connect(lambda: self.timeline.reset_view())
        tl_head.addWidget(tl_title)
        tl_head.addWidget(tl_hint)
        tl_head.addStretch(1)
        tl_head.addWidget(self.btn_reset_view)
        tl_head.addWidget(self.lbl_duration)
        tl.addLayout(tl_head)

        self.timeline = TimelineWidget()
        self.timeline.seekRequested.connect(self.seek_to)
        self.timeline.viewChanged.connect(self._on_view_changed)
        tl_wrap = QHBoxLayout()
        tl_wrap.setContentsMargins(16, 6, 16, 14)
        tl_wrap.addWidget(self.timeline)
        tl.addLayout(tl_wrap)
        right.addWidget(tl_card)

        table_card, tc = make_card((1, 0, 1, 12), 0)
        self.table_card = table_card
        tb_head = QHBoxLayout()
        tb_head.setContentsMargins(18, 6, 18, 2)
        tb_head.setSpacing(6)
        self.lbl_count = QLabel("片段")
        self.lbl_count.setObjectName("SectionTitle")
        self.btn_clear_filter = QPushButton("显示全部")
        self.btn_clear_filter.setObjectName("Ghost")
        self.btn_clear_filter.setVisible(False)
        self.btn_clear_filter.clicked.connect(lambda: self.filter_by(None))
        tb_head.addWidget(self.lbl_count)
        tb_head.addStretch(1)
        tb_head.addWidget(self.btn_clear_filter)
        tc.addLayout(tb_head)

        self.table = SegmentTable()
        self.table.rowClicked.connect(self.on_table_row_clicked)
        self.table.rowActivated.connect(self.on_table_row_clicked)
        self.table.rowContextMenu.connect(self.table_menu)
        self.table.textEdited.connect(self.on_text_edited)
        tc.addWidget(self.table, 1)

        self.ready_label = QLabel("素材已就绪\n点右上角「开始处理」")
        self.ready_label.setAlignment(Qt.AlignCenter)
        self.ready_label.setObjectName("ReadyHint")
        tc.addWidget(self.ready_label, 1)
        right.addWidget(table_card, 1)
        self.right_box = QWidget()
        self.right_box.setLayout(right)

        edit_card, edit_lay = make_card((0, 0, 0, 0), 0)
        self.edit_card = edit_card
        self.editor_view = EditorView()
        edit_lay.addWidget(self.editor_view)

        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.right_box)
        self.view_stack.addWidget(edit_card)
        main_col.addWidget(self.view_stack, 1)

        body.addLayout(main_col, 1)

        body_widget = QWidget()
        body_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body_widget.setLayout(body)
        col.addWidget(body_widget, 1)

        self.player_bar = self._build_player_bar()
        col.addWidget(self.player_bar)
        self._shadow_cards = [sidebar, tl_card, table_card, self.player_bar,
                              self.drop_zone, edit_card]

        grip = QSizeGrip(self)
        grip.setFixedSize(14, 14)
        grip.setStyleSheet("background: transparent;")
        self._grip = grip

    def _tick_ambient(self):
        self._ambient_t = (self._ambient_t + 0.1 / 24.0) % 1.0
        self.update()

    def _on_anim_active(self, active: bool):
        if active:
            self._ambient_timer.stop()
        elif self.isVisible() and not self._ambient_timer.isActive():
            self._ambient_timer.start()

    def _apply_window_style(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes

            class _Margins(ctypes.Structure):
                _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int),
                            ("t", ctypes.c_int), ("b", ctypes.c_int)]

            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi

            def set_attr(attr, value):
                v = ctypes.c_int(value)
                return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)

            set_attr(33, 2)                    # 圆角（Win11）
            set_attr(20, 1 if theme.dark else 0)  # 深色标题区，系统阴影跟着变深
            dwm.DwmExtendFrameIntoClientArea(
                hwnd, ctypes.byref(_Margins(1, 1, 1, 1)))
        except Exception:
            pass

    def hideEvent(self, event):
        self._ambient_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._ambient_timer.isActive():
            self._ambient_timer.start()
        if not getattr(self, "_window_style_done", False):
            self._window_style_done = True
            self._apply_window_style()

    def _draw_card_shadows(self, p: QPainter):
        cards = getattr(self, "_shadow_cards", [])
        for card in cards:
            if not card.isVisible() or card.width() <= 0 or card.height() <= 0:
                continue
            pos = card.mapTo(self, QPointF(0, 0).toPoint())
            radius = 24 if card is self.drop_zone else 18
            pixmap, pad = make_shadow(card.width(), card.height(), radius=radius,
                                      blur=30, alpha=40)
            p.drawPixmap(int(pos.x()) - pad, int(pos.y()) - pad + 8, pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        grip = getattr(self, "_grip", None)
        if grip is not None:
            grip.move(self.width() - 18, self.height() - 18)
            grip.setVisible(not self.isMaximized())
        settings = getattr(self, "settings", None)
        if settings is not None and settings.isVisible():
            settings.setGeometry(self.rect())

    def _vline(self) -> QFrame:
        line = QFrame()
        line.setObjectName("VLine")
        line.setFrameShape(QFrame.NoFrame)
        line.setFixedHeight(48)
        return line

    def _build_player_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("Card")
        bar.setFixedHeight(90)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ---- 正在播放 ----
        np = QWidget()
        np.setFixedWidth(236)
        npl = QHBoxLayout(np)
        npl.setContentsMargins(14, 0, 14, 0)
        npl.setSpacing(10)
        self.art = QLabel()
        self.art.setFixedSize(44, 44)
        self.art.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 #B8C6DB, stop:1 #8E9EB8); border-radius: 11px;")
        npl.addWidget(self.art)
        np_box = QVBoxLayout()
        np_box.setSpacing(1)
        self.np_title = QLabel("未在播放")
        self.np_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.np_sub = QLabel("—")
        self.np_sub.setObjectName("PlayerSub")
        np_box.addWidget(self.np_title)
        np_box.addWidget(self.np_sub)
        npl.addLayout(np_box, 1)
        lay.addWidget(np)
        lay.addWidget(self._vline())

        # ---- 播放控制 ----
        transport = QWidget()
        tl = QVBoxLayout(transport)
        tl.setContentsMargins(24, 6, 24, 6)
        tl.setSpacing(6)
        ctrls = QHBoxLayout()
        ctrls.setSpacing(10)
        ctrls.addStretch(1)
        self.btn_back = SkipButton(-1)
        self.btn_back.setToolTip("后退 5 秒")
        self.btn_back.clicked.connect(lambda: self.seek_relative(-5))
        self.btn_play = PlayButton()
        self.btn_play.setToolTip("播放 / 暂停（空格）")
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_fwd = SkipButton(1)
        self.btn_fwd.setToolTip("前进 5 秒")
        self.btn_fwd.clicked.connect(lambda: self.seek_relative(5))
        ctrls.addWidget(self.btn_back)
        ctrls.addWidget(self.btn_play)
        ctrls.addWidget(self.btn_fwd)
        ctrls.addStretch(1)
        tl.addLayout(ctrls)

        scrub = QHBoxLayout()
        scrub.setSpacing(10)
        scrub.addStretch(1)
        self.lbl_time = QLabel("0:00.0")
        self.lbl_time.setFixedWidth(56)
        self.lbl_time.setObjectName("PlayerMeta")
        self.pos_slider = MorphSlider()
        self.pos_slider.setRange(0, 1000)
        self.pos_slider.setEnabled(False)
        self.pos_slider.setMaximumWidth(620)
        self.pos_slider.setMinimumWidth(260)
        self.pos_slider.sliderMoved.connect(self.slider_seek)
        self.lbl_remain = QLabel("-0:00.0")
        self.lbl_remain.setFixedWidth(56)
        self.lbl_remain.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_remain.setObjectName("PlayerMeta")
        scrub.addWidget(self.lbl_time)
        scrub.addWidget(self.pos_slider, 1)
        scrub.addWidget(self.lbl_remain)
        scrub.addStretch(1)
        tl.addLayout(scrub)
        lay.addWidget(transport, 1)
        lay.addWidget(self._vline())

        # ---- 音量 ----
        vol = QWidget()
        vol.setFixedWidth(200)
        vl = QHBoxLayout(vol)
        vl.setContentsMargins(18, 0, 18, 0)
        vl.setSpacing(8)
        self.btn_mute = VolumeButton()
        self.btn_mute.setToolTip("静音")
        self.btn_mute.clicked.connect(self.toggle_mute)
        self.vol_slider = MorphSlider()
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(70)
        self.vol_slider.valueChanged.connect(self.on_volume)
        vl.addWidget(self.btn_mute)
        vl.addWidget(self.vol_slider, 1)
        lay.addWidget(vol)
        return bar

    # ------------------------------------------------------------ 编辑
    def _in_edit(self) -> bool:
        return (hasattr(self, "view_stack")
                and self.view_stack.currentWidget() is self.edit_card)

    def _on_view_switch(self, name: str):
        if name == "编辑":
            self._show_editor()
        else:
            self._show_review()

    def _show_editor(self):
        if not self.segments or not self.project:
            self.view_switch.set_index(0, animate=False)
            return
        self.player.pause()
        self.view_stack.setCurrentWidget(self.edit_card)
        if self.editor_view.project is None or self._editor_for != id(self.project):
            from core.edit_model import from_segments

            names = list(self.speaker_names)
            colors = [self.colors.get(n, theme.speakers[i % len(theme.speakers)])
                      for i, n in enumerate(names)]
            edit_project = from_segments(self.project["export_source"],
                                         self.segments, names, colors)
            self.editor_view.bind(edit_project)
            self._editor_for = id(self.project)
        else:
            self.editor_view.fit_zoom()
        self._enter_library()
        self.player_bar.setVisible(False)
        self._sync_state()

    def _show_review(self):
        self.editor_view.stop()
        self._exit_library()
        self.view_stack.setCurrentWidget(self.right_box)
        self.player_bar.setVisible(self.source is not None)
        self._sync_state()

    def _fill_library(self):
        lib = self.library
        lib.clear()
        for name in self.speaker_names:
            rows = [s for s in self.segments
                    if s["speaker"] == name and not s.get("excluded")]
            if not rows:
                continue
            lib.add_group(name)
            color = self.colors.get(name, theme.ACCENT)
            for seg in rows:
                text = seg.get("text") or f"片段 {seg['start']:.1f}s"
                sub = f"{fmt_time(seg['start'])} · {seg['end'] - seg['start']:.1f}s"
                lib.add_segment({
                    "start": float(seg["start"]),
                    "end": float(seg["end"]),
                    "speaker": name,
                    "text": seg.get("text", ""),
                }, color, text, sub)

    def _enter_library(self):
        self._fill_library()
        self.people.setVisible(False)
        self.people_empty.setVisible(False)
        self.library.setVisible(True)
        self.spk_title.setText("素材库")
        self.lbl_spk_total.setText("")
        self.people_hint.setText("拖到右侧轨道即可叠加 / 插入")
        self.people_hint.setVisible(True)

    def _exit_library(self):
        self.library.setVisible(False)
        self.spk_title.setText("说话人")
        self.people_hint.setText("单击筛选 · Ctrl 多选合并 · 右键改名")
        visible = len([n for n in self.speaker_names
                       if any(s["speaker"] == n for s in self.segments)])
        self.people.setVisible(visible > 0)
        self.people_empty.setVisible(visible == 0)
        self.people_hint.setVisible(visible > 0)
        self.lbl_spk_total.setText(f"{visible} 人" if visible else "")

    # ------------------------------------------------------------ 主题
    def open_settings(self):
        self.settings.open()
        self._sync_state()

    def toggle_theme(self):
        self.btn_theme.animate()
        self.set_dark(not theme.dark)

    def set_dark(self, dark: bool, animate: bool = True):
        origin = self.btn_theme.mapTo(self, self.btn_theme.rect().center())

        def apply():
            theme.apply(dark)
            self._apply_theme()
            self._apply_window_style()

        if animate:
            self._transition.run(apply, origin)
        else:
            apply()
        QSettings("SpeakerSplit", "SpeakerSplit").setValue(
            "appearance", "dark" if dark else "light")

    def apply_system_theme(self):
        from PySide6.QtGui import QGuiApplication

        hints = QGuiApplication.styleHints()
        dark = hints.colorScheme() == Qt.ColorScheme.Dark
        self.set_dark(dark, animate=False)
        QSettings("SpeakerSplit", "SpeakerSplit").setValue("appearance", "system")

    def _apply_theme(self):
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.qss())
        if self.speaker_names:
            self.colors = {name: theme.speakers[i % len(theme.speakers)]
                           for i, name in enumerate(self.speaker_names)}
            self._rebuild_people()
            self.filter_by(self._current_filter)
        try:
            self.table._cache.clear()
            self.table.header.update()
            self.table.viewport().update()
        except Exception:
            pass
        self.timeline.update()
        if hasattr(self, "editor_view"):
            self.editor_view.canvas.update()
        self._sync_state()
        self.update()

    # ------------------------------------------------------------ 状态
    def _analyzing(self) -> bool:
        return bool(self.worker and self.worker.isRunning())

    def _sync_state(self):
        analyzing = self._analyzing()
        has_source = self.source is not None
        has_segments = bool(self.segments)

        if not has_source:
            cur, done = 0, set()
        elif analyzing:
            cur, done = 2, {0, 1}
        elif has_segments:
            if self._exported:
                cur, done = 4, {0, 1, 2, 3}
            else:
                cur, done = 3, {0, 1, 2}
        else:
            cur, done = 1, {0}
        self.step_rail.set_state(cur, done)

        if analyzing:
            self.btn_primary.setText("处理中…")
            self.btn_primary.setEnabled(False)
            self._set_pill("处理中", "orange")
            self.ready_label.setText(f"正在处理…\n{self.stage_label.text()}")
            self.people_empty.setText("正在识别说话人…")
        elif has_segments:
            self.btn_primary.setText("导出混音" if self._in_edit() else "导出")
            self.btn_primary.setEnabled(True)
            self._set_pill("已导出" if self._exported else "待检查",
                           "gray" if self._exported else "green")
        elif has_source:
            self.btn_primary.setText("开始处理")
            self.btn_primary.setEnabled(True)
            self._set_pill("已导入", "blue")
        else:
            self.btn_primary.setText("选择文件")
            self.btn_primary.setEnabled(True)
            self.pill.setVisible(False)

        if not analyzing:
            self.people_empty.setText("处理完成后\n这里显示人物")
            if has_source and not has_segments:
                self.ready_label.setText("素材已就绪\n点右上角「开始处理」")

        self.btn_open.setVisible(has_source)
        self.btn_open.setEnabled(not analyzing)
        self.btn_primary.setVisible(has_source)
        self.view_switch.set_item_enabled(1, has_segments and not analyzing)

    def _set_pill(self, text: str, kind: str):
        if theme.dark:
            colors = {
                "blue": ("rgba(10,132,255,46)", "#5AA9FF"),
                "orange": ("rgba(255,149,0,46)", "#FFB340"),
                "green": ("rgba(52,199,89,46)", "#4CD964"),
                "gray": ("rgba(120,120,128,46)", "#A0A0A6"),
            }
        else:
            colors = {
                "blue": ("rgba(0,122,255,36)", "#0066D6"),
                "orange": ("rgba(255,149,0,41)", "#C46A00"),
                "green": ("rgba(52,199,89,43)", "#1F8A3B"),
                "gray": ("rgba(120,120,128,41)", "#636366"),
            }
        bg, fg = colors.get(kind, colors["gray"])
        self.pill.setText(text)
        self.pill.setStyleSheet(
            f"background: {bg}; color: {fg}; border-radius: 10px;"
            f" padding: 0 10px; font-size: 11px; font-weight: 600;")
        self.pill.setFixedHeight(20)
        self.pill.setVisible(True)

    def primary_action(self):
        if self._analyzing():
            return
        if self._in_edit():
            self.editor_view.export_mixed()
            return
        if not self.source:
            self.choose_file()
        elif not self.segments:
            self.start_processing()
        else:
            self.export_result()

    # ------------------------------------------------------------ 导入
    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频或音频", "",
            "媒体文件 (*.mp4 *.mov *.mkv *.avi *.flv *.webm *.wav *.mp3 *.m4a *.aac *.flac *.ogg);;所有文件 (*.*)")
        if path:
            self.load_file(Path(path))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            if self.drop_zone.isVisible():
                self.drop_zone.set_over(True)

    def dragLeaveEvent(self, e):
        self.drop_zone.set_over(False)

    def dropEvent(self, e):
        self.drop_zone.set_over(False)
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_file():
                self.load_file(p)
                return

    def load_file(self, path: Path):
        self._pending_path = path
        self.file_chip.set_info(path.name, "读取中…")
        self.step_rail.set_times([None] * 5)
        self.lbl_proc_total.setText("")
        if self._in_edit():
            self.view_switch.set_index(0, animate=False)
            self._show_review()
        self._probe_worker = ProbeWorker(path, self)
        self._probe_worker.done.connect(
            lambda info: self._on_probe_done(path, info))
        self._probe_worker.failed.connect(
            lambda msg: self._on_probe_failed(msg))
        self._probe_worker.start()

    def _on_probe_failed(self, message: str):
        self.file_chip.clear()
        dialogs.error(self, "无法读取文件", message)

    def _on_probe_done(self, path: Path, info):
        if getattr(self, "_pending_path", None) != path:
            return
        self.source = path
        self.media_info = info
        self.project = None
        self.segments = []
        self.speaker_names = []
        self.colors = {}
        self._current_filter = None
        self._exported = False
        self._clear_views()
        self.player.setSource(QUrl())
        self.pos_slider.setEnabled(False)

        kind = "视频" if info.has_video else "音频"
        self.file_chip.set_info(
            path.name, fmt_time(info.duration),
            f"{path.name} · {fmt_time(info.duration)} · {kind} · "
            f"{len(info.audio_streams)} 条音轨")
        self.track_index = 0

        self._refresh_empty_state()
        self._sync_state()

    def _clear_views(self):
        self.table.set_rows([])
        self.people.clear()
        self.timeline.set_data([], 1.0, {})
        self.lbl_count.setText("片段")
        self.lbl_duration.setText("")
        self.btn_clear_filter.setVisible(False)

    def _refresh_empty_state(self):
        has_source = self.source is not None
        has_segments = bool(self.segments)
        self.drop_zone.setVisible(not has_source)
        self.view_stack.setVisible(has_source)
        self.tl_card.setVisible(has_source)
        self.table_card.setVisible(has_source)
        self.player_bar.setVisible(has_source and not self._in_edit())
        if not has_source:
            self.view_stack.setCurrentWidget(self.right_box)
            self.view_switch.set_index(0, animate=False)
        self.table.setVisible(has_segments)
        self.ready_label.setVisible(has_source and not has_segments)
        self.people.setVisible(has_segments)
        self.people_empty.setVisible(not has_segments)
        self.people_hint.setVisible(has_segments)

    # ------------------------------------------------------------ 处理
    def start_processing(self):
        if not self.source or self._analyzing():
            return
        out_dir = self.source.with_name(self.source.stem + "_output")
        options = {
            "audio_track": int(self.track_index or 0),
            "clean": bool(self.clean_mode),
            "sep_model": self.sep_model_key,
            "whisper_model": self.whisper_model or "large-v3-turbo",
            "spk_model": self.spk_model_key,
        }
        self.progress_wrap.setVisible(True)
        self.progress.setValue(0)
        self.progress.set_active(True)
        self.pct_label.setText("0%")
        self._proc_t0 = time.perf_counter()
        self._stage_t0 = self._proc_t0
        self._stage_name = "读取素材"
        self._group_elapsed = [0.0, 0.0, 0.0]
        self._stage_group = 0
        self._stage_t0_group = self._proc_t0
        self.lbl_proc_total.setText("")
        self.step_rail.set_times([None] * 5)
        self.stage_label.setText("读取素材 · 0.0s")
        self._stage_timer.start()
        self._sync_state()

        self.worker = PipelineWorker(self.source, out_dir, options, self)
        self.worker.stageChanged.connect(self.on_stage)
        self.worker.progressChanged.connect(self.on_progress)
        self.worker.succeeded.connect(self.on_processed)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()
        self._sync_state()

    def on_stage(self, key: str, text: str):
        now = time.perf_counter()
        if self._stage_group is not None and self._stage_t0_group is not None:
            self._group_elapsed[self._stage_group] += now - self._stage_t0_group
        group = STAGE_GROUP.get(key)
        self._stage_group = group
        self._stage_t0_group = now if group is not None else None
        self._push_step_times()
        self._stage_name = STAGE_TEXT.get(key, text)
        self._stage_t0 = time.perf_counter()
        self.stage_label.setText(self._stage_name + " · 0.0s")
        if self._analyzing():
            self.ready_label.setText(f"正在处理…\n{self.stage_label.text()}")
        if key != "transcribe":
            self.progress.setValue(0)
            self.pct_label.setText("0%")

    def _push_step_times(self):
        times = [round(v, 1) if v >= 0.05 else None for v in self._group_elapsed]
        self.step_rail.set_times(times + [None, None])

    def _finish_stage_timing(self):
        if self._stage_group is not None and self._stage_t0_group is not None:
            self._group_elapsed[self._stage_group] += (
                time.perf_counter() - self._stage_t0_group)
        self._stage_group = None
        self._stage_t0_group = None
        self._push_step_times()

    def _tick_stage(self):
        if self._proc_t0 is None:
            return
        now = time.perf_counter()
        total = now - self._proc_t0
        current = now - (self._stage_t0 or now)
        self.stage_label.setText(
            f"{self._stage_name} · {current:.1f}s · 总计 {total:.0f}s")

    def on_progress(self, value: float):
        self.progress.setValue(int(value * 1000))
        self.pct_label.setText(f"{int(value * 100)}%")

    def on_processed(self, project: dict):
        self.worker = None
        self._finish_stage_timing()
        self.lbl_proc_total.setText(f"共 {project['elapsed_seconds']:.1f}s")
        self.project = project
        self._editor_for = None
        if self._in_edit():
            self.view_switch.set_index(0, animate=False)
            self._show_review()
        self.segments = [dict(s, excluded=s.get("excluded", False))
                         for s in project["segments"]]
        self.speaker_names = [s["name"] for s in project["speakers"]]
        self.colors = {name: theme.speakers[i % len(theme.speakers)]
                       for i, name in enumerate(self.speaker_names)}
        self._stage_timer.stop()
        self._proc_t0 = None
        self.progress.setValue(1000)
        self.progress.set_active(False)
        self.pct_label.setText("100%")
        self.stage_label.setText("完成")
        self.file_chip.set_info(
            self.source.name, fmt_time(project["duration"]),
            f"{self.source.name} · {fmt_time(project['duration'])} · "
            f"{len([n for n in self.speaker_names if n != '未分配'])} 位说话人 · "
            f"耗时 {project['elapsed_seconds']:.0f} 秒")
        self.lbl_duration.setText(fmt_time(project["duration"]))
        self._wave_worker = WaveformWorker(project.get("analysis_audio"), self)
        self._wave_worker.done.connect(self.timeline.set_waveform)
        self._wave_worker.start()
        self.timeline.set_data(self.segments, project["duration"], self.colors)
        self.player.setSource(QUrl.fromLocalFile(project["export_source"]))
        self.pos_slider.setEnabled(True)
        self._rebuild_people()
        self.filter_by(None)
        self._refresh_empty_state()
        self._exported = False
        self._sync_state()
        QTimer.singleShot(2500, lambda: self.progress_wrap.setVisible(False))

    def on_failed(self, message: str):
        self.worker = None
        self._stage_group = None
        self._stage_t0_group = None
        self._stage_timer.stop()
        self._proc_t0 = None
        self.progress.set_active(False)
        self.progress_wrap.setVisible(False)
        self._sync_state()
        dialogs.error(self, "处理失败", message[-3000:])

    # ------------------------------------------------------------ 人物
    def _rebuild_people(self):
        self.people.clear()
        visible = 0
        for name in self.speaker_names:
            count = len([s for s in self.segments if s["speaker"] == name])
            if count == 0:
                continue
            item = QListWidgetItem()
            item.setData(Qt.UserRole, name)
            item.setSizeHint(QSize(0, 44))
            self.people.addItem(item)
            seconds = sum(s["end"] - s["start"] for s in self.segments
                          if s["speaker"] == name)
            row = PersonRow(name, self.colors.get(name, theme.ACCENT), count, seconds)
            row.set_selected(name == self._current_filter)
            row.clicked.connect(self.on_person_row_clicked)
            self.people.setItemWidget(item, row)
            visible += 1
        self.people.setVisible(visible > 0)
        self.people_empty.setVisible(visible == 0)
        self.people_hint.setVisible(visible > 0)
        self.lbl_spk_total.setText(f"{visible} 人" if visible else "")

    def filter_by(self, name: str | None):
        self._current_filter = name
        rows = self._visible_rows()
        display = [self._display_row(s) for s in rows]
        self.table.set_rows(display)
        self.table.set_current_row(-1)
        self.lbl_count.setText(f"片段  {len(rows)}")
        self.btn_clear_filter.setVisible(name is not None)
        for i in range(self.people.count()):
            item = self.people.item(i)
            widget = self.people.itemWidget(item)
            if widget is not None:
                widget.set_selected(item.data(Qt.UserRole) == name)
        self.timeline.set_data(rows, (self.project or {}).get("duration", 1.0), self.colors)

    def on_person_row_clicked(self, name: str, ctrl: bool):
        items = [self.people.item(i) for i in range(self.people.count())]
        target = next((it for it in items if it.data(Qt.UserRole) == name), None)
        if ctrl:
            if target is not None:
                target.setSelected(not target.isSelected())
            return
        if name == self._current_filter:
            self.filter_by(None)
            self.people.clearSelection()
        else:
            self.people.clearSelection()
            if target is not None:
                target.setSelected(True)
            self.filter_by(name)

    def on_table_row_clicked(self, index: int):
        rows = self._visible_rows()
        if 0 <= index < len(rows):
            seg = rows[index]
            span = max(2.0, seg["end"] - seg["start"])
            self.timeline.set_view(seg["start"] - span * 0.4, seg["end"] + span * 0.4)
            self.timeline.set_row(index)
            self.table.set_current_row(index)
            self.play_segment(seg)

    def on_text_edited(self, index: int, text: str):
        rows = self._visible_rows()
        if 0 <= index < len(rows):
            rows[index]["text"] = text
            if self.project:
                self.project["segments"] = self.segments
            self.table.set_rows([self._display_row(r) for r in rows])

    def _display_row(self, s: dict) -> dict:
        if s.get("excluded"):
            status = "已排除"
        elif s.get("flag") == "overlap":
            status = "重叠"
        else:
            status = ""
        return {
            "start": fmt_time(s["start"]),
            "end": fmt_time(s["end"]),
            "dur": f"{s['end'] - s['start']:.1f}s",
            "speaker": s["speaker"],
            "color": self.colors.get(s["speaker"], theme.ACCENT),
            "text": s.get("text", ""),
            "status": status,
            "_payload": {
                "start": float(s["start"]),
                "end": float(s["end"]),
                "speaker": s["speaker"],
                "text": s.get("text", ""),
            },
        }

    def _visible_rows(self) -> list[dict]:
        rows = [s for s in self.segments
                if self._current_filter is None or s["speaker"] == self._current_filter]
        rows.sort(key=lambda s: s["start"])
        return rows

    def table_menu(self, global_pos, row: int):
        rows = self._visible_rows()
        if not (0 <= row < len(rows)):
            return
        seg = rows[row]
        excluded = bool(seg.get("excluded"))
        menu = GlassMenu(self)
        menu.show_at(global_pos, [
            ("从头试听这一句", lambda: self.play_segment(seg)),
            (None, None),
            ("恢复此片段" if excluded else "排除此片段",
             lambda: self._toggle_exclude(seg)),
        ])

    def _toggle_exclude(self, seg: dict):
        seg["excluded"] = not seg.get("excluded", False)
        if self.project:
            self.project["segments"] = self.segments
        self.filter_by(self._current_filter)

    def person_menu(self, pos):
        item = self.people.itemAt(pos)
        if not item:
            return
        name = item.data(Qt.UserRole)
        selected = [i.data(Qt.UserRole) for i in self.people.selectedItems()]
        if name not in selected:
            selected = [name]

        items = []
        if len(selected) > 1:
            items.append((f"合并 {len(selected)} 个人物（并入「{selected[0]}」）",
                          lambda: self.merge_people(selected)))
            items.append((None, None))
        items.append(("重命名", lambda: self._rename_dialog(name)))
        menu = GlassMenu(self)
        menu.show_at(self.people.viewport().mapToGlobal(pos), items)

    def _rename_dialog(self, name: str):
        new_name, ok = dialogs.ask_text(self, "重命名", "给这位说话人换个名字", name)
        if ok and new_name:
            self.rename_speaker(name, new_name)

    def merge_people(self, names: list[str]):
        names = [n for n in names if n in self.speaker_names]
        if len(names) < 2:
            return
        target = names[0]
        dropped = set(names[1:])
        for seg in self.segments:
            if seg["speaker"] in dropped:
                seg["speaker"] = target
        for name in dropped:
            self.colors.pop(name, None)
        self.speaker_names = [n for n in self.speaker_names if n not in dropped]
        if self.project:
            self.project["speakers"] = [{"id": i, "name": n}
                                        for i, n in enumerate(self.speaker_names)]
            self.project["segments"] = self.segments
        self.people.clearSelection()
        self._rebuild_people()
        self.filter_by(None)

    def rename_speaker(self, old: str, new: str):
        for s in self.segments:
            if s["speaker"] == old:
                s["speaker"] = new
        if old in self.colors:
            self.colors[new] = self.colors.pop(old)
        self.speaker_names = [new if n == old else n for n in self.speaker_names]
        if self.project:
            self.project["speakers"] = [{"id": i, "name": n}
                                        for i, n in enumerate(self.speaker_names)]
            self.project["segments"] = self.segments
        self._rebuild_people()
        self.filter_by(new if self._current_filter == old else self._current_filter)

    # ------------------------------------------------------------ 播放
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            if self._play_until is not None and \
                    self.player.position() >= self._play_until * 1000 - 50:
                self._play_until = None
            self.player.play()

    def play_segment(self, segment: dict):
        self._play_until = float(segment["end"])
        self.player.setPosition(int(segment["start"] * 1000))
        self.timeline.set_position(segment["start"])
        self.player.play()

    def seek_to(self, seconds: float):
        self._play_until = None
        self.player.setPosition(int(seconds * 1000))
        self.timeline.set_position(seconds)
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            self.player.play()

    def seek_relative(self, delta: float):
        pos = max(0.0, self.player.position() / 1000 + delta)
        self.seek_to(pos)

    def slider_seek(self, value: int):
        self._play_until = None
        duration = self.player.duration()
        if duration > 0:
            self.player.setPosition(int(duration * value / 1000))

    def on_player_position(self, ms: int):
        if self._play_until is not None and ms >= self._play_until * 1000 - 30:
            self.player.pause()
            self._play_until = None
        duration = self.player.duration()
        self._update_time_labels(ms, duration)
        if not self.pos_slider.isSliderDown() and duration > 0:
            self.pos_slider.setValue(int(ms / duration * 1000))
        self.timeline.set_position(ms / 1000)
        self._update_now_playing(ms / 1000)

    def on_player_duration(self, ms: int):
        self._update_time_labels(self.player.position(), ms)

    def _update_time_labels(self, ms: int, duration: int):
        self.lbl_time.setText(fmt_tenths(ms / 1000))
        remain = max(0, duration - ms)
        self.lbl_remain.setText("-" + fmt_tenths(remain / 1000))

    def _update_now_playing(self, t: float):
        seg = None
        for s in self.segments:
            if not s.get("excluded") and s["start"] <= t <= s["end"]:
                seg = s
                break
        if seg:
            color = self.colors.get(seg["speaker"], theme.ACCENT)
            self.np_title.setText(f"● {seg['speaker']}")
            self.np_title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {color};")
            self.np_sub.setText(seg.get("text", ""))
            self.art.setStyleSheet(
                "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                f"stop:0 {color}, stop:1 {color}AA); border-radius: 11px;")
        elif self.source:
            self.np_title.setText(self.source.name)
            self.np_title.setStyleSheet("font-size: 13px; font-weight: 600;")
            self.np_sub.setText("SpeakerSplit · 本地处理")
            self.art.setStyleSheet(
                "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                "stop:0 #B8C6DB, stop:1 #8E9EB8); border-radius: 11px;")

    def on_playback_state(self, state):
        self.btn_play.set_playing(state == QMediaPlayer.PlayingState)

    def on_volume(self, value: int):
        self.audio_out.setVolume(value / 100)
        self.btn_mute.set_muted(value == 0)

    def toggle_mute(self):
        if self.vol_slider.value() > 0:
            self._prev_volume = self.vol_slider.value()
            self.vol_slider.setValue(0)
        else:
            self.vol_slider.setValue(getattr(self, "_prev_volume", 70))

    # ------------------------------------------------------------ 项目数据
    @staticmethod
    def _compute_waveform(path, bucket_sec: float = 0.1):
        if not path:
            return None
        try:
            import numpy as np
            import soundfile as sf

            data, sr = sf.read(str(path), dtype="float32", always_2d=False)
            if data.ndim > 1:
                data = data.mean(axis=1)
            step = max(1, int(sr * bucket_sec))
            n = len(data) // step
            if n == 0:
                return None
            block = np.abs(data[: n * step]).reshape(n, step).max(axis=1)
            peak = float(block.max()) or 1.0
            return (block / peak).astype(np.float32)
        except Exception:
            return None

    # ------------------------------------------------------------ 导出
    def export_result(self):
        if not self.segments or not self.project:
            return
        base = self.source.parent / "导出" if self.source else Path.cwd() / "导出"
        chosen = QFileDialog.getExistingDirectory(self, "选择导出文件夹", str(base))
        if not chosen:
            return
        try:
            utterances = [
                Utterance(s["start"], s["end"], s["speaker"], s.get("text", ""),
                          excluded=s.get("excluded", False))
                for s in self.segments
            ]
            names = {n: n for n in self.speaker_names}
            result = export.export_by_speaker(
                Path(self.project["export_source"]), utterances, names, Path(chosen))
        except Exception as exc:
            dialogs.error(self, "导出失败", str(exc))
            return
        lines = [f"{s['name']}：{s['seconds'] / 60:.1f} 分钟" for s in result["speakers"]]
        self._exported = True
        self._sync_state()
        dialogs.info(self, "导出完成", f"已导出到：\n{chosen}\n\n" + "\n".join(lines))

    def closeEvent(self, event):
        if self._analyzing():
            if not dialogs.confirm(self, "正在处理", "处理还没完成，确定要退出吗？", "退出"):
                event.ignore()
                return
            self.worker.terminate()
            self.worker.wait(3000)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.settings.isVisible():
            self.settings.close_()
            event.accept()
            return
        editing = isinstance(QApplication.focusWidget(), QLineEdit)
        if self._in_edit() and not editing:
            mod = event.modifiers() & Qt.ControlModifier
            if mod and event.key() == Qt.Key_Z:
                if event.modifiers() & Qt.ShiftModifier:
                    self.editor_view.undo.redo()
                else:
                    self.editor_view.undo.undo()
                event.accept()
                return
            if mod and event.key() == Qt.Key_D:
                self.editor_view.duplicate_selection()
                event.accept()
                return
            if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
                if event.modifiers() & Qt.ShiftModifier:
                    self.editor_view.ripple_delete()
                else:
                    self.editor_view.delete_selection()
                event.accept()
                return
            if event.key() == Qt.Key_S and not mod:
                self.editor_view.split_at_playhead()
                event.accept()
                return
        if event.key() == Qt.Key_Space:
            if not editing:
                if self._in_edit():
                    self.editor_view.toggle_play()
                else:
                    self.toggle_play()
                event.accept()
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------ 播放器
    def _build_player(self):
        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.audio_out.setVolume(0.7)
        self.player.setAudioOutput(self.audio_out)
        self.player.positionChanged.connect(self.on_player_position)
        self.player.durationChanged.connect(self.on_player_duration)
        self.player.playbackStateChanged.connect(self.on_playback_state)

    def _on_view_changed(self):
        self.btn_reset_view.setVisible(self.timeline.is_zoomed())
