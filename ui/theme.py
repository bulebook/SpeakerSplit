"""主题：浅色 / 深色两套令牌，所有界面颜色都从这里取。"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor

LIGHT = {
    "bg": "#F2F2F7",
    "text": "#1D1D1F",
    "text2": "#3C3C4399",
    "text3": "#3C3C4357",
    "accent": "#007AFF",
    "accent_hover": "#1A88FF",
    "accent_press": "#0062CC",
    "sel": "#007AFF1F",
    "border": "#3C3C431F",
    "hover": "#FFFFFF99",
    "card_top": "#FFFFFFA3",
    "card_bottom": "#FFFFFF70",
    "card_border": "#FFFFFFDC",
    "group_top": "#FFFFFFBE",
    "group_bottom": "#FFFFFF82",
    "group_border": "#FFFFFFE1",
    "header_bg": "#FFFFFF",
    "header_line": "#E5E7EB",
    "table_bg": "#F7F8FB",
    "hl": "#FFFFFFD9",
    "dialog_top": "#FFFFFFE8",
    "dialog_bottom": "#FFFFFFCD",
    "dialog_border": "#FFFFFFEB",
    "menu_bg": "#FCFCFEF8",
    "menu_border": "#3C3C431F",
    "tooltip_bg": "#1E1E22F0",
    "shadow": "#181F503E",
    "input_bg": "#FFFFFFCD",
    "scroll": "#3C3C433C",
    "row_excluded": "#3C3C435A",
    "timeline_bg": "#FAFAFC",
    "wave": "#3C3C4373",
}

DARK = {
    "bg": "#1C1C1E",
    "text": "#F5F5F7",
    "text2": "#EBEBF599",
    "text3": "#EBEBF55C",
    "accent": "#0A84FF",
    "accent_hover": "#339CFF",
    "accent_press": "#0068D6",
    "sel": "#0A84FF33",
    "border": "#5454584D",
    "hover": "#FFFFFF14",
    "card_top": "#2C2C2EC4",
    "card_bottom": "#242426B4",
    "card_border": "#FFFFFF1F",
    "group_top": "#3A3A3CDD",
    "group_bottom": "#2E2E30C9",
    "group_border": "#FFFFFF24",
    "header_bg": "#2C2C2E",
    "header_line": "#3A3A3C",
    "table_bg": "#232325",
    "hl": "#FFFFFF1A",
    "dialog_top": "#2C2C2EF2",
    "dialog_bottom": "#262628F0",
    "dialog_border": "#FFFFFF1F",
    "menu_bg": "#2C2C2EF8",
    "menu_border": "#FFFFFF1F",
    "tooltip_bg": "#3A3A3CF5",
    "shadow": "#0000008C",
    "input_bg": "#3A3A3CD9",
    "scroll": "#FFFFFF33",
    "row_excluded": "#EBEBF55C",
    "timeline_bg": "#3A3A3C",
    "wave": "#EBEBF58C",
}

SPEAKER_LIGHT = ["#007AFF", "#34C759", "#FF9500", "#AF52DE",
                 "#FF3B30", "#5AC8FA", "#FFCC00", "#5856D6"]
SPEAKER_DARK = ["#0A84FF", "#30D158", "#FF9F0A", "#BF5AF2",
                "#FF375F", "#64D2FF", "#FFD60A", "#5E5CE6"]


def _color(value: str) -> QColor:
    if len(value) == 9:
        return QColor(int(value[1:3], 16), int(value[3:5], 16),
                      int(value[5:7], 16), int(value[7:9], 16))
    return QColor(value)


def _rgba(value: str) -> str:
    """#RRGGBBAA -> rgba(r,g,b,a)"""
    if len(value) == 9:
        r, g, b = int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
        a = int(value[7:9], 16)
        return f"rgba({r},{g},{b},{a})"
    return value


class Theme(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.dark = False
        self._palette = dict(LIGHT)
        self._colors = {k: _color(v) for k, v in self._palette.items()}

    # ---- 取色 ----
    def c(self, name: str) -> QColor:
        return self._colors.get(name, QColor("#FF00FF"))

    def s(self, name: str) -> str:
        return _rgba(self._palette.get(name, "#FF00FF"))

    @property
    def speakers(self) -> list[str]:
        return SPEAKER_DARK if self.dark else SPEAKER_LIGHT

    # ---- 切换 ----
    def apply(self, dark: bool):
        self.dark = bool(dark)
        self._palette = dict(DARK if self.dark else LIGHT)
        self._colors = {k: _color(v) for k, v in self._palette.items()}
        self.changed.emit()

    def qss(self) -> str:
        p = {k: _rgba(v) for k, v in self._palette.items()}
        return f"""
* {{
    font-size: 13px;
    color: {p['text']};
}}
QWidget {{ background: transparent; }}
#Root, #Window {{ background: transparent; }}

#H1 {{ font-size: 22px; font-weight: 700; letter-spacing: -0.4px; color: {p['text']}; }}
#Subtitle {{ font-size: 12.5px; color: {p['text2']}; }}
#SectionTitle {{ font-size: 14px; font-weight: 600; letter-spacing: -0.2px; color: {p['text']}; }}
#Hint {{ font-size: 12px; color: {p['text3']}; }}
#SideTitle {{ font-size: 11px; font-weight: 600; letter-spacing: 0.5px; color: {p['text3']}; }}
#SideMeta {{ font-size: 11px; color: {p['text3']}; }}
#SideHint {{ font-size: 11px; color: {p['text3']}; }}
#VersionLabel {{ font-size: 11px; color: {p['text2']}; }}
#PlayerMeta {{ font-size: 11px; color: {p['text2']}; }}
#PlayerSub {{ font-size: 11.5px; color: {p['text2']}; }}
#StageMeta {{ font-size: 12px; font-weight: 500; color: {p['text2']}; }}
#ReadyHint {{ font-size: 13px; color: {p['text2']}; }}
#Count {{ color: {p['text2']}; font-weight: 500; }}

#Card {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {p['card_top']}, stop:1 {p['card_bottom']});
    border: 1px solid {p['card_border']};
    border-radius: 18px;
}}
#Group {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {p['group_top']}, stop:1 {p['group_bottom']});
    border: 1px solid {p['group_border']};
    border-radius: 19px;
}}
#VLine {{ background: {p['border']}; max-width: 1px; min-width: 1px; }}
#SettingsCard {{
    background: {p['bg']};
    border: 1px solid {p['card_border']};
    border-radius: 18px;
}}

QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {p['group_top']}, stop:1 {p['group_bottom']});
    border: 1px solid {p['group_border']};
    border-radius: 15px;
    padding: 7px 15px;
    color: {p['text']};
    font-weight: 500;
}}
QPushButton:hover {{ background: {p['hover']}; }}
QPushButton:pressed {{ background: {p['sel']}; }}
QPushButton:disabled {{ color: {p['text3']}; }}

QPushButton#Primary {{
    color: #FFFFFF;
    font-weight: 600;
    border: 1px solid rgba(255,255,255,90);
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {p['accent_hover']}, stop:1 {p['accent']});
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {p['accent_hover']}, stop:1 {p['accent_hover']});
}}
QPushButton#Primary:disabled {{ background: {p['text3']}; }}
QPushButton#Ghost {{
    background: transparent; border: none; color: {p['accent']};
    padding: 3px 9px; border-radius: 10px; font-size: 12px;
}}
QPushButton#Ghost:hover {{ background: {p['sel']}; }}
QPushButton#Tool {{
    background: transparent; border: none; padding: 0 13px;
    height: 30px; border-radius: 15px; font-weight: 500;
}}
QPushButton#Tool:hover {{ background: {p['hover']}; }}

QComboBox {{
    background: transparent; border: none; padding: 0 24px 0 13px;
    height: 30px; font-weight: 500; color: {p['text']};
}}
QComboBox:hover {{ background: {p['hover']}; border-radius: 15px; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{
    background: {p['menu_bg']};
    border: 1px solid {p['menu_border']};
    border-radius: 12px; padding: 4px;
    selection-background-color: {p['sel']};
    selection-color: {p['text']};
    outline: none;
}}

QProgressBar {{
    background: {p['border']}; border: none; border-radius: 3px;
    max-height: 6px; min-height: 6px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p['accent_hover']}, stop:1 {p['accent']});
}}

QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 12px; margin: 1px 4px; }}
QListWidget::item:hover {{ background: {p['hover']}; }}
QListWidget::item:selected {{ background: {p['sel']}; color: {p['text']}; }}

QTableWidget {{
    background: transparent; border: none; gridline-color: transparent; outline: none;
}}
QTableWidget::item {{ border-bottom: 1px solid {p['border']}; padding: 4px 8px; }}
QTableWidget::item:hover {{ background: {p['hover']}; }}
QTableWidget::item:selected {{ background: {p['sel']}; color: {p['text']}; }}
QHeaderView::section {{
    background: transparent; border: none; border-bottom: 1px solid {p['border']};
    padding: 6px 8px; color: {p['text2']}; font-size: 11.5px; font-weight: 500;
}}
QTableCornerButton::section {{ background: transparent; border: none; }}

QSlider::groove:horizontal {{ height: 5px; background: {p['border']}; border-radius: 4px; }}
QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 4px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0;
    background: #FFFFFF; border: none; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['scroll']}; border-radius: 5px; min-height: 32px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p['scroll']}; border-radius: 5px; min-width: 32px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

QMenu {{
    background: {p['menu_bg']}; border: 1px solid {p['menu_border']};
    border-radius: 14px; padding: 6px;
}}
QMenu::item {{ padding: 7px 20px 7px 12px; border-radius: 8px; color: {p['text']}; }}
QMenu::item:selected {{ background: {p['accent']}; color: #FFFFFF; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 5px 10px; }}

QLineEdit {{
    background: {p['input_bg']};
    border: 1px solid {p['border']};
    border-radius: 10px; padding: 7px 12px; color: {p['text']};
    selection-background-color: {p['accent']}; selection-color: #FFFFFF;
}}
QLineEdit:focus {{ border: 1px solid {p['accent']}; }}

#DialogCard {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {p['dialog_top']}, stop:1 {p['dialog_bottom']});
    border: 1px solid {p['dialog_border']};
    border-radius: 24px;
}}
#DialogTitle {{ font-size: 15px; font-weight: 600; letter-spacing: -0.2px; color: {p['text']}; }}
#DialogBody {{ color: {p['text2']}; font-size: 13px; }}

QToolTip {{
    background: {p['tooltip_bg']}; color: #FFFFFF;
    border: none; border-radius: 12px; padding: 10px 12px;
}}
"""


theme = Theme()

# 兼容旧代码：模块级别名（浅色值，仅供未迁移的静态样式使用）
TEXT = LIGHT["text"]
TEXT2 = _rgba(LIGHT["text2"])
TEXT3 = _rgba(LIGHT["text3"])
ACCENT = LIGHT["accent"]
ACCENT_HOVER = LIGHT["accent_hover"]
ACCENT_PRESS = LIGHT["accent_press"]
BORDER = _rgba(LIGHT["border"])
SELECT = _rgba(LIGHT["sel"])
SPEAKER_COLORS = SPEAKER_LIGHT
RADIUS_WINDOW = 20
RADIUS_CARD = 18
RADIUS_CTRL = 15
QSS = theme.qss()


# ---- 模块级代理：允许 ui.* 里直接写 theme.c(...) / theme.dark ----
def c(name: str) -> QColor:
    return theme.c(name)


def s(name: str) -> str:
    return theme.s(name)


def __getattr__(name):
    return getattr(theme, name)
