"""自绘控件：标题栏、步骤栏、时间轴、播放器、滑块、表格。"""
from __future__ import annotations

import math
import time

import numpy as np

from PySide6.QtCore import (
    QEasingCurve, QElapsedTimer, QObject, QPoint, QPointF, QPropertyAnimation,
    QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QLinearGradient, QMouseEvent, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea, QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from . import theme

TRAFFIC_DOT = 16
TRAFFIC_HIT = 26
TRAFFIC_GAP = 4
TRAFFIC_RIGHT = 14
TRAFFIC_TOP = 13


def fmt_time(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def fmt_short(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def fmt_tenths(sec: float) -> str:
    sec = max(0.0, float(sec))
    m = int(sec // 60)
    return f"{m}:{sec - m * 60:04.1f}"


class Ticker(QObject):
    """全局唯一的界面动画时钟：16ms 推进所有注册回调。"""

    activeChanged = Signal(bool)
    _instance = None

    @classmethod
    def instance(cls) -> "Ticker":
        if cls._instance is None:
            cls._instance = Ticker()
        return cls._instance

    def __init__(self):
        super().__init__(QApplication.instance())
        self._subs: set = set()
        self._last: float | None = None
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)

    def running(self) -> bool:
        return self.timer.isActive()

    def kick(self, fn):
        """注册回调；回调返回 False 表示自己已经停下。"""
        if fn in self._subs:
            return
        self._subs.add(fn)
        if not self.timer.isActive():
            self._last = time.perf_counter()
            self.timer.start()
            self.activeChanged.emit(True)

    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self._last) if self._last else 0.016
        self._last = now
        alive = set()
        for fn in list(self._subs):
            try:
                if fn(dt):
                    alive.add(fn)
            except RuntimeError:
                pass          # 控件已销毁，直接丢弃
        self._subs = alive
        if not self._subs:
            self.timer.stop()
            self._last = None
            self.activeChanged.emit(False)


def make_card(margins=(0, 0, 0, 0), spacing=0, horizontal=False):
    frame = QFrame()
    frame.setObjectName("Card")
    lay = QHBoxLayout(frame) if horizontal else QVBoxLayout(frame)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return frame, lay


_SHADOW_CACHE = {}


def make_shadow(width: int, height: int, radius: int = 18, blur: int = 30,
                alpha: int = 46):
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import (
        QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene,
    )

    key = (width, height, radius, blur, alpha)
    cached = _SHADOW_CACHE.get(key)
    if cached is not None:
        return cached

    pad = blur * 2
    img = QImage(width + pad * 2, height + pad * 2,
                 QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(24, 32, 80, alpha))
    p.drawRoundedRect(QRectF(pad, pad, width, height), radius, radius)
    p.end()

    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(img))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(blur)
    item.setGraphicsEffect(effect)
    scene.addItem(item)

    out = QImage(img.size(), QImage.Format_ARGB32_Premultiplied)
    out.fill(0)
    p = QPainter(out)
    scene.render(p, QRectF(out.rect()), QRectF(img.rect()))
    p.end()

    pixmap = QPixmap.fromImage(out)
    _SHADOW_CACHE[key] = (pixmap, pad)
    return pixmap, pad


def add_shadow(widget: QWidget, blur: int = 30, dy: int = 8, alpha: int = 46):
    from PySide6.QtWidgets import QGraphicsDropShadowEffect
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(24, 32, 80, alpha))
    widget.setGraphicsEffect(effect)


def animate_pop(widget: QWidget, duration: int = 380):
    """对话框弹出：从 92% 大小带弹簧放大，同时淡入。"""
    target = widget.geometry()
    w, h = target.width(), target.height()
    start = QRect(int(target.center().x() - w * 0.46),
                  int(target.center().y() - h * 0.46),
                  int(w * 0.92), int(h * 0.92))
    widget.setWindowOpacity(0.0)
    widget.setGeometry(start)
    grow = QPropertyAnimation(widget, b"geometry", widget)
    grow.setDuration(duration)
    grow.setStartValue(start)
    grow.setEndValue(target)
    grow.setEasingCurve(QEasingCurve.OutCubic)
    grow.start()
    fade = QPropertyAnimation(widget, b"windowOpacity", widget)
    fade.setDuration(180)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.start()
    widget._pop_anims = (grow, fade)


# ---------------------------------------------------------------- 弹性按钮
class SpringButton(QPushButton):
    """用 QPainter 自绘的玻璃按钮，悬停放大、按下回弹（对齐设计稿的弹簧曲线）。"""

    def __init__(self, text: str = "", kind: str = "glass", parent=None):
        super().__init__(text, parent)
        self.kind = kind
        self._scale = 1.0
        self._hover = False
        self._checked = False
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_scale)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(36)
        font = self.font()
        font.setPixelSize(13)
        font.setWeight(QFont.Medium if kind != "primary" else QFont.DemiBold)
        self.setFont(font)

    def setChecked(self, checked: bool):
        if self._checked != checked:
            self._checked = checked
            self.update()

    def isChecked(self) -> bool:
        return self._checked

    def _on_scale(self, value):
        self._scale = float(value)
        self.update()

    def _animate(self, target: float, duration: int):
        self._anim.stop()
        self._anim.setDuration(duration)
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._hover = True
        self._animate(1.04, 260)
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._animate(1.0, 240)
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._animate(0.94, 90)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._animate(1.04 if self._hover else 1.0, 240)
        super().mouseReleaseEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.translate(w / 2, h / 2)
        p.scale(self._scale, self._scale)
        rect = QRectF(-w / 2 + 1, -h / 2 + 1, w - 2, h - 2)
        radius = min(rect.height() / 2, 18)
        disabled = not self.isEnabled()

        if self.kind == "primary":
            grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
            if disabled:
                grad.setColorAt(0, QColor(160, 160, 168, 130))
                grad.setColorAt(1, QColor(140, 140, 148, 130))
            elif self.isDown():
                grad.setColorAt(0, QColor("#1679EE"))
                grad.setColorAt(1, theme.c("accent_press"))
            elif self._hover:
                grad.setColorAt(0, QColor("#3D97FF"))
                grad.setColorAt(1, QColor(theme.ACCENT_HOVER))
            else:
                grad.setColorAt(0, QColor("#2A8CFF"))
                grad.setColorAt(1, theme.c("accent"))
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            p.setBrush(grad)
            p.drawRoundedRect(rect, radius, radius)
            text_color = QColor(255, 255, 255, 150 if disabled else 255)
        elif self.kind == "ghost":
            if self._hover and not disabled:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 122, 255, 26))
                p.drawRoundedRect(rect, 10, 10)
            text_color = QColor(theme.ACCENT if not disabled else "rgba(60,60,67,90)")
        elif self.kind == "tool":
            if self._hover and not disabled:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 180))
                p.drawRoundedRect(rect, 15, 15)
            text_color = theme.c("text") if not disabled else theme.c("text3")
        elif self.kind == "bar":
            if self._checked:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(10, 132, 255, 60) if theme.dark
                           else QColor(0, 122, 255, 34))
                p.drawRoundedRect(rect, 8, 8)
            elif self._hover and not disabled:
                p.setPen(Qt.NoPen)
                p.setBrush(theme.c("hover"))
                p.drawRoundedRect(rect, 8, 8)
            if self._checked:
                text_color = QColor(theme.ACCENT)
            else:
                text_color = theme.c("text") if not disabled else theme.c("text3")
        else:  # glass
            grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
            if theme.dark:
                top = 64 if self._hover else 44
                bottom = 44 if self._hover else 28
                if disabled:
                    top, bottom = 24, 16
                grad.setColorAt(0, QColor(255, 255, 255, top))
                grad.setColorAt(1, QColor(255, 255, 255, bottom))
                p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            else:
                top = 235 if self._hover else 190
                bottom = 170 if self._hover else 130
                if disabled:
                    top, bottom = 120, 90
                grad.setColorAt(0, QColor(255, 255, 255, top))
                grad.setColorAt(1, QColor(255, 255, 255, bottom))
                p.setPen(QPen(QColor(255, 255, 255, 225), 1))
            p.setBrush(grad)
            p.drawRoundedRect(rect, radius, radius)
            text_color = theme.c("text") if not disabled else theme.c("text3")

        if self.text():
            p.setPen(text_color)
            p.setFont(self.font())
            p.drawText(QRectF(rect), Qt.AlignCenter, self.text())


# ---------------------------------------------------------------- 标题栏
class TitleBar(QWidget):
    """标题栏：左（设置/品牌/状态）、中（检查·编辑切换）、右（文件/主按钮/窗口按钮）。"""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._win = window
        self.setFixedHeight(52)
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 0, TRAFFIC_RIGHT - 6, 0)
        grid.setHorizontalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)

        self.left_box = QWidget(self)
        self.left_lay = QHBoxLayout(self.left_box)
        self.left_lay.setContentsMargins(0, 0, 0, 0)
        self.left_lay.setSpacing(9)
        grid.addWidget(self.left_box, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)

        self.center_box = QWidget(self)
        self.center_lay = QHBoxLayout(self.center_box)
        self.center_lay.setContentsMargins(0, 0, 0, 0)
        self.center_lay.setSpacing(0)

        self.right_box = QWidget(self)
        self.right_lay = QHBoxLayout(self.right_box)
        self.right_lay.setContentsMargins(0, 0, 0, 0)
        self.right_lay.setSpacing(8)
        grid.addWidget(self.right_box, 0, 2, Qt.AlignRight | Qt.AlignVCenter)

        self.btn_min = _LightButton(self, QColor("#FEBC2E"), "min",
                                    window.showMinimized, "最小化")
        self.btn_max = _LightButton(self, QColor("#28C840"), "max",
                                    lambda: window.showNormal()
                                    if window.isMaximized() else window.showMaximized(),
                                    "最大化")
        self.btn_close = _LightButton(self, QColor("#FF5F57"), "close",
                                      window.close, "关闭")

    def add_window_buttons(self):
        self.right_lay.addSpacing(6)
        for btn in (self.btn_min, self.btn_max, self.btn_close):
            self.right_lay.addWidget(btn)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._center_switch()

    def showEvent(self, event):
        super().showEvent(event)
        self._center_switch()

    def _center_switch(self):
        self.center_box.adjustSize()
        w, h = self.center_box.width(), self.center_box.height()
        self.center_box.move((self.width() - w) // 2, (self.height() - h) // 2)
        self.center_box.raise_()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(QPen(theme.c("border"), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent):
        if e.buttons() & Qt.LeftButton and hasattr(self, "_drag") and not self._win.isMaximized():
            self._win.move(e.globalPosition().toPoint() - self._drag)
            e.accept()

    def mouseDoubleClickEvent(self, e: QMouseEvent):
        if self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()


class _LightButton(QPushButton):
    def __init__(self, parent, color: QColor, glyph: str, handler, tooltip: str):
        super().__init__(parent)
        self._color = color
        self._glyph = glyph
        self._hover = False
        self._scale = 1.0
        self.setFixedSize(TRAFFIC_HIT, TRAFFIC_HIT)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(handler)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_scale)

    def _on_scale(self, value):
        self._scale = float(value)
        self.update()

    def _animate(self, target):
        self._anim.stop()
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._hover = True
        self._animate(1.06)
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._animate(1.0)
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(self._scale, self._scale)

        r = TRAFFIC_DOT / 2
        p.setPen(QPen(self._color.darker(115), 1))
        p.setBrush(self._color)
        p.drawEllipse(QPointF(0, 0), r, r)

        if self._hover:
            g = r * 0.42
            pen = QPen(QColor(0, 0, 0, 150), 2.2)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            if self._glyph == "close":
                p.drawLine(QPointF(-g, -g), QPointF(g, g))
                p.drawLine(QPointF(-g, g), QPointF(g, -g))
            elif self._glyph == "min":
                p.drawLine(QPointF(-g, 0), QPointF(g, 0))
            else:
                p.setBrush(Qt.NoBrush)
                p.drawRect(QRectF(-g, -g, 2 * g, 2 * g))


# ---------------------------------------------------------------- 标题栏控件
class AppLogo(QWidget):
    def __init__(self, size: int = 24, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pix = None
        try:
            from app.resources import icon_png

            path = icon_png()
            if path.exists():
                pix = QPixmap(str(path))
                if not pix.isNull():
                    self._pix = pix
        except Exception:
            self._pix = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 0, self.width(), self.height())
        if self._pix is not None:
            clip = QPainterPath()
            clip.addRoundedRect(r, 7, 7)
            p.save()
            p.setClipPath(clip)
            scaled = self._pix.scaled(self.width(), self.height(),
                                      Qt.KeepAspectRatioByExpanding,
                                      Qt.SmoothTransformation)
            p.drawPixmap(QPointF(-(scaled.width() - self.width()) / 2,
                                 -(scaled.height() - self.height()) / 2), scaled)
            p.restore()
            return

        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, QColor("#2E8BFF"))
        grad.setColorAt(1, QColor("#6A5CFF"))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(r, 7, 7)
        pen = QPen(QColor(255, 255, 255, 235), 1.8)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        cx, cy = self.width() / 2, self.height() / 2
        heights = (3.0, 7.0, 11.0, 7.0, 3.0)
        step = self.width() / 8
        for i, h in enumerate(heights):
            x = self.width() / 2 + (i - 2) * step
            p.drawLine(QPointF(x, cy - h / 2), QPointF(x, cy + h / 2))


class SwapButton(QPushButton):
    """换文件：圆形按钮 + 双向箭头。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor(120, 120, 128, 46 if self._hover else 28)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawEllipse(QRectF(0, 0, self.width(), self.height()))
        pen = QPen(theme.c("text"), 1.7)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        w, h = self.width(), self.height()
        p.drawLine(QPointF(7, 13), QPointF(23, 13))
        p.drawLine(QPointF(20, 10), QPointF(23, 13))
        p.drawLine(QPointF(20, 16), QPointF(23, 13))
        p.drawLine(QPointF(25, 19), QPointF(9, 19))
        p.drawLine(QPointF(12, 16), QPointF(9, 19))
        p.drawLine(QPointF(12, 22), QPointF(9, 19))


class ViewSwitch(QWidget):
    """分段控件：检查 / 编辑。"""

    changed = Signal(str)

    def __init__(self, items=("检查", "编辑"), parent=None):
        super().__init__(parent)
        self._items = list(items)
        self._enabled = [True] * len(self._items)
        self._index = 0
        self._pos = 0.0
        self.setFixedSize(len(self._items) * 66 + 4, 32)
        self.setCursor(Qt.PointingHandCursor)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_pos)
        f = QFont(self.font())
        f.setPixelSize(13)
        self._font = f
        self._font_on = QFont(f)
        self._font_on.setBold(True)

    def _seg_w(self) -> float:
        return (self.width() - 4) / len(self._items)

    def index(self) -> int:
        return self._index

    def set_index(self, index: int, animate: bool = True):
        if not (0 <= index < len(self._items)):
            return
        self._index = index
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(float(index))
            self._anim.start()
        else:
            self._pos = float(index)
            self.update()

    def set_item_enabled(self, index: int, enabled: bool):
        self._enabled[index] = enabled
        if not enabled and self._index == index:
            self.set_index(0, animate=False)
        self.update()

    def _on_pos(self, value):
        self._pos = float(value)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(120, 120, 128, 46 if theme.dark else 28))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 9, 9)

        seg = self._seg_w()
        thumb = QRectF(2 + self._pos * seg, 2, seg, self.height() - 4)
        p.setBrush(QColor(0, 0, 0, 18))
        p.drawRoundedRect(thumb.translated(0, 1), 7, 7)
        p.setBrush(QColor(255, 255, 255) if not theme.dark
                   else QColor(255, 255, 255, 40))
        p.drawRoundedRect(thumb, 7, 7)

        for i, label in enumerate(self._items):
            active = i == self._index
            p.setFont(self._font_on if active else self._font)
            if not self._enabled[i]:
                color = QColor(theme.c("text3"))
                color.setAlpha(110)
            elif active:
                color = theme.c("text")
            else:
                color = theme.c("text2")
            p.setPen(color)
            p.drawText(QRectF(2 + i * seg, 0, seg, self.height()),
                       Qt.AlignCenter, label)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        i = int((e.position().x() - 2) // self._seg_w())
        i = max(0, min(len(self._items) - 1, i))
        if i != self._index and self._enabled[i]:
            self.set_index(i)
            self.changed.emit(self._items[i])


class FileChip(QWidget):
    """标题栏文件信息胶囊：圆点 + 文件名 + 时长。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._name = ""
        self._dur = ""
        self.setFixedHeight(28)
        self.setVisible(False)
        f = QFont(self.font())
        f.setPixelSize(12)
        self._font_name = QFont(f)
        self._font_name.setWeight(QFont.DemiBold)
        d = QFont(self.font())
        d.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
        d.setPixelSize(11)
        self._font_dur = d

    def set_info(self, name: str, duration: str = "", tooltip: str = ""):
        self._name = name
        self._dur = duration
        self.setToolTip(tooltip or (f"{name} · {duration}" if duration else name))
        self.setVisible(bool(name))
        self.updateGeometry()
        self.update()

    def clear(self):
        self.set_info("")

    def sizeHint(self) -> QSize:
        if not self._name:
            return QSize(0, 28)
        fm = QFontMetrics(self._font_name)
        fmd = QFontMetrics(self._font_dur)
        width = 24 + min(fm.horizontalAdvance(self._name), 190) + 12
        if self._dur:
            width += fmd.horizontalAdvance(self._dur) + 8
        return QSize(min(width, 300), 28)

    def paintEvent(self, event):
        if not self._name:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(120, 120, 128, 40 if theme.dark else 28))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 14, 14)

        p.setBrush(theme.c("accent"))
        p.drawEllipse(QRectF(11, self.height() / 2 - 3.5, 7, 7))

        fmd = QFontMetrics(self._font_dur)
        dur_w = fmd.horizontalAdvance(self._dur) if self._dur else 0
        fm = QFontMetrics(self._font_name)
        name_w = self.width() - 24 - 10 - (dur_w + 8 if dur_w else 0)
        p.setFont(self._font_name)
        p.setPen(theme.c("text"))
        p.drawText(QRectF(24, 0, max(10, name_w), self.height()),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(self._name, Qt.ElideRight, int(max(10, name_w))))
        if dur_w:
            p.setFont(self._font_dur)
            p.setPen(theme.c("text2"))
            p.drawText(QRectF(self.width() - dur_w - 10, 0, dur_w, self.height()),
                       Qt.AlignRight | Qt.AlignVCenter, self._dur)


# ---------------------------------------------------------------- 步骤栏
class ImportButton(QPushButton):
    """侧栏顶部渐变导入按钮。"""

    def __init__(self, text: str = "导入视频或音频", parent=None):
        super().__init__(text, parent)
        self.setFixedHeight(38)
        self.setCursor(Qt.PointingHandCursor)
        self._hover_t = 0.0
        self._scale = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_scale)
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(160)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover)
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)

    def _on_scale(self, value):
        self._scale = float(value)
        self.update()

    def _on_hover(self, value):
        self._hover_t = float(value)
        self.update()

    def _animate(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(target)
        self._anim.start()

    def _fade_hover(self, target: float):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_t)
        self._hover_anim.setEndValue(target)
        self._hover_anim.start()

    def enterEvent(self, e):
        self._fade_hover(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._fade_hover(0.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._animate(0.97)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._animate(1.0)
        super().mouseReleaseEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(self._scale, self._scale)
        rect = QRectF(-self.width() / 2, -self.height() / 2,
                      self.width(), self.height())
        boost = 100 + int(10 * self._hover_t)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0, QColor("#1F86FF").lighter(boost))
        grad.setColorAt(1, QColor("#5E5CE6").lighter(boost))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(rect, 11, 11)

        fm = QFontMetrics(self.font())
        text_w = fm.horizontalAdvance(self.text())
        icon_w, gap = 15.0, 7.0
        total = icon_w + gap + text_w
        x0 = -total / 2

        pen = QPen(QColor(255, 255, 255, 240), 1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        ix = x0 + icon_w / 2
        p.drawLine(QPointF(ix, -1.5), QPointF(ix, -7.5))
        p.drawLine(QPointF(ix - 3.4, -4.4), QPointF(ix, -7.8))
        p.drawLine(QPointF(ix + 3.4, -4.4), QPointF(ix, -7.8))
        p.drawLine(QPointF(ix - 6.0, 0.5), QPointF(ix - 6.0, 6.5))
        p.drawLine(QPointF(ix - 6.0, 6.5), QPointF(ix + 6.0, 6.5))
        p.drawLine(QPointF(ix + 6.0, 6.5), QPointF(ix + 6.0, 0.5))

        p.setPen(QColor("#FFFFFF"))
        p.setFont(self.font())
        p.drawText(QRectF(x0 + icon_w + gap, -self.height() / 2,
                          text_w + 2, self.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text())


class StepRail(QWidget):
    """侧栏处理步骤：圆形序号 + 名称 + 耗时，当前步骤高亮。"""

    LABELS = ["导入", "清理", "分析", "检查", "导出"]
    ROW_H = 32
    GAP = 6
    PAD = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current = 0
        self.completed: set[int] = set()
        self.times: list[float | None] = [None] * len(self.LABELS)
        self.setFixedHeight(len(self.LABELS) * (self.ROW_H + self.GAP) + self.PAD * 2)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_state(self, current: int, completed: set[int]):
        if current == self.current and completed == self.completed:
            return
        self.current = current
        self.completed = set(completed)
        self.update()

    def set_times(self, times):
        times = list(times)[:len(self.LABELS)]
        times += [None] * (len(self.LABELS) - len(times))
        if times == self.times:
            return
        self.times = times
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        step = self.ROW_H + self.GAP
        r = 11.5
        cx = 6 + r
        f_idx = QFont(self.font())
        f_idx.setPixelSize(11)
        f_idx.setBold(True)
        f_label = QFont(self.font())
        f_label.setPixelSize(12.5)
        f_mono = QFont(self.font())
        f_mono.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
        f_mono.setPixelSize(10.5)

        for i, label in enumerate(self.LABELS):
            top = self.PAD + i * step

            if i < len(self.LABELS) - 1:
                linked = i in self.completed and (i + 1) in self.completed
                p.setPen(QPen(QColor("#34C759") if linked
                              else QColor(120, 120, 128, 70), 1.5))
                p.drawLine(QPointF(cx, top + r * 2 + 4),
                           QPointF(cx, top + step - 3))

            if i == self.current:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(10, 132, 255, 54) if theme.dark
                           else QColor(0, 122, 255, 30))
                p.drawRoundedRect(QRectF(0, top, self.width(), self.ROW_H), 9, 9)

            circle = QRectF(cx - r, top + self.ROW_H / 2 - r, r * 2, r * 2)
            if i in self.completed:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#34C759"))
                p.drawEllipse(circle)
                pen = QPen(QColor("#FFFFFF"), 1.7)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                cy = circle.center().y()
                p.drawLine(QPointF(cx - 4.5, cy + 0.5), QPointF(cx - 1.5, cy + 3.5))
                p.drawLine(QPointF(cx - 1.5, cy + 3.5), QPointF(cx + 5, cy - 3.5))
                label_color = theme.c("text")
            elif i == self.current:
                p.setPen(Qt.NoPen)
                p.setBrush(theme.c("accent"))
                p.drawEllipse(circle)
                p.setPen(QColor("#FFFFFF"))
                p.setFont(f_idx)
                p.drawText(circle, Qt.AlignCenter, str(i + 1))
                label_color = theme.c("accent")
            else:
                p.setPen(QPen(QColor(120, 120, 128, 90), 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(circle)
                p.setPen(theme.c("text3"))
                p.setFont(f_idx)
                p.drawText(circle, Qt.AlignCenter, str(i + 1))
                label_color = theme.c("text2")

            p.setFont(f_label)
            p.setPen(label_color)
            p.drawText(QRectF(cx + r + 10, top, self.width() - 96, self.ROW_H),
                       Qt.AlignLeft | Qt.AlignVCenter, label)

            t = self.times[i]
            if t is not None and (i in self.completed or i == self.current):
                p.setFont(f_mono)
                p.setPen(theme.c("accent") if i == self.current
                         else theme.c("text3"))
                p.drawText(QRectF(self.width() - 66, top, 62, self.ROW_H),
                           Qt.AlignRight | Qt.AlignVCenter, f"{t:.1f}s")


# ---------------------------------------------------------------- 时间轴
class TimelineWidget(QWidget):
    seekRequested = Signal(float)
    viewChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(118)
        self.setCursor(Qt.PointingHandCursor)
        self._duration = 1.0
        self._segments: list[dict] = []
        self._colors: dict[str, str] = {}
        self._position = 0.0
        self._peaks = None
        self._view_start = 0.0
        self._view_end = 1.0
        self._row = -1

    def set_waveform(self, peaks):
        self._peaks = peaks if peaks is not None and len(peaks) else None
        self.update()

    def set_data(self, segments, duration, colors):
        self._segments = segments
        self._duration = max(1e-6, duration)
        self._colors = colors
        self._view_start, self._view_end = 0.0, self._duration
        self.update()
        self.viewChanged.emit()

    def set_row(self, row_id: int):
        self._row = row_id
        self.update()

    def is_zoomed(self) -> bool:
        return self._view_end - self._view_start < self._duration - 1e-6

    def set_view(self, start: float, end: float):
        start = max(0.0, min(start, self._duration))
        end = min(self._duration, max(end, start + 0.5))
        if end - start >= self._duration:
            start, end = 0.0, self._duration
        self._view_start, self._view_end = start, end
        self.update()
        self.viewChanged.emit()

    def reset_view(self):
        self._view_start, self._view_end = 0.0, self._duration
        self.update()
        self.viewChanged.emit()

    def set_position(self, seconds: float):
        if abs(seconds - self._position) < 0.02:
            return
        self._position = seconds
        self.update()

    def _x(self, t: float) -> float:
        span = max(1e-6, self._view_end - self._view_start)
        return 12 + (t - self._view_start) / span * (self.width() - 24)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 14, 14)
        p.fillPath(path, theme.c("timeline_bg"))
        p.setPen(QPen(QColor(255, 255, 255, 170), 1))
        p.drawPath(path)

        if self._peaks is not None:
            self._draw_waveform(p, rect)
        self._draw_ticks(p, rect)

        for seg in self._segments:
            if seg.get("excluded"):
                continue
            x1, x2 = self._x(seg["start"]), self._x(seg["end"])
            if x2 < -6 or x1 > self.width() + 6:
                continue
            left = max(-6.0, x1)
            w = max(3.0, min(self.width() + 6.0, x2) - left)
            color = QColor(self._colors.get(seg.get("speaker", ""), theme.ACCENT))
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(left, 10, w, 18), 9, 9)
            gloss = QLinearGradient(0, 10, 0, 28)
            gloss.setColorAt(0, QColor(255, 255, 255, 140))
            gloss.setColorAt(0.5, QColor(255, 255, 255, 20))
            gloss.setColorAt(1, QColor(255, 255, 255, 0))
            p.setBrush(gloss)
            p.drawRoundedRect(QRectF(left, 10, w, 18), 9, 9)

        if self._view_start <= self._position <= self._view_end:
            x = self._x(self._position)
            p.setPen(QPen(theme.c("accent"), 1.5))
            p.drawLine(QPointF(x, 8), QPointF(x, rect.height() - 8))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(QPointF(x, 8), 6, 6)
            p.setBrush(theme.c("accent"))
            p.drawEllipse(QPointF(x, 8), 3.6, 3.6)

    def _draw_waveform(self, p, rect):
        count = len(self._peaks)
        i0 = max(0, int(self._view_start / self._duration * count))
        i1 = min(count, int(self._view_end / self._duration * count) + 1)
        sub = self._peaks[i0:i1]
        width = max(1, int(rect.width() - 24))
        if len(sub) > width:
            k = len(sub) // width
            if k > 1:
                sub = sub[: k * width].reshape(width, k).max(axis=1)
        if len(sub) == 0:
            return

        mid, half = 63.0, 26.0
        step = (rect.width() - 24) / max(1, len(sub))
        p.setPen(Qt.NoPen)
        for i, value in enumerate(sub):
            x = 12 + i * step
            h = max(1.0, float(value) * half)
            t = self._view_start + i * step / max(1e-6, rect.width() - 24) * (
                self._view_end - self._view_start)
            played = t < self._position
            p.setBrush(theme.c("accent") if played else theme.c("wave"))
            p.drawRoundedRect(QRectF(x, mid - h, max(1.4, step * 0.62), h * 2), 0.8, 0.8)

    def _draw_ticks(self, p, rect):
        span = self._view_end - self._view_start
        for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
            if span / s <= 8:
                step = s
                break
        else:
            step = 600
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        p.setFont(font)
        t = int(self._view_start / step) * step
        while t <= self._view_end:
            if t >= self._view_start:
                x = self._x(t)
                if 16 < x < rect.width() - 16:
                    p.setPen(QPen(theme.c("border"), 1))
                    p.drawLine(QPointF(x, rect.height() - 22), QPointF(x, rect.height() - 18))
                    p.setPen(theme.c("text3"))
                    p.drawText(QRectF(x - 26, rect.height() - 16, 52, 14),
                               Qt.AlignHCenter, fmt_time(t))
            t += step

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.LeftButton and self.width() > 24:
            ratio = max(0.0, min(1.0, (e.position().x() - 12) / (self.width() - 24)))
            span = self._view_end - self._view_start
            self.seekRequested.emit(self._view_start + ratio * span)

    def mouseDoubleClickEvent(self, e: QMouseEvent):
        self.reset_view()


# ---------------------------------------------------------------- 播放按钮（弹跳果冻）
class PlayButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(48, 48)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self._playing = False
        self._scale = 1.0
        self._sx = 1.0
        self._sy = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_scale)
        self._jelly = QVariantAnimation(self)
        self._jelly.setDuration(650)
        for t, v in ((0.0, 1.0), (0.22, 1.24), (0.38, 0.86), (0.52, 1.10),
                     (0.66, 0.96), (0.82, 1.02), (1.0, 1.0)):
            self._jelly.setKeyValueAt(t, v)
        self._jelly.valueChanged.connect(self._on_jelly)

    def _on_scale(self, v):
        self._scale = float(v)
        self.update()

    def _on_jelly(self, v):
        self._sx = float(v)
        self._sy = 2 - float(v) * 0.72 if float(v) > 1 else 0.86 + float(v) * 0.14
        self.update()

    def _animate(self, target, duration=320):
        self._anim.stop()
        self._anim.setDuration(duration)
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._animate(1.12, 260)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate(1.0, 240)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._animate(0.84, 85)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._animate(1.12, 240)
        self._jelly.stop()
        self._jelly.start()
        super().mouseReleaseEvent(e)

    def set_playing(self, playing: bool):
        if self._playing != playing:
            self._playing = playing
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(self._scale * self._sx, self._scale * self._sy)
        r = 19.0

        grad = QRadialGradient(-r * 0.3, -r * 0.4, r * 1.9)
        grad.setColorAt(0, QColor(255, 255, 255, 250))
        grad.setColorAt(0.5, QColor(255, 255, 255, 168))
        grad.setColorAt(1, QColor(255, 255, 255, 108))
        p.setPen(QPen(QColor(255, 255, 255, 210), 1))
        p.setBrush(grad)
        p.drawEllipse(QPointF(0, 0), r, r)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1D1D1F"))
        if self._playing:
            p.drawRoundedRect(QRectF(-5.4, -6, 3.4, 12), 1.1, 1.1)
            p.drawRoundedRect(QRectF(2.0, -6, 3.4, 12), 1.1, 1.1)
        else:
            path = QPainterPath()
            path.moveTo(-3.6, -6.4)
            path.lineTo(6.4, 0)
            path.lineTo(-3.6, 6.4)
            path.closeSubpath()
            p.drawPath(path)


class SkipButton(QPushButton):
    def __init__(self, direction: int, parent=None):
        super().__init__(parent)
        self.direction = direction
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self._scale = 1.0
        self._hover = False
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutBack)
        self._anim.valueChanged.connect(self._on_scale)

    def _on_scale(self, v):
        self._scale = float(v)
        self.update()

    def _animate(self, target, duration):
        self._anim.stop()
        self._anim.setDuration(duration)
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._hover = True
        self._animate(1.1, 240)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._animate(1.0, 240)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._animate(0.85, 85)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._animate(1.1 if self._hover else 1.0, 240)
        super().mouseReleaseEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(self._scale, self._scale)
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.c("hover"))
            p.drawEllipse(QPointF(0, 0), 15, 15)
        color = QColor(29, 29, 31, 210)
        p.setPen(QPen(color, 1.7))
        p.setBrush(Qt.NoBrush)
        rect = QRectF(-8, -8, 16, 16)
        if self.direction > 0:
            p.drawArc(rect, 60 * 16, 270 * 16)
        else:
            p.drawArc(rect, 210 * 16, 270 * 16)
        p.setFont(QFont(self.font().family(), 7, QFont.Bold))
        p.drawText(QRectF(-8, -7, 16, 14), Qt.AlignCenter, "5")


class VolumeButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self._muted = False
        self._hover = False
        self._scale = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutBack)
        self._anim.valueChanged.connect(self._on_scale)

    def _on_scale(self, v):
        self._scale = float(v)
        self.update()

    def _animate(self, target, duration):
        self._anim.stop()
        self._anim.setDuration(duration)
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._hover = True
        self._animate(1.12, 260)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._animate(1.0, 240)
        super().leaveEvent(e)

    def set_muted(self, muted: bool):
        if self._muted != muted:
            self._muted = muted
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(self._scale, self._scale)
        color = theme.c("text")
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        path = QPainterPath()
        path.moveTo(-8, -3)
        path.lineTo(-4, -3)
        path.lineTo(0.5, -7)
        path.lineTo(0.5, 7)
        path.lineTo(-4, 3)
        path.lineTo(-8, 3)
        path.closeSubpath()
        p.drawPath(path)

        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if self._muted:
            p.drawLine(QPointF(3.5, -3.5), QPointF(8.5, 3.5))
            p.drawLine(QPointF(8.5, -3.5), QPointF(3.5, 3.5))
        else:
            p.drawArc(QRectF(1.5, -4.5, 7, 9), -60 * 16, 120 * 16)
            p.drawArc(QRectF(-0.5, -7, 12, 14), -60 * 16, 120 * 16)


# ---------------------------------------------------------------- 苹果风滑块
class MorphSlider(QWidget):
    """轨道悬停变粗、拖动时把手变成玻璃胶囊（对齐设计稿）。"""
    valueChanged = Signal(int)
    sliderMoved = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min, self._max = 0, 1000
        self._value = 0
        self._hover = False
        self._dragging = False
        self._track_h = 5.0
        self._knob_w = 22.0
        self._knob_h = 14.0
        self._phase = 0.0
        self.setMinimumHeight(26)
        self.setCursor(Qt.PointingHandCursor)
        self.setEnabled(True)
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_phase)

    def _on_phase(self, v):
        ph = float(v)
        if self._dragging:
            self._track_h = 5 + 3 * ph
            self._knob_w = 22 + 14 * ph
            self._knob_h = 14 + 10 * ph
        else:
            self._track_h = 5 + 3 * ph
            self._knob_w = 22 + (22 * 0.1) * ph
            self._knob_h = 14 + (14 * 0.1) * ph
        self.update()

    def _animate(self, target, duration=260):
        self._anim.stop()
        self._anim.setDuration(duration)
        self._anim.setStartValue(self._phase)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._hover = True
        self._animate(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        if not self._dragging:
            self._animate(0.0)
        super().leaveEvent(e)

    def setRange(self, lo: int, hi: int):
        self._min, self._max = lo, hi

    def setValue(self, value: int):
        value = max(self._min, min(self._max, int(value)))
        if value == self._value:
            return
        self._value = value
        self.update()
        self.valueChanged.emit(value)

    def value(self) -> int:
        return self._value

    def isSliderDown(self) -> bool:
        return self._dragging

    def _ratio(self) -> float:
        return (self._value - self._min) / max(1, self._max - self._min)

    def _x_for(self, ratio: float) -> float:
        pad = 11 + (self._knob_w - 22) / 2
        return pad + (self.width() - 2 * pad) * ratio

    def _set_from_x(self, x: float):
        pad = 11 + (self._knob_w - 22) / 2
        ratio = (x - pad) / max(1, self.width() - 2 * pad)
        ratio = max(0.0, min(1.0, ratio))
        self.setValue(int(self._min + ratio * (self._max - self._min)))

    def mousePressEvent(self, e: QMouseEvent):
        if not self.isEnabled():
            return
        self._dragging = True
        self._animate(1.0, 300)
        self._set_from_x(e.position().x())
        self.sliderMoved.emit(self._value)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._dragging:
            self._set_from_x(e.position().x())
            self.sliderMoved.emit(self._value)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._dragging:
            self._dragging = False
            self._animate(1.0 if self._hover else 0.0, 420)
            self.valueChanged.emit(self._value)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        enabled = self.isEnabled()
        p.setOpacity(1.0 if enabled else 0.4)
        cy = self.height() / 2
        pad = 11 + (self._knob_w - 22) / 2
        track = QRectF(pad, cy - self._track_h / 2, self.width() - 2 * pad, self._track_h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(120, 120, 128, 46))
        p.drawRoundedRect(track, self._track_h / 2, self._track_h / 2)

        ratio = self._ratio()
        filled = QRectF(track.left(), track.top(),
                        max(0.0, track.width() * ratio), track.height())
        grad = QLinearGradient(track.left(), 0, track.right(), 0)
        grad.setColorAt(0, QColor("#4FA8FF"))
        grad.setColorAt(1, theme.c("accent"))
        p.setBrush(grad)
        p.drawRoundedRect(filled, self._track_h / 2, self._track_h / 2)

        x = pad + (self.width() - 2 * pad) * ratio
        knob = QRectF(x - self._knob_w / 2, cy - self._knob_h / 2,
                      self._knob_w, self._knob_h)
        if self._dragging:
            p.setBrush(QColor(255, 255, 255, 60))
            p.setPen(QPen(QColor(255, 255, 255, 220), 1))
        else:
            p.setBrush(QColor(255, 255, 255))
            p.setPen(QPen(QColor(0, 0, 0, 18), 0.8))
        p.drawRoundedRect(knob, self._knob_h / 2, self._knob_h / 2)


# ---------------------------------------------------------------- 进度条（带流光）
class ShineProgress(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self._value = 0
        self._phase = 0.0
        self._active = False

    def setValue(self, v: int):
        self._value = max(0, min(1000, int(v)))
        self.update()

    def set_active(self, active: bool):
        self._active = active
        if active:
            Ticker.instance().kick(self._anim_tick)
        else:
            self._phase = 0.0
            self.update()

    def _anim_tick(self, dt: float) -> bool:
        if not self._active:
            return False
        self._phase = (self._phase + dt / 1.4) % 1.4
        self.update()
        return True

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(120, 120, 128, 41))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), r, r)

        w = self.width() * self._value / 1000
        if w <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, self.height()), r, r)
        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0, QColor("#5AB0FF"))
        grad.setColorAt(1, theme.c("accent"))
        p.fillPath(path, grad)

        if self._active:
            p.setClipPath(path)
            shine_w = max(40.0, w * 0.35)
            x = -shine_w + self._phase * (w + shine_w)
            sg = QLinearGradient(x, 0, x + shine_w, 0)
            sg.setColorAt(0, QColor(255, 255, 255, 0))
            sg.setColorAt(0.5, theme.c("hover"))
            sg.setColorAt(1, QColor(255, 255, 255, 0))
            p.setBrush(sg)
            p.drawRect(QRectF(x, 0, shine_w, self.height()))
            p.setClipping(False)


# ---------------------------------------------------------------- 人物行
class PersonRow(QWidget):
    """人物行：悬停放大、按下回弹。"""
    clicked = Signal(str, bool)

    def __init__(self, name: str, color: str, count: int, seconds: float = 0.0,
                 parent=None):
        super().__init__(parent)
        self.name = name
        self.color = color
        self.count = count
        self.seconds = seconds
        self._hover = False
        self._selected = False
        self._scale = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_scale)
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)

    def set_selected(self, selected: bool):
        if self._selected != selected:
            self._selected = selected
            self.update()

    def _on_scale(self, v):
        self._scale = float(v)
        self.update()

    def _animate(self, target, duration):
        self._anim.stop()
        self._anim.setDuration(duration)
        self._anim.setStartValue(self._scale)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._hover = True
        self._animate(1.02, 240)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._animate(1.0, 220)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._animate(0.96, 85)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._animate(1.02 if self._hover else 1.0, 220)
        if self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.name, bool(e.modifiers() & Qt.ControlModifier))
        super().mouseReleaseEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(self._scale, self._scale)
        rect = QRectF(-self.width() / 2 + 4, -20, self.width() - 8, 40)
        if self._selected:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.c("sel"))
            p.drawRoundedRect(rect, 10, 10)
        elif self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.c("hover"))
            p.drawRoundedRect(rect, 10, 10)

        av = QRectF(rect.left() + 6, -14, 28, 28)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(av)

        short = self.name.replace("说话人0", "").replace("说话人", "")
        if not short:
            short = self.name[:2]
        short = short[:3]
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(av, Qt.AlignCenter, short)

        f.setPixelSize(12.5)
        f.setBold(False)
        f.setWeight(QFont.Medium)
        p.setFont(f)
        p.setPen(theme.c("text"))
        p.drawText(QRectF(av.right() + 10, -18, rect.width() - 58, 19),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f).elidedText(self.name, Qt.ElideRight,
                                              int(rect.width() - 58)))

        mono = QFont(self.font())
        mono.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
        mono.setPixelSize(10.5)
        p.setFont(mono)
        p.setPen(theme.c("text3"))
        sub = f"{self.count} 句 · {self.seconds:.1f}s" if self.seconds else f"{self.count} 句"
        p.drawText(QRectF(av.right() + 10, 1, rect.width() - 58, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, sub)


# ---------------------------------------------------------------- 素材库
class LibraryList(QListWidget):
    """编辑态侧栏素材库：分组列出各人片段，可拖到轨道。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press = None
        self.setStyleSheet(
            "QListWidget { background: transparent; border: none; }"
            "QListWidget::item { padding: 3px 6px; border-radius: 8px; }"
            "QListWidget::item:selected { background: rgba(0,122,255,0.12); }")
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.setSelectionMode(QListWidget.SingleSelection)

    def add_group(self, text: str):
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        item.setFont(font)
        item.setForeground(theme.c("text3"))
        item.setSizeHint(QSize(0, 24))
        self.addItem(item)

    def add_segment(self, payload: dict, color: str, text: str, sub: str):
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, payload)
        item.setToolTip(f"{text}\n{sub}")
        item.setSizeHint(QSize(0, 38))
        item.setIcon(QIcon(self._dot(color)))
        self.addItem(item)

    @staticmethod
    def _dot(color: str) -> QPixmap:
        pix = QPixmap(12, 12)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(1, 1, 10, 10))
        p.end()
        return pix

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (e.buttons() & Qt.LeftButton) and self._press is not None \
                and (e.position().toPoint() - self._press).manhattanLength() > 8:
            item = self.itemAt(self._press)
            payload = item.data(Qt.UserRole) if item else None
            if payload:
                self._start_drag(payload)
                self._press = None
                return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._press = None
        super().mouseReleaseEvent(e)

    def _start_drag(self, payload: dict):
        import json

        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QDrag

        from core.edit_model import SEGMENT_MIME

        mime = QMimeData()
        mime.setData(SEGMENT_MIME, json.dumps(payload).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


# ---------------------------------------------------------------- 拖放区
class DropZone(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self._over = False
        self._apply_style()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignCenter)

        self.icon = _WaveTile()
        self.title = QLabel("把视频或音频拖到这里")
        self.title.setStyleSheet("font-size: 19px; font-weight: 600; letter-spacing: -0.3px;")
        self.sub = QLabel(
            "支持 MP4 / MOV / MKV / AVI / FLV / WEBM / WAV / MP3 / M4A / AAC / FLAC / OGG\n"
            "全程本地处理，素材不会上传")
        self.sub.setAlignment(Qt.AlignCenter)
        self.sub.setStyleSheet(f"color: {theme.TEXT2}; font-size: 12px;")
        self.button = SpringButton("选择文件", "primary")
        self.button.setMinimumWidth(110)

        lay.addWidget(self.icon, 0, Qt.AlignHCenter)
        lay.addSpacing(8)
        lay.addWidget(self.title, 0, Qt.AlignHCenter)
        lay.addWidget(self.sub, 0, Qt.AlignHCenter)
        lay.addSpacing(6)
        lay.addWidget(self.button, 0, Qt.AlignHCenter)

    def _apply_style(self):
        if self._over:
            self.setStyleSheet(
                "#DropZone { border-radius: 24px; background: qlineargradient("
                "x1:0,y1:0,x2:0,y2:1, stop:0 rgba(225,238,255,205),"
                " stop:1 rgba(210,230,255,140));"
                " border: 1px solid rgba(0,122,255,180); }")
        else:
            self.setStyleSheet(
                "#DropZone { border-radius: 24px; background: qlineargradient("
                "x1:0,y1:0,x2:0,y2:1, stop:0 rgba(255,255,255,128),"
                " stop:1 rgba(255,255,255,80));"
                " border: 1px solid rgba(255,255,255,220); }")

    def set_over(self, over: bool):
        if self._over == over:
            return
        self._over = over
        self._apply_style()
        self._animate_scale(1.0 if over else 0.0)

    def _animate_scale(self, target: float):
        from PySide6.QtCore import QVariantAnimation
        if not hasattr(self, "_pop"):
            self._pop = 0.0
            self._pop_anim = QVariantAnimation(self)
            self._pop_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._pop_anim.setDuration(300)
            self._pop_anim.valueChanged.connect(self._on_pop)
        self._pop_anim.stop()
        self._pop_anim.setStartValue(self._pop)
        self._pop_anim.setEndValue(target)
        self._pop_anim.start()

    def _on_pop(self, value):
        self._pop = float(value)
        margin = int(24 - 10 * self._pop)
        self.layout().setContentsMargins(margin, margin, margin, margin)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        inset = 14 + 4 * getattr(self, "_pop", 0.0)
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        pen = QPen(QColor(0, 122, 255, 200) if self._over else QColor(60, 60, 67, 56), 1.5)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, 16, 16)


class _WaveTile(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(76, 76)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        grad = QRadialGradient(20, 16, 90)
        grad.setColorAt(0, QColor(255, 255, 255, 255))
        grad.setColorAt(1, theme.c("hover"))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0, 0, 76, 76), 20, 20)

        pen = QPen(theme.c("accent"), 2.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        for i, h in enumerate([8, 18, 30, 12, 36, 22, 10]):
            x = 16 + i * 7.5
            p.drawLine(QPointF(x, 38 - h / 2), QPointF(x, 38 + h / 2))


# ---------------------------------------------------------------- 片段表格（悬浮放大镜）
class _TextCache:
    """文字按最大放大倍数预渲染成位图，放大时只缩放位图，不重新排版。"""

    def __init__(self, max_scale: float = 1.22):
        self.max_scale = max_scale
        self._cache: dict = {}

    def get(self, text: str, font: QFont, color: QColor, dpr: float):
        key = (text, font.key(), color.rgba(), round(dpr, 2))
        hit = self._cache.get(key)
        if hit is not None:
            return hit

        from PySide6.QtGui import QFontMetricsF, QPixmap

        fm = QFontMetricsF(font)
        w = int(fm.horizontalAdvance(text)) + 6
        h = int(fm.height()) + 2
        scale = dpr * self.max_scale
        pm = QPixmap(int(w * scale), int(h * scale))
        pm.setDevicePixelRatio(scale)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(QPointF(2, fm.ascent() + 1), text)
        painter.end()

        if len(self._cache) > 3000:
            self._cache.clear()
        self._cache[key] = (pm, w, h)
        return pm, w, h

    def clear(self):
        self._cache.clear()


class TableHeader(QWidget):
    """固定表头：纯白底、淡灰分隔线、下方渐变阴影（不随内容滚动）。"""

    def __init__(self, table):
        super().__init__(table)
        self.table = table
        self._scrolled = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)

    def set_scrolled(self, value: int):
        scrolled = value > 0
        if scrolled != self._scrolled:
            self._scrolled = scrolled
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.table.HEADER_H

        p.fillRect(0, 0, w, h, theme.c("header_bg"))
        p.fillRect(0, h - 1, w, 1, theme.c("header_line"))

        shadow = QLinearGradient(0, h, 0, h + 8)
        strength = (54 if self._scrolled else 34) if theme.dark else (30 if self._scrolled else 16)
        shadow.setColorAt(0, QColor(0, 0, 0, strength))
        shadow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(0, h, w, 8, shadow)

        p.setPen(theme.c("text2"))
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Medium)
        p.setFont(font)

        widths = self.table.column_widths()
        x = self.table.PAD_X
        for i, title in enumerate(["开始", "结束", "时长", "人物", "文字", "状态"]):
            p.drawText(QRectF(x, 0, widths[i], h),
                       Qt.AlignLeft | Qt.AlignVCenter, title)
            x += widths[i]


class SegmentTable(QAbstractScrollArea):
    """自绘表格：悬停行放大，文字用预渲染位图缩放，高亮条按内容坐标跟随。"""
    rowClicked = Signal(int)
    rowActivated = Signal(int)
    rowContextMenu = Signal(QPoint, int)
    textEdited = Signal(int, str)

    AMP = 0.10
    SIGMA = 30.0
    ROW_H = 38
    ROW_PAD = 6
    HEADER_H = 40
    COLS = [66, 66, 54, 88, -1, 66]
    PAD_X = 18
    TAU_SCALE = 0.06
    TAU_HL = 0.035
    MAX_SCALE = 1.10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._mouse_y = None
        self._row = -1
        self._ks = []
        self._hl_y = 0.0
        self._hl_target_y = 0.0
        self._hl_alpha = 0.0
        self._hl_target_alpha = 0.0
        self._editing = None
        self._press_pos = None
        self._dragging = False
        self._cache = _TextCache(self.MAX_SCALE)
        self._frame_costs: list[float] = []

        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("QAbstractScrollArea { background: transparent; }")
        self.setViewportMargins(0, self.HEADER_H, 0, 0)
        vp = self.viewport()
        vp.setAutoFillBackground(False)
        vp.setAttribute(Qt.WA_OpaquePaintEvent, True)
        vp.setAttribute(Qt.WA_NoSystemBackground, True)
        vp.setMouseTracking(True)

        self.header = TableHeader(self)
        self.header.raise_()
        self.verticalScrollBar().valueChanged.connect(self.header.set_scrolled)

        self._intervals = []
        self._last_tick = None

        self._font_time = QFont(self.font())
        self._font_time.setPixelSize(12)
        self._font_speaker = QFont(self.font())
        self._font_speaker.setPixelSize(13)
        self._font_speaker.setBold(True)
        self._font_speaker_strike = QFont(self._font_speaker)
        self._font_speaker_strike.setStrikeOut(True)
        self._font_text = QFont(self.font())
        self._font_text.setPixelSize(13)
        self._font_text_strike = QFont(self._font_text)
        self._font_text_strike.setStrikeOut(True)
        self._font_status = QFont(self.font())
        self._font_status.setPixelSize(11)
        self._font_status.setBold(True)

    # ---------- 数据 ----------
    def set_rows(self, rows: list[dict]):
        self._rows = rows
        self._ks = [1.0] * len(rows)
        self._hl_alpha = 0.0
        self._hl_target_alpha = 0.0
        self._reelide()
        self._update_scrollbar()
        self._recompute_hl_target()
        self.viewport().update()

    def set_current_row(self, index: int):
        self._row = index
        self.viewport().update()

    def column_widths(self) -> list[int]:
        total = self.viewport().width() - 2 * self.PAD_X
        fixed = sum(w for w in self.COLS if w > 0)
        return [w if w > 0 else max(120, total - fixed) for w in self.COLS]

    def _widths(self) -> list[int]:
        return self.column_widths()

    def _reelide(self):
        widths = self.column_widths()
        fm = QFontMetrics(self._font_text)
        for row in self._rows:
            row["_text"] = fm.elidedText(row.get("text", ""), Qt.ElideRight,
                                         int(widths[4] - 24))

    def _content_h(self) -> int:
        return self.ROW_PAD * 2 + len(self._rows) * self.ROW_H

    def _update_scrollbar(self):
        vp_h = self.viewport().height()
        self.verticalScrollBar().setRange(0, max(0, self._content_h() - vp_h))
        self.verticalScrollBar().setPageStep(vp_h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.header.setGeometry(cr.x(), cr.y(), self.viewport().width(),
                                self.HEADER_H + 8)
        self.header.raise_()
        self._update_scrollbar()
        self._reelide()
        self._cache.clear()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.header.set_scrolled(self.verticalScrollBar().value())
        self._recompute_hl_target()
        self.viewport().update()
        self._kick()

    # ---------- 高亮条（内容坐标） ----------
    def _recompute_hl_target(self):
        if self._mouse_y is None:
            self._hl_target_alpha = 0.0
            return
        content_y = self._mouse_y + self.verticalScrollBar().value() - self.ROW_PAD
        idx = int(content_y // self.ROW_H)
        if 0 <= idx < len(self._rows):
            new_y = self.ROW_PAD + idx * self.ROW_H
            if self._hl_alpha < 0.05:
                self._hl_y = new_y
            self._hl_target_y = new_y
            self._hl_target_alpha = 1.0
        else:
            self._hl_target_alpha = 0.0

    def _hl_rect(self) -> QRectF:
        y = self._hl_y - self.verticalScrollBar().value()
        return QRectF(6, y + 2, self.viewport().width() - 12, self.ROW_H - 4)

    # ---------- 动画 ----------
    def _kick(self):
        Ticker.instance().kick(self._anim_tick)

    def _anim_tick(self, dt: float) -> bool:
        if not self._rows:
            return False
        now = time.perf_counter()
        if self._last_tick is not None:
            self._intervals.append((now - self._last_tick) * 1000)
        self._last_tick = now
        if len(self._intervals) >= 60:
            ordered = sorted(self._intervals)
            print(f"[frame] interval median={ordered[len(ordered)//2]:.1f}ms "
                  f"p95={ordered[int(len(ordered)*0.95)]:.1f}ms "
                  f"max={ordered[-1]:.1f}ms", flush=True)
            self._intervals.clear()

        a_scale = 1 - math.exp(-dt / self.TAU_SCALE)
        a_hl = 1 - math.exp(-dt / self.TAU_HL)

        scroll = self.verticalScrollBar().value()
        local_hover = None
        if self._mouse_y is not None:
            local_hover = self._mouse_y + scroll - self.ROW_PAD
        hover_row = int(local_hover // self.ROW_H) if local_hover is not None else -1

        moving = False
        dirty = QRect()

        for i in range(max(0, hover_row - 4), min(len(self._rows), hover_row + 5)):
            target = 1.0
            if 0 <= hover_row < len(self._rows):
                center = self.ROW_PAD + i * self.ROW_H + self.ROW_H / 2
                d = local_hover - center
                target = 1 + self.AMP * math.exp(-d * d / (2 * self.SIGMA ** 2))
            k = self._ks[i]
            if abs(target - k) > 0.0005:
                self._ks[i] = k + (target - k) * a_scale
                moving = True
                dirty |= self._row_rect(i)

        if not (0 <= hover_row < len(self._rows)):
            for i in range(len(self._rows)):
                if abs(self._ks[i] - 1.0) > 0.001:
                    self._ks[i] += (1.0 - self._ks[i]) * a_scale
                    moving = True
                    dirty |= self._row_rect(i)

        old_hl = self._hl_rect().toAlignedRect()
        self._hl_y += (self._hl_target_y - self._hl_y) * a_hl
        self._hl_alpha += (self._hl_target_alpha - self._hl_alpha) * a_hl
        if (abs(self._hl_target_y - self._hl_y) > 0.3
                or abs(self._hl_target_alpha - self._hl_alpha) > 0.01):
            moving = True
        dirty |= old_hl | self._hl_rect().toAlignedRect()

        if not dirty.isEmpty():
            self.viewport().update(dirty.adjusted(-6, -6, 6, 6))
        return moving

    def _row_rect(self, index: int) -> QRect:
        y = self.ROW_PAD + index * self.ROW_H - self.verticalScrollBar().value()
        k = self._ks[index]
        h = int(self.ROW_H * k) + 8
        return QRect(0, int(y - (h - self.ROW_H) / 2), self.viewport().width(), h)

    # ---------- 事件 ----------
    def mouseMoveEvent(self, e):
        self._mouse_y = e.position().y()
        self._recompute_hl_target()
        self._kick()
        if (e.buttons() & Qt.LeftButton) and not self._dragging \
                and self._press_pos is not None \
                and (e.position() - self._press_pos).manhattanLength() > 8:
            self._start_drag(self._row_at(self._press_pos.y()))
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._mouse_y = None
        self._recompute_hl_target()
        self._kick()
        super().leaveEvent(e)

    def wheelEvent(self, e):
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - int(e.angleDelta().y() / 120) * 60)
        self._recompute_hl_target()
        self.viewport().update()

    def _row_at(self, y: float) -> int:
        scroll = self.verticalScrollBar().value()
        idx = int((y + scroll - self.ROW_PAD) // self.ROW_H)
        return idx if 0 <= idx < len(self._rows) else -1

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = e.position()
            self._dragging = False
            idx = self._row_at(e.position().y())
            if idx >= 0:
                self._row = idx
                self.rowClicked.emit(idx)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._press_pos = None
        self._dragging = False
        super().mouseReleaseEvent(e)

    def _start_drag(self, idx: int) -> bool:
        if not (0 <= idx < len(self._rows)):
            return False
        payload = self._rows[idx].get("_payload")
        if not payload:
            return False
        import json

        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QDrag

        from core.edit_model import SEGMENT_MIME

        mime = QMimeData()
        mime.setData(SEGMENT_MIME, json.dumps(payload).encode("utf-8"))
        self._dragging = True
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)
        self._dragging = False
        return True

    def mouseDoubleClickEvent(self, e):
        idx = self._row_at(e.position().y())
        if idx >= 0:
            if self._col_at(e.position().x()) == 4:
                self._start_edit(idx)
            else:
                self.rowActivated.emit(idx)
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        idx = self._row_at(e.pos().y())
        if idx >= 0:
            self.rowContextMenu.emit(self.viewport().mapToGlobal(e.pos()), idx)
        e.accept()

    def _col_at(self, x: float) -> int:
        widths = self.column_widths()
        acc = self.PAD_X
        for i, w in enumerate(widths):
            if acc <= x <= acc + w:
                return i
            acc += w
        return -1

    def _start_edit(self, idx: int):
        if not (0 <= idx < len(self._rows)):
            return
        widths = self.column_widths()
        x = self.PAD_X + sum(widths[:4])
        y = self.ROW_PAD + idx * self.ROW_H - self.verticalScrollBar().value()
        edit = QLineEdit(self.viewport())
        edit.setText(self._rows[idx].get("text", ""))
        edit.setGeometry(int(x + 4), int(y + 6), int(widths[4] - 12), int(self.ROW_H - 12))
        edit.setFocus()
        edit.selectAll()
        edit.show()
        self._editing = edit

        def finish():
            if self._editing is None:
                return
            text = edit.text().strip()
            row = idx
            edit.deleteLater()
            self._editing = None
            if text:
                self.textEdited.emit(row, text)

        edit.editingFinished.connect(finish)
        edit.returnPressed.connect(edit.clearFocus)

    # ---------- 绘制 ----------
    def paintEvent(self, event):
        cost = QElapsedTimer()
        cost.start()
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        rect = event.rect()
        p.fillRect(rect, theme.c("table_bg"))

        scroll = self.verticalScrollBar().value()
        widths = self.column_widths()
        w = self.viewport().width()

        if self._hl_alpha > 0.01:
            color = QColor(255, 255, 255)
            color.setAlphaF(0.9 * self._hl_alpha)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(self._hl_rect(), 12, 12)

        dpr = self.devicePixelRatioF()
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        for i, row in enumerate(self._rows):
            y = self.ROW_PAD + i * self.ROW_H - scroll
            if y > rect.bottom() + self.ROW_H or y + self.ROW_H < rect.top() - self.ROW_H:
                continue
            k = self._ks[i] if i < len(self._ks) else 1.0
            cy = y + self.ROW_H / 2

            if i == self._row:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 122, 255, 26))
                p.drawRoundedRect(QRectF(8, cy - 17, w - 16, 34), 10, 10)

            col_x = self.PAD_X
            self._draw_cell(p, row.get("start", ""), self._font_time,
                            theme.c("text2"), col_x, cy, 1.0, dpr)
            col_x += widths[0]
            self._draw_cell(p, row.get("end", ""), self._font_time,
                            theme.c("text2"), col_x, cy, 1.0, dpr)
            col_x += widths[1]
            self._draw_cell(p, row.get("dur", ""), self._font_time,
                            theme.c("text3"), col_x, cy, 1.0, dpr)
            col_x += widths[2]
            excluded = row.get("status") == "已排除"
            speaker_color = QColor(row.get("color", theme.ACCENT))
            if excluded:
                speaker_color.setAlpha(120)
            speaker_font = (self._font_speaker_strike if excluded
                            else self._font_speaker)
            self._draw_cell(p, row.get("speaker", ""), speaker_font,
                            speaker_color, col_x, cy, 1.0, dpr)
            col_x += widths[3]

            indent = (k - 1) * 14
            text_color = theme.c("text3") if excluded else theme.c("text")
            text_font = self._font_text_strike if excluded else self._font_text
            self._draw_cell(p, row.get("_text", ""), text_font, text_color,
                            col_x + indent, cy, k, dpr)
            col_x += widths[4]

            status = row.get("status", "")
            if status:
                if status == "重叠":
                    fg = QColor("#FFB340") if theme.dark else QColor("#E08600")
                    bg = QColor(255, 149, 0, 44)
                elif status == "已排除":
                    fg = theme.c("text2")
                    bg = QColor(120, 120, 128, 44)
                else:
                    fg = theme.c("accent")
                    bg = QColor(0, 122, 255, 36)
                pm, pw, ph = self._cache.get(status, self._font_status, fg, dpr)
                pad = 8.0
                p.setPen(Qt.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(QRectF(col_x, cy - 10, pw + pad * 2, 20), 10, 10)
                p.drawPixmap(QRectF(col_x + pad, cy - ph / 2, pw, ph),
                             pm, QRectF(pm.rect()))

            p.setPen(QPen(theme.c("border"), 1))
            p.drawLine(8, y + self.ROW_H - 0.5, w - 8, y + self.ROW_H - 0.5)

        elapsed = cost.nsecsElapsed() / 1e6
        self._frame_costs.append(elapsed)
        if len(self._frame_costs) >= 60:
            avg = sum(self._frame_costs) / len(self._frame_costs)
            worst = max(self._frame_costs)
            print(f"[table] paint avg={avg:.2f}ms worst={worst:.2f}ms", flush=True)
            self._frame_costs.clear()

    def _draw_cell(self, p, text: str, font: QFont, color: QColor,
                   x: float, cy: float, k: float, dpr: float):
        if not text:
            return
        pm, w, h = self._cache.get(text, font, color, dpr)
        target = QRectF(x, cy - h * k / 2, w * k, h * k)
        p.drawPixmap(target, pm, QRectF(pm.rect()))


# ---------------------------------------------------------------- 设置 / 主题按钮
class GearButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self._rotate = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_rot)

    def _on_rot(self, value):
        self._rotate = float(value)
        self.update()

    def enterEvent(self, e):
        self._anim.stop()
        self._anim.setStartValue(self._rotate)
        self._anim.setEndValue(30.0)
        self._anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim.stop()
        self._anim.setStartValue(self._rotate)
        self._anim.setEndValue(0.0)
        self._anim.start()
        super().leaveEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate(self._rotate)
        color = theme.c("text2")
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        path = QPainterPath()
        teeth = 8
        for i in range(teeth * 2):
            a = math.pi * i / teeth
            rr = 8.0 if i % 2 == 0 else 6.4
            pt = QPointF(rr * math.cos(a), rr * math.sin(a))
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        path.closeSubpath()
        hole = QPainterPath()
        hole.addEllipse(QPointF(0, 0), 3.0, 3.0)
        p.drawPath(path.subtracted(hole))


class ThemeToggle(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self._rotate = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(320)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_rot)

    def _on_rot(self, value):
        self._rotate = float(value)
        self.update()

    def animate(self):
        self._anim.stop()
        self._anim.setStartValue(self._rotate)
        self._anim.setEndValue(self._rotate + 90.0)
        self._anim.start()

    def enterEvent(self, e):
        self.animate()
        super().enterEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate(self._rotate)
        if theme.dark:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.c("text2"))
            moon = QPainterPath()
            moon.addEllipse(QPointF(0, 0), 7.0, 7.0)
            cut = QPainterPath()
            cut.addEllipse(QPointF(3.2, -2.4), 6.2, 6.2)
            p.drawPath(moon.subtracted(cut))
        else:
            p.setPen(QPen(theme.c("text2"), 1.6))
            p.drawEllipse(QPointF(0, 0), 4.2, 4.2)
            for i in range(8):
                a = math.pi * i / 4
                p.drawLine(QPointF(6.2 * math.cos(a), 6.2 * math.sin(a)),
                           QPointF(8.4 * math.cos(a), 8.4 * math.sin(a)))


class ThemeTransition(QWidget):
    """截图 + 圆形扩散的主题切换过渡。"""

    def __init__(self, win):
        super().__init__(win)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        self._old = None
        self._t = 0.0
        self._origin = QPointF(0, 0)
        self._radius = 1.0
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(460)
        self.anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._on_t)
        self.anim.finished.connect(self._end)

    def _on_t(self, value):
        self._t = float(value)
        self.update()

    def run(self, apply_cb, origin: QPoint):
        win = self.parentWidget()
        self._old = win.grab()
        self._origin = QPointF(origin)
        corners = [(0, 0), (win.width(), 0), (0, win.height()),
                   (win.width(), win.height())]
        self._radius = max(math.hypot(origin.x() - x, origin.y() - y)
                           for x, y in corners) or 1.0
        self.setGeometry(win.rect())
        self.show()
        self.raise_()
        apply_cb()
        self.anim.stop()
        self.anim.start()

    def _end(self):
        self._old = None
        self.hide()

    def paintEvent(self, event):
        if self._old is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        full = QPainterPath()
        full.addRect(QRectF(self.rect()))
        hole = QPainterPath()
        r = self._radius * self._t
        hole.addEllipse(self._origin, r, r)
        p.setClipPath(full.subtracted(hole))
        p.drawPixmap(0, 0, self._old)
