"""Colour palettes and the application stylesheet (light and dark)."""
from __future__ import annotations

import re

PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#f4f6fb", "surface": "#ffffff", "border": "#e1e6ef", "text": "#17212f", "muted": "#667085",
        "primary": "#3350d9", "primary_hover": "#2a43b8", "primary_text": "#ffffff",
        "sidebar": "#0f1a36", "sidebar_text": "#c9d3ea", "sidebar_active": "#1f3068", "sidebar_muted": "#7d8bb0",
        "danger": "#c62828", "success": "#1b7f4b", "warning": "#b26a00",
        "row_alt": "#f8f9fd", "selection": "#dfe6ff", "input_bg": "#ffffff", "hover": "#eef1f9",
        "bar_udhaar": "#e0575b", "bar_jama": "#2fa36b",
    },
    "dark": {
        "bg": "#0d1220", "surface": "#151c2e", "border": "#26304a", "text": "#e8ecf6", "muted": "#93a0bd",
        "primary": "#6b86ff", "primary_hover": "#869cff", "primary_text": "#0b1020",
        "sidebar": "#0a0f1d", "sidebar_text": "#aab6d3", "sidebar_active": "#18223d", "sidebar_muted": "#6c7a9c",
        "danger": "#ff7b7b", "success": "#4cd694", "warning": "#f0b44c",
        "row_alt": "#121a2b", "selection": "#25335f", "input_bg": "#101728", "hover": "#1b2440",
        "bar_udhaar": "#ff7b7b", "bar_jama": "#4cd694",
    },
}

_current = "light"


def current_name() -> str:
    return _current


def current() -> dict[str, str]:
    return PALETTES[_current]


def set_current(name: str) -> None:
    global _current
    _current = name if name in PALETTES else "light"


_TEMPLATE = """
* { font-size: 13px; }
QMainWindow, QDialog, QStackedWidget { background: @bg@; color: @text@; }
QWidget#page { background: @bg@; }
QLabel { color: @text@; background: transparent; }
QLabel#h1 { font-size: 22px; font-weight: 700; }
QLabel#h2 { font-size: 15px; font-weight: 600; }
QLabel#muted, QLabel[muted="true"] { color: @muted@; }
QLabel#kpiValue { font-size: 24px; font-weight: 700; }
QLabel#kpiTitle { color: @muted@; font-size: 12px; font-weight: 600; }
QLabel#kpiCaption { color: @muted@; font-size: 11px; }
QLabel[kind="danger"] { color: @danger@; font-weight: 600; }
QLabel[kind="success"] { color: @success@; font-weight: 600; }
QLabel[kind="warning"] { color: @warning@; font-weight: 600; }

QFrame#sidebar { background: @sidebar@; }
QLabel#brand { color: #ffffff; font-size: 17px; font-weight: 800; letter-spacing: 1px; background: transparent; }
QLabel#brandSub { color: @sidebar_muted@; font-size: 11px; background: transparent; }
QLabel#sideNote { color: @sidebar_muted@; font-size: 11px; background: transparent; }
QPushButton#nav { background: transparent; color: @sidebar_text@; border: none; border-radius: 8px;
                  text-align: left; padding: 10px 14px; font-size: 13px; }
QPushButton#nav:hover { background: @sidebar_active@; }
QPushButton#nav:checked { background: @sidebar_active@; color: #ffffff; font-weight: 700; }
QPushButton#sideTool { background: transparent; color: @sidebar_text@; border: 1px solid @sidebar_active@;
                       border-radius: 8px; padding: 7px 10px; }
QPushButton#sideTool:hover { background: @sidebar_active@; }

QFrame#card { background: @surface@; border: 1px solid @border@; border-radius: 12px; }
QFrame#card QLabel { background: transparent; }

QPushButton { background: @surface@; color: @text@; border: 1px solid @border@; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { background: @hover@; }
QPushButton:disabled { color: @muted@; }
QPushButton#primary { background: @primary@; color: @primary_text@; border: 1px solid @primary@; font-weight: 600; }
QPushButton#primary:hover { background: @primary_hover@; }
QPushButton#danger { color: @danger@; }

QLineEdit, QComboBox, QDateEdit, QPlainTextEdit, QSpinBox {
    background: @input_bg@; color: @text@; border: 1px solid @border@; border-radius: 8px; padding: 6px 8px;
    selection-background-color: @selection@; selection-color: @text@; }
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QPlainTextEdit:focus { border: 1px solid @primary@; }
QLineEdit:read-only { background: @row_alt@; }
QComboBox QAbstractItemView { background: @surface@; color: @text@; selection-background-color: @selection@;
                              selection-color: @text@; border: 1px solid @border@; }
QCalendarWidget QWidget { background: @surface@; color: @text@; }

QTableWidget { background: @surface@; alternate-background-color: @row_alt@; color: @text@; border: 1px solid @border@;
               border-radius: 10px; gridline-color: transparent; selection-background-color: @selection@;
               selection-color: @text@; }
QTableWidget::item { padding: 4px 8px; border: none; }
QHeaderView::section { background: @surface@; color: @muted@; border: none; border-bottom: 1px solid @border@;
                       padding: 8px; font-weight: 600; font-size: 12px; }
QTableCornerButton::section { background: @surface@; border: none; }

QTabWidget::pane { border: 1px solid @border@; border-radius: 10px; background: @bg@; top: -1px; }
QTabBar::tab { background: transparent; color: @muted@; padding: 8px 16px; border: none; font-weight: 600; }
QTabBar::tab:selected { color: @primary@; border-bottom: 2px solid @primary@; }

QGroupBox { background: @surface@; border: 1px solid @border@; border-radius: 12px; margin-top: 14px;
            padding: 14px 12px 10px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: @muted@; }

QProgressBar { background: @border@; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }
QProgressBar::chunk { background: @primary@; border-radius: 4px; }
QProgressBar[state="warn"]::chunk { background: @warning@; }
QProgressBar[state="over"]::chunk { background: @danger@; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: @border@; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: @border@; border-radius: 4px; min-width: 30px; }

QStatusBar { background: @surface@; color: @muted@; border-top: 1px solid @border@; }
QToolTip { background: @surface@; color: @text@; border: 1px solid @border@; padding: 4px; }
QMessageBox { background: @surface@; }
QCheckBox, QRadioButton { color: @text@; spacing: 6px; }
"""


def build_stylesheet(name: str) -> str:
    palette = PALETTES.get(name, PALETTES["light"])
    return re.sub(r"@(\w+)@", lambda m: palette[m.group(1)], _TEMPLATE)
