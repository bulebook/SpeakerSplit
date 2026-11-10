"""音频编辑视图：工具栏、轨道画布、缩放、撤销、试听与混音导出。"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import numpy as np
from PySide6.QtCore import (
    QLineF, QPoint, QPointF, QRectF, Qt, QThread, QUrl, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPen, QUndoCommand, QUndoStack,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QSlider,
    QVBoxLayout, QWidget,
)

from core import edit_ops
from core.edit_model import SEGMENT_MIME, Clip, EditProject
from core.mixer import export_wav, render

from . import dialogs, theme
from .menus import GlassMenu
from .widgets import SpringButton, ViewSwitch, fmt_short, fmt_tenths

HEADER_W = 168
RULER_H = 26
LANE_H = 62
BTN = 20
PEAK_BUCKET = 0.02
PPS_MIN, PPS_MAX = 6.0, 160.0

_PEAK_CACHE: dict[str, tuple[np.ndarray, float]] = {}


def compute_peaks(path: str, bucket_sec: float = PEAK_BUCKET):
    """流式读取，返回归一化峰值和每秒采样数；避免整段载入内存。"""
    import soundfile as sf

    parts = []
    with sf.SoundFile(str(path)) as f:
        step = max(1, int(round(f.samplerate * bucket_sec)))
        chunk = step * 512
        while True:
            data = f.read(chunk, dtype="float32", always_2d=True)
            if data is None or len(data) == 0:
                break
            mono = data.mean(axis=1)
            n = len(mono) // step
            if n:
                parts.append(np.abs(mono[: n * step]).reshape(n, step).max(axis=1))
    if not parts:
        return None, 1.0 / bucket_sec
    peaks = np.concatenate(parts)
    peak = float(peaks.max()) or 1.0
    return (peaks / peak).astype(np.float32), 1.0 / bucket_sec


class _PeaksWorker(QThread):
    done = Signal(str, object, float)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            peaks, rate = compute_peaks(self.path)
            if peaks is not None:
                self.done.emit(self.path, peaks, rate)
        except Exception:
            pass


class _RenderWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, project: EditProject, path: Path | None, parent=None):
        super().__init__(parent)
        self.project = project
        self.path = path

    def run(self):
        try:
            if self.path is None:
                audio = render(self.project)
                tmp = Path(tempfile.gettempdir()) / "speakersplit_preview.wav"
                import soundfile as sf
                sf.write(str(tmp), audio, self.project.sr, subtype="PCM_16")
                self.done.emit(True, str(tmp))
            else:
                export_wav(self.project, self.path)
                self.done.emit(True, str(self.path))
        except Exception as exc:
            self.done.emit(False, str(exc))


class TrackCanvas(QWidget):
    seekRequested = Signal(float)
    editFinished = Signal(str)
    contextRequested = Signal(QPoint, int, str)
    trackToggled = Signal(int, str)
    segmentDropped = Signal(dict, int, float)
    selectionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: EditProject | None = None
        self.playhead = 0.0
        self.selection: set[str] = set()
        self.selected: tuple[int, str] | None = None
        self.snap_enabled = True
        self._pps = None
        self._drag = None
        self._before = None
        self._peaks: dict[str, tuple[np.ndarray, float]] = {}
        self._pending: set[str] = set()
        self._workers: list[_PeaksWorker] = []
        self.setMinimumHeight(240)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

    def set_project(self, project: EditProject):
        self.project = project
        self.selection.clear()
        self.selected = None
        self._ensure_peaks()
        self._update_width()
        self.update()
        self.selectionChanged.emit()

    def set_pps(self, pps: float):
        self._pps = max(PPS_MIN, min(PPS_MAX, pps))
        self._update_width()
        self.update()

    def _update_width(self):
        if not self.project or not self._pps:
            return
        self.setMinimumWidth(int(HEADER_W + self.project.duration * self._pps + 40))

    def set_playhead(self, t: float):
        if abs(t - self.playhead) < 0.01:
            return
        self.playhead = t
        self.update()

    def _ensure_peaks(self):
        if not self.project:
            return
        for src in {c.source for t in self.project.tracks for c in t.clips}:
            if src in self._peaks or src in self._pending:
                continue
            self._pending.add(src)
            worker = _PeaksWorker(src, self)
            worker.done.connect(self._on_peaks)
            self._workers.append(worker)
            worker.start()

    def _on_peaks(self, path: str, peaks, rate: float):
        self._pending.discard(path)
        _PEAK_CACHE[path] = (peaks, rate)
        self._peaks[path] = (peaks, rate)
        self.update()

    # ---------- 坐标 ----------
    def _scale(self) -> float:
        duration = max(0.5, (self.project.duration if self.project else 0.5))
        return max(1e-3, (self.width() - HEADER_W - 12) / duration)

    def _x(self, t: float) -> float:
        return HEADER_W + t * self._scale()

    def _t(self, x: float) -> float:
        return max(0.0, (x - HEADER_W) / self._scale())

    def _lane_top(self, index: int) -> float:
        return RULER_H + 8 + index * LANE_H

    def _lane_at(self, y: float) -> int | None:
        if not self.project:
            return None
        index = int((y - RULER_H - 8) // LANE_H)
        return index if 0 <= index < len(self.project.tracks) else None

    def _header_buttons(self, index: int) -> dict[str, QRectF]:
        top = self._lane_top(index) + LANE_H / 2 - BTN / 2
        return {
            "mute": QRectF(HEADER_W - 2 * BTN - 14, top, BTN, BTN),
            "solo": QRectF(HEADER_W - BTN - 8, top, BTN, BTN),
        }

    def _clip_rect(self, track_index: int, clip: Clip) -> QRectF:
        x = self._x(clip.start)
        w = max(3.0, clip.dur * self._scale())
        return QRectF(x, self._lane_top(track_index) + 8, w, LANE_H - 18)

    def _hit(self, pos: QPoint):
        if not self.project:
            return None
        for ti, track in enumerate(self.project.tracks):
            top = self._lane_top(ti)
            if not (top <= pos.y() <= top + LANE_H):
                continue
            for clip in track.clips:
                rect = self._clip_rect(ti, clip)
                if rect.contains(pos.x(), pos.y()):
                    return ti, clip
        return None

    # ---------- 绘制 ----------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), theme.c("table_bg"))
        if not self.project:
            return
        w = self.width()
        duration = max(0.5, self.project.duration)

        p.setPen(QPen(theme.c("border"), 1))
        p.drawLine(0, RULER_H - 1, w, RULER_H - 1)
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        p.setFont(font)
        scale = self._scale()
        minor = next((s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120) if s * scale >= 9), 300)
        major = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300) if s * scale >= 70), 600)
        t = 0.0
        while t <= duration:
            x = self._x(t)
            is_major = abs((t / major) - round(t / major)) < 1e-6
            p.setPen(QPen(theme.c("border"), 1))
            p.drawLine(int(x), RULER_H - (6 if is_major else 3), int(x), RULER_H - 1)
            if is_major:
                p.setPen(theme.c("text3"))
                p.drawText(QRectF(x - 24, 2, 48, 16), Qt.AlignHCenter, fmt_short(t))
            t += minor

        any_solo = any(tr.solo for tr in self.project.tracks)
        for ti, track in enumerate(self.project.tracks):
            top = self._lane_top(ti)
            p.setPen(Qt.NoPen)
            p.setBrush(theme.c("hover") if ti % 2 == 0 else QColor(0, 0, 0, 0))
            p.drawRect(QRectF(0, top, w, LANE_H))

            p.setBrush(QColor(track.color))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(10, top + LANE_H / 2 - 4, 8, 8))

            p.setPen(theme.c("text2"))
            f = QFont(self.font())
            f.setPixelSize(12)
            f.setBold(True)
            p.setFont(f)
            name_w = int(self._header_buttons(ti)["mute"].left() - 24 - 6)
            name = QFontMetrics(f).elidedText(track.name, Qt.ElideRight, name_w)
            p.drawText(QRectF(24, top, name_w, LANE_H),
                       Qt.AlignLeft | Qt.AlignVCenter, name)

            for kind, rect in self._header_buttons(ti).items():
                on = track.mute if kind == "mute" else track.solo
                if on:
                    bg = QColor("#FF3B30" if kind == "mute" else "#FF9F0A")
                else:
                    bg = QColor(theme.c("text3"))
                    bg.setAlpha(30)
                p.setPen(Qt.NoPen)
                p.setBrush(bg)
                p.drawEllipse(rect)
                f3 = QFont(self.font())
                f3.setPixelSize(10)
                f3.setBold(True)
                p.setFont(f3)
                p.setPen(QColor(255, 255, 255, 235) if on else theme.c("text2"))
                p.drawText(rect, Qt.AlignCenter, "M" if kind == "mute" else "S")

            dim = track.mute or (any_solo and not track.solo)
            for clip in track.clips:
                rect = self._clip_rect(ti, clip)
                base = QColor(track.color)
                fill = QColor(base)
                fill.setAlpha(52 if dim else 72)
                p.setPen(QPen(QColor(base) if not dim else QColor(base.darker(120)), 1))
                p.setBrush(fill)
                p.drawRoundedRect(rect, 9, 9)

                if not dim:
                    p.save()
                    p.setClipRect(rect.adjusted(2, 18, -2, -4))
                    self._draw_wave(p, rect, clip, base)
                    p.restore()
                    self._draw_fades(p, rect, clip)

                label_color = QColor(theme.c("text"))
                label_color.setAlpha(120 if dim else 210)
                p.setPen(label_color)
                f2 = QFont(self.font())
                f2.setPixelSize(10.5)
                f2.setBold(True)
                p.setFont(f2)
                if rect.width() > 26:
                    p.drawText(rect.adjusted(8, 3, -8, -rect.height() + 18),
                               Qt.AlignLeft | Qt.AlignTop, track.name)
                f2.setPixelSize(9.5)
                f2.setBold(False)
                p.setFont(f2)
                label_color.setAlpha(90 if dim else 165)
                p.setPen(label_color)
                if rect.width() > 52:
                    p.drawText(rect.adjusted(8, 0, -8, -4),
                               Qt.AlignLeft | Qt.AlignBottom, f"{clip.dur:.1f}s")
                if clip.gain_db and rect.width() > 40:
                    label_color.setAlpha(110 if dim else 220)
                    p.setPen(label_color)
                    p.drawText(rect.adjusted(6, 0, -6, 0),
                               Qt.AlignRight | Qt.AlignBottom,
                               f"{clip.gain_db:+.0f} dB")

                if clip.id in self.selection:
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(theme.c("accent"), 2))
                    p.drawRoundedRect(rect, 9, 9)

        x = self._x(self.playhead)
        p.setPen(QPen(theme.c("accent"), 1.6))
        p.drawLine(int(x), RULER_H - 4, int(x), self.height() - 4)

    def _draw_wave(self, p: QPainter, rect: QRectF, clip: Clip,
                   base: QColor | None = None):
        item = self._peaks.get(clip.source) or _PEAK_CACHE.get(clip.source)
        if item is None:
            return
        peaks, rate = item
        i0 = max(0, int(clip.src_in * rate))
        i1 = min(len(peaks), int(round(clip.src_out * rate)))
        if i1 <= i0:
            return
        sub = peaks[i0:i1]
        cols = max(1, int(rect.width()))
        if len(sub) > cols:
            sub = sub[np.linspace(0, len(sub) - 1, cols).astype(np.int32)]
        mid = rect.center().y() + 8
        half = rect.height() / 2 - 12
        step = rect.width() / len(sub)
        wave_color = (base or QColor(theme.ACCENT)).darker(115)
        wave_color.setAlpha(150)
        p.setPen(QPen(wave_color, 1))
        p.drawLines([QLineF(rect.left() + i * step, mid - float(v) * half,
                            rect.left() + i * step, mid + float(v) * half)
                     for i, v in enumerate(sub)])

    def _draw_fades(self, p: QPainter, rect: QRectF, clip: Clip):
        scale = self._scale()
        if clip.fade_in > 0:
            fw = min(rect.width(), clip.fade_in * scale)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 60))
            p.drawPolygon(QPointF(rect.left(), rect.top()),
                          QPointF(rect.left() + fw, rect.top()),
                          QPointF(rect.left(), rect.bottom()))
        if clip.fade_out > 0:
            fw = min(rect.width(), clip.fade_out * scale)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 60))
            p.drawPolygon(QPointF(rect.right() - fw, rect.top()),
                          QPointF(rect.right(), rect.top()),
                          QPointF(rect.right(), rect.bottom()))

    # ---------- 交互 ----------
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if e.position().y() < RULER_H:
            self.seekRequested.emit(self._t(e.position().x()))
            return
        if e.position().x() < HEADER_W:
            lane = self._lane_at(e.position().y())
            if lane is not None:
                for kind, rect in self._header_buttons(lane).items():
                    if rect.contains(e.position()):
                        self.trackToggled.emit(lane, kind)
                        return
        hit = self._hit(e.position().toPoint())
        if hit:
            ti, clip = hit
            multi = bool(e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier))
            if multi:
                if clip.id in self.selection:
                    self.selection.discard(clip.id)
                else:
                    self.selection.add(clip.id)
                self.selected = (ti, clip.id) if clip.id in self.selection else None
                self.update()
                self.selectionChanged.emit()
                return
            if clip.id not in self.selection:
                self.selection = {clip.id}
                self.selectionChanged.emit()
            self.selected = (ti, clip.id)
            rect = self._clip_rect(ti, clip)
            edge = 7
            mode = "move"
            if e.position().x() - rect.left() <= edge:
                mode = "left"
            elif rect.right() - e.position().x() <= edge:
                mode = "right"
            self._drag = (mode, ti, clip, e.position().x(), clip.start,
                          clip.src_in, clip.src_out)
            self._before = copy.deepcopy(self.project)
            self.update()
        else:
            if self.selection:
                self.selection.clear()
                self.selectionChanged.emit()
            self.selected = None
            self.seekRequested.emit(self._t(e.position().x()))
            self.update()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        mode, ti, clip, x0, start0, in0, out0 = self._drag
        delta = self._t(e.position().x()) - self._t(x0)

        def snap(value: float) -> float:
            if not self.snap_enabled:
                return value
            return round(value * 20) / 20

        if mode == "move":
            target = self._lane_at(e.position().y())
            if target is not None and target != ti:
                old = self.project.tracks[ti]
                if clip in old.clips:
                    old.clips.remove(clip)
                self.project.tracks[target].clips.append(clip)
                ti = target
                self._drag = (mode, ti, clip, x0, start0, in0, out0)
                self.selected = (ti, clip.id)
            clip.start = max(0.0, snap(start0 + delta))
        elif mode == "left":
            new_in = max(0.0, snap(in0 + delta))
            new_in = min(new_in, out0 - 0.05)
            clip.src_in = new_in
            clip.start = max(0.0, snap(start0 + (new_in - in0)))
        else:
            clip.src_out = max(in0 + 0.05, snap(out0 + delta))
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None and self._before is not None:
            name = {"move": "移动片段", "left": "裁剪左侧",
                    "right": "裁剪右侧"}[self._drag[0]]
            self.editFinished.emit(name)
            self._before = None
        self._drag = None
        self.update()

    def contextMenuEvent(self, e):
        hit = self._hit(e.pos())
        if not hit:
            return
        ti, clip = hit
        if clip.id not in self.selection:
            self.selection = {clip.id}
            self.selectionChanged.emit()
        self.selected = (ti, clip.id)
        self.update()
        self.contextRequested.emit(e.globalPos(), ti, clip.id)

    # ---------- 从检查表格拖入 ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(SEGMENT_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(SEGMENT_MIME):
            e.acceptProposedAction()

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(SEGMENT_MIME) or not self.project:
            return
        try:
            payload = json.loads(
                bytes(e.mimeData().data(SEGMENT_MIME)).decode("utf-8"))
        except Exception:
            return
        pos = e.position()
        lane = self._lane_at(pos.y())
        if lane is None:
            names = [t.name for t in self.project.tracks]
            lane = names.index(payload.get("speaker")) if payload.get("speaker") in names else 0
        if self.project.tracks:
            self.segmentDropped.emit(payload, lane, self._t(pos.x()))
            e.acceptProposedAction()


class _SnapshotCommand(QUndoCommand):
    def __init__(self, view, text, before, after):
        super().__init__(text)
        self.view = view
        self.before = before
        self.after = after

    def undo(self):
        self.view.project = copy.deepcopy(self.before)
        self.view.refresh()

    def redo(self):
        self.view.project = copy.deepcopy(self.after)
        self.view.refresh()


class EditorView(QWidget):
    """内嵌音频编辑视图：工具栏 + 轨道画布 + 试听。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: EditProject | None = None
        self.undo = QUndoStack(self)
        self._worker = None
        self.insert_mode = False
        self._dirty = True
        self._free_pan = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 工具栏 ----
        bar = QWidget()
        bar.setFixedHeight(46)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 6, 14, 4)
        bl.setSpacing(4)

        self.mode = ViewSwitch(("叠加", "插入"))
        self.mode.setToolTip("拖入音频时的处理方式：叠加或插入（后面内容后移）")
        self.mode.changed.connect(self._on_mode)
        bl.addWidget(self.mode)
        bl.addSpacing(8)

        def tool(text, slot, tip=""):
            btn = SpringButton(text, "bar")
            btn.setMinimumHeight(30)
            btn.clicked.connect(slot)
            if tip:
                btn.setToolTip(tip)
            bl.addWidget(btn)
            return btn

        self.btn_undo = tool("撤销", self.undo.undo, "撤销（Ctrl+Z）")
        self.btn_redo = tool("重做", self.undo.redo, "重做（Ctrl+Shift+Z）")
        bl.addSpacing(8)
        self.btn_split = tool("切开", self.split_at_playhead, "在播放头处切开")
        self.btn_dup = tool("复制", self.duplicate_selection, "复制选中片段（Ctrl+D）")
        self.btn_del = tool("删除", self.delete_selection, "删除选中片段")
        self.btn_ripple = tool("波纹删除", self.ripple_delete, "删除并前移后面的内容")
        bl.addSpacing(8)
        self.btn_add = tool("轨道", self.add_track, "新建一条轨道")
        self.btn_snap = tool("吸附", self.toggle_snap, "吸附到 0.05 秒")
        self.btn_snap.setChecked(True)

        bl.addStretch(1)
        self.zoom_label = QLabel("缩放")
        self.zoom_label.setObjectName("Hint")
        bl.addWidget(self.zoom_label)
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(0, 1000)
        self.zoom.setFixedWidth(110)
        self.zoom.valueChanged.connect(self._on_zoom)
        bl.addWidget(self.zoom)
        self.btn_export = SpringButton("导出混音", "primary")
        self.btn_export.setFixedHeight(30)
        self.btn_export.setMinimumWidth(88)
        self.btn_export.clicked.connect(self.export_mixed)
        bl.addWidget(self.btn_export)
        root.addWidget(bar)

        # ---- 画布 ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        self.canvas = TrackCanvas()
        self.canvas.seekRequested.connect(self.seek)
        self.canvas.editFinished.connect(self._push_edit)
        self.canvas.contextRequested.connect(self._clip_menu)
        self.canvas.trackToggled.connect(self._toggle_track)
        self.canvas.segmentDropped.connect(self._drop_segment)
        self.canvas.selectionChanged.connect(self._update_status)
        self.scroll.setWidget(self.canvas)
        root.addWidget(self.scroll, 1)

        # ---- 试听条 ----
        tr = QWidget()
        tr.setFixedHeight(40)
        tl = QHBoxLayout(tr)
        tl.setContentsMargins(14, 2, 14, 4)
        tl.setSpacing(10)
        self.btn_play = SpringButton("播放", "glass")
        self.btn_play.setFixedHeight(30)
        self.btn_play.setMinimumWidth(64)
        self.btn_play.clicked.connect(self.toggle_play)
        tl.addWidget(self.btn_play)
        self.time_label = QLabel("0:00.0 / 0:00.0")
        self.time_label.setObjectName("Hint")
        tl.addWidget(self.time_label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self._slider_seek)
        tl.addWidget(self.slider, 1)
        root.addWidget(tr)

        # ---- 状态栏 ----
        st = QWidget()
        st.setFixedHeight(26)
        sl = QHBoxLayout(st)
        sl.setContentsMargins(14, 0, 14, 6)
        sl.setSpacing(10)
        self.lbl_left = QLabel("")
        self.lbl_left.setObjectName("Hint")
        self.lbl_right = QLabel("")
        self.lbl_right.setObjectName("Hint")
        sl.addWidget(self.lbl_left)
        sl.addStretch(1)
        sl.addWidget(self.lbl_right)
        root.addWidget(st)

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.audio_out.setVolume(0.8)
        self.player.setAudioOutput(self.audio_out)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)

        self.undo.indexChanged.connect(self._sync_tools)

    # ---------- 数据 ----------
    def bind(self, project: EditProject):
        self.project = project
        self.undo.clear()
        self.canvas.set_project(project)
        self.canvas.setMinimumHeight(
            RULER_H + 8 + max(1, len(project.tracks)) * LANE_H + 20)
        self.player.stop()
        self.player.setSource(QUrl())
        self.btn_play.setText("播放")
        self._dirty = True
        self.fit_zoom()
        self._update_status()
        self._sync_tools()

    def refresh(self):
        self.canvas.set_project(self.project)
        self.canvas.setMinimumHeight(
            RULER_H + 8 + max(1, len(self.project.tracks)) * LANE_H + 20)
        self.player.stop()
        self.player.setSource(QUrl())
        self.btn_play.setText("播放")
        self._dirty = True
        self._update_status()
        self.canvas.update()

    def fit_zoom(self):
        if not self.project:
            return
        viewport = max(400, self.scroll.viewport().width())
        pps = (viewport - HEADER_W - 40) / max(1.0, self.project.duration)
        pps = max(PPS_MIN, min(PPS_MAX, pps))
        self.canvas.set_pps(pps)
        self._sync_zoom_slider(pps)

    def _pps(self) -> float:
        value = self.canvas._pps or 40.0
        return value

    def _on_zoom(self, value: int):
        ratio = PPS_MAX / PPS_MIN
        pps = PPS_MIN * (ratio ** (value / 1000.0))
        self.canvas.set_pps(pps)

    def _sync_zoom_slider(self, pps: float):
        ratio = PPS_MAX / PPS_MIN
        value = int(round(1000 * np.log(pps / PPS_MIN) / np.log(ratio)))
        self.zoom.blockSignals(True)
        self.zoom.setValue(max(0, min(1000, value)))
        self.zoom.blockSignals(False)

    # ---------- 撤销 ----------
    def _push_edit(self, name: str):
        before = getattr(self.canvas, "_before", None)
        if before is None:
            return
        self.undo.push(_SnapshotCommand(self, name, before,
                                        copy.deepcopy(self.project)))
        self.canvas._before = None

    def _mutate(self, fn, name: str):
        before = copy.deepcopy(self.project)
        fn()
        self.undo.push(_SnapshotCommand(self, name, before,
                                        copy.deepcopy(self.project)))
        self.refresh()

    def _sync_tools(self, *_):
        self.btn_undo.setEnabled(self.undo.canUndo())
        self.btn_redo.setEnabled(self.undo.canRedo())
        self._update_status()

    # ---------- 状态 ----------
    def _update_status(self, *_):
        if not self.project:
            return
        p = self.project
        n = sum(len(t.clips) for t in p.tracks)
        left = f"工程 {fmt_short(p.duration)} · {len(p.tracks)} 轨 · {n} 段"
        if self.canvas.selection:
            total = sum(c.dur for t in p.tracks for c in t.clips
                        if c.id in self.canvas.selection)
            left += f" · 已选 {len(self.canvas.selection)} 段，共 {total:.1f}s"
        self.lbl_left.setText(left)
        mode = "插入：后面的内容整体后移" if self.insert_mode else "叠加：与其他轨同时播放"
        self.lbl_right.setText(
            f"播放头 {fmt_tenths(self.canvas.playhead)} · {mode}")

    def _on_mode(self, name: str):
        self.insert_mode = name == "插入"
        self._update_status()

    # ---------- 播放 ----------
    def seek(self, t: float):
        self.player.setPosition(int(t * 1000))
        self.canvas.set_playhead(t)
        self._update_status()

    def _slider_seek(self, value: int):
        duration = self.project.duration if self.project else 0.0
        self.seek(duration * value / 1000)

    def _on_position(self, ms: int):
        self.canvas.set_playhead(ms / 1000)
        duration = self.project.duration if self.project else 0.0
        self.time_label.setText(
            f"{fmt_tenths(ms / 1000)} / {fmt_tenths(duration)}")
        self._update_status()
        dur = self.player.duration()
        if dur > 0 and not self.slider.isSliderDown():
            self.slider.setValue(int(ms / dur * 1000))

    def _on_duration(self, ms: int):
        self._update_status()

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.btn_play.setText("播放")
            return
        if self.player.source().isEmpty() or self._dirty:
            self._render_preview()
        else:
            self.player.play()
            self.btn_play.setText("暂停")

    def stop(self):
        self.player.pause()
        self.btn_play.setText("播放")

    def _render_preview(self):
        self.btn_play.setEnabled(False)
        self.btn_play.setText("渲染中…")
        self._worker = _RenderWorker(self.project, None, self)
        self._worker.done.connect(self._preview_done)
        self._worker.start()

    def _preview_done(self, ok: bool, result: str):
        self.btn_play.setEnabled(True)
        if not ok:
            dialogs.error(self, "试听渲染失败", result[-1500:])
            self.btn_play.setText("播放")
            return
        self._dirty = False
        self.player.setSource(QUrl.fromLocalFile(result))
        self.player.play()
        self.btn_play.setText("暂停")

    def export_mixed(self):
        if not self.project:
            return
        default = str(Path(tempfile.gettempdir()) / "混音.wav")
        path, _ = QFileDialog.getSaveFileName(self, "导出混音 WAV", default,
                                              "WAV (*.wav)")
        if not path:
            return
        self.btn_export.setEnabled(False)
        self.btn_export.setText("导出中…")
        self._worker = _RenderWorker(self.project, Path(path), self)
        self._worker.done.connect(self._export_done)
        self._worker.start()

    def _export_done(self, ok: bool, result: str):
        self.btn_export.setEnabled(True)
        self.btn_export.setText("导出混音")
        if ok:
            dialogs.info(self, "导出完成", f"已保存：\n{result}")
        else:
            dialogs.error(self, "导出失败", result[-1500:])

    # ---------- 编辑操作 ----------
    def split_at_playhead(self):
        t = self.canvas.playhead
        self._mutate(lambda: edit_ops.split_all(self.project, t), "切开")

    def duplicate_selection(self):
        ids = set(self.canvas.selection)
        if not ids:
            return

        def work():
            self.canvas.selection = edit_ops.duplicate(self.project, ids)

        self._mutate(work, "复制片段")

    def delete_selection(self):
        ids = set(self.canvas.selection)
        if not ids:
            return

        def work():
            edit_ops.delete_clips(self.project, ids)
            self.canvas.selection.clear()

        self._mutate(work, "删除片段")

    def ripple_delete(self):
        ids = set(self.canvas.selection)
        if not ids:
            return

        def work():
            edit_ops.ripple_delete(self.project, ids)
            self.canvas.selection.clear()

        self._mutate(work, "波纹删除")

    def add_track(self):
        def work():
            self.project.tracks.append(edit_ops.new_track(self.project))

        self._mutate(work, "新建轨道")

    def toggle_snap(self):
        self.canvas.snap_enabled = not self.canvas.snap_enabled
        self.btn_snap.setChecked(self.canvas.snap_enabled)

    def _toggle_track(self, index: int, kind: str):
        track = self.project.tracks[index]

        def work():
            setattr(track, kind, not getattr(track, kind))

        self._mutate(work, "静音" if kind == "mute" else "独奏")

    def _drop_segment(self, payload: dict, lane: int, at: float):
        if not self.project or not self.project.tracks:
            return
        source = next((c.source for t in self.project.tracks for c in t.clips),
                      None)
        if not source:
            return
        start = float(payload.get("start", 0.0))
        end = float(payload.get("end", start))
        if end - start < 0.02:
            return
        clip = Clip(source=source, src_in=start, src_out=end,
                    start=max(0.0, round(at * 20) / 20))
        if self.insert_mode:
            self._mutate(lambda: edit_ops.insert(self.project, lane, clip,
                                                 ripple_all=True), "插入片段")
        else:
            self._mutate(lambda: self.project.tracks[lane].clips.append(clip),
                         "叠加片段")

    # ---------- 片段菜单 ----------
    def _clip_menu(self, pos: QPoint, track_index: int, clip_id: str):
        track = self.project.tracks[track_index]
        clip = next((c for c in track.clips if c.id == clip_id), None)
        if clip is None:
            return
        items = [
            ("在播放头切分", self.split_at_playhead),
            (None, None),
            ("复制一份", self.duplicate_selection),
            ("删除此片段", self.delete_selection),
            ("波纹删除", self.ripple_delete),
            (None, None),
            ("淡入淡出 0.3 秒", lambda: self._fade(clip)),
            (None, None),
            ("增益 +3 dB", lambda: self._gain(clip, 3)),
            ("增益 -3 dB", lambda: self._gain(clip, -3)),
            (None, None),
            ("在此处叠加音频…", lambda: self._add_audio(track_index, overlay=True)),
            ("在此处插入音频…", lambda: self._add_audio(track_index, overlay=False)),
        ]
        GlassMenu(self).show_at(pos, items)

    def _fade(self, clip: Clip):
        def work():
            clip.fade_in = min(0.3, clip.dur / 2)
            clip.fade_out = min(0.3, clip.dur / 2)

        self._mutate(work, "淡入淡出")

    def _gain(self, clip: Clip, db: float):
        def work():
            clip.gain_db += db

        self._mutate(work, f"增益 {db:+.0f}dB")

    def _add_audio(self, track_index: int, overlay: bool):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择音频", "", "音频 (*.wav *.mp3 *.flac *.m4a *.aac *.ogg)")
        if not path:
            return
        import soundfile as sf
        try:
            info = sf.info(path)
            dur = float(info.frames) / float(info.samplerate)
        except Exception as exc:
            dialogs.error(self, "无法读取音频", str(exc))
            return

        at = self.canvas.playhead
        clip = Clip(source=path, src_in=0.0, src_out=dur, start=at)
        if overlay:
            def work():
                self.project.tracks.append(edit_ops.new_track(
                    self.project, name="叠加音"))
                self.project.tracks[-1].clips.append(clip)
            self._mutate(work, "叠加音频")
        else:
            self._mutate(lambda: edit_ops.insert(self.project, track_index,
                                                 clip, ripple_all=True),
                         "插入音频")
