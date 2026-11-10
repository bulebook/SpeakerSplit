"""自绘菜单与提示气泡：动画只发生在绘制层，不缩放窗口。"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QPoint, QRect, QRectF, Qt, QTimer, QVariantAnimation,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QPainter,
    QPainterPath, QPen, QTextDocument,
)
from PySide6.QtWidgets import QWidget

from . import theme


class _PopMixin:
    """从 88% 缩放到 100%，只改绘制参数，不动窗口几何。"""

    def _init_pop(self):
        self._t = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_t)

    def _on_t(self, value):
        self._t = float(value)
        self.update()

    def _play_pop(self):
        self._anim.stop()
        self._anim.start()

    def _scale(self) -> float:
        return 0.88 + 0.12 * self._t


class GlassMenu(QWidget, _PopMixin):
    ITEM_H = 28
    PAD = 6

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._items: list = []
        self._hover = -1
        self._init_pop()
        font = self.font()
        font.setPixelSize(13)
        self.setFont(font)

    def show_at(self, pos: QPoint, items: list):
        self._items = list(items)
        self._hover = -1
        fm = QFontMetrics(self.font())
        texts = [t for t, _ in items if t]
        width = max([fm.horizontalAdvance(t) for t in texts] + [150]) + 40
        width = max(210, width)
        height = self.PAD * 2 + sum(self.ITEM_H if t else 9 for t, _ in items)
        width, height = int(width), int(height)

        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(geo.left() + 6, pos.x()), geo.right() - width - 6)
        y = min(max(geo.top() + 6, pos.y()), geo.bottom() - height - 6)
        self.setGeometry(QRect(x, y, width, height))
        self.show()
        self.setFocus()
        self._play_pop()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(min(1.0, self._t * 1.5))
        s = self._scale()
        p.scale(s, s)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0, theme.c("menu_bg"))
        grad.setColorAt(1, theme.c("menu_bg"))
        p.fillPath(path, grad)
        p.setPen(QPen(theme.c("border"), 1))
        p.drawPath(path)

        y = self.PAD
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Medium)
        p.setFont(font)
        for i, (label, _) in enumerate(self._items):
            if not label:
                p.setPen(QPen(theme.c("border"), 1))
                p.drawLine(10, y + 4, self.width() - 10, y + 4)
                y += 9
                continue
            item_rect = QRectF(6, y, self.width() - 12, self.ITEM_H)
            if i == self._hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(theme.ACCENT))
                p.drawRoundedRect(item_rect, 8, 8)
                p.setPen(QColor("#FFFFFF"))
            else:
                p.setPen(theme.c("text"))
            p.drawText(item_rect.adjusted(10, 0, -8, 0),
                       Qt.AlignLeft | Qt.AlignVCenter, label)
            y += self.ITEM_H

    def _item_at(self, y: float) -> int:
        top = self.PAD
        for i, (label, _) in enumerate(self._items):
            height = self.ITEM_H if label else 9
            if top <= y <= top + height:
                return i if label else -1
            top += height
        return -1

    def mouseMoveEvent(self, e):
        idx = self._item_at(e.position().y())
        if idx != self._hover:
            self._hover = idx
            self.update()

    def mouseReleaseEvent(self, e):
        idx = self._item_at(e.position().y())
        self.close()
        if 0 <= idx < len(self._items):
            fn = self._items[idx][1]
            if fn:
                fn()

    def keyPressEvent(self, e):
        rows = [i for i, (t, _) in enumerate(self._items) if t]
        if e.key() == Qt.Key_Down and rows:
            self._hover = rows[0] if self._hover not in rows else rows[min(
                rows.index(self._hover) + 1, len(rows) - 1)]
            self.update()
        elif e.key() == Qt.Key_Up and rows:
            self._hover = rows[0] if self._hover not in rows else rows[max(
                rows.index(self._hover) - 1, 0)]
            self.update()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            if 0 <= self._hover < len(self._items):
                fn = self._items[self._hover][1]
                self.close()
                if fn:
                    fn()
        elif e.key() == Qt.Key_Escape:
            self.close()


class GlassTooltip(QWidget, _PopMixin):
    WIDTH = 460

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._doc = QTextDocument(self)
        self._doc.setDocumentMargin(0)
        font = self.font()
        font.setPixelSize(12)
        self._doc.setDefaultFont(font)
        self._init_pop()

    def show_html(self, html: str, anchor: QPoint):
        self._doc.setHtml(html)
        self._doc.setTextWidth(self.WIDTH)
        size = self._doc.size()
        w = int(self.WIDTH) + 32
        h = int(size.height()) + 28
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(geo.left() + 6, anchor.x() - w // 2), geo.right() - w - 6)
        y = min(anchor.y(), geo.bottom() - h - 6)
        self.setGeometry(QRect(x, y, w, h))
        self.show()
        self._play_pop()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(min(1.0, self._t * 1.6))
        s = self._scale()
        p.translate(self.width() / 2, 0)
        p.scale(s, s)
        p.translate(-self.width() / 2, 0)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        p.fillPath(path, theme.c("tooltip_bg"))

        p.translate(16, 14)
        p.setPen(QColor("#FFFFFF"))
        self._doc.drawContents(p, QRectF(0, 0, self.WIDTH, self._doc.size().height()))


class InfoBadge(QWidget):
    """橙色感叹号，悬停延迟后弹出气泡。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.WhatsThisCursor)
        self._html = ""
        self._tip: GlassTooltip | None = None
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.setInterval(120)
        self._delay.timeout.connect(self._show_tip)
        self.setMouseTracking(True)

    def set_html(self, html: str):
        self._html = html

    def enterEvent(self, e):
        self._delay.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._delay.stop()
        if self._tip is not None:
            self._tip.hide()
        super().leaveEvent(e)

    def _show_tip(self):
        if not self._html:
            return
        if self._tip is None:
            self._tip = GlassTooltip()
        self._tip.show_html(self._html, self.mapToGlobal(QPoint(0, self.height() + 8)))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(theme.c("border"), 1.3))
        p.setBrush(theme.c("hover"))
        p.drawEllipse(QRectF(1.5, 1.5, 15, 15))

        font = QFont(self.font())
        font.setPixelSize(11)
        font.setBold(True)
        p.setFont(font)
        p.setPen(theme.c("text2"))
        p.drawText(self.rect(), Qt.AlignCenter, "i")
