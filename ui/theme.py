"""Dwarf Therapist-like palette: dark grid, saturated status cells, gradient skill/stress cells."""

from __future__ import annotations

from PySide6.QtGui import QColor

BG = QColor("#1e1f22")
GRID = QColor("#33353a")
TEXT = QColor("#d8d8d8")
DIM = QColor("#8a8d93")

OK = QColor("#2e7d32")
WARN = QColor("#b28704")
BAD = QColor("#b71c1c")
INFO = QColor("#1565c0")
EXCESS = QColor("#6a1b9a")
NEUTRAL = QColor("#3a3d42")

STATUS_COLORS: dict[str, QColor] = {
    "ok": OK, "low": WARN, "critical": BAD, "excess": EXCESS, "untracked": NEUTRAL,
    "queued": NEUTRAL, "active": INFO, "done": OK, "cancelled": QColor("#555"),
    "autopilot": INFO, "user": NEUTRAL, "emergency": BAD,
    "planned": QColor("#00695c"), "blocked": WARN, "digging": QColor("#6d4c41"), "dug": QColor("#8d6e63"), "building": INFO,
    "missing_zone": BAD, "free": OK,
}


def lerp(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def ramp(t: float) -> QColor:
    """0 = green, 0.5 = amber, 1 = red."""
    return lerp(OK, WARN, t * 2) if t < 0.5 else lerp(WARN, BAD, (t - 0.5) * 2)


def skill_color(level: int, enabled: bool) -> QColor:
    """Therapist-style skill cell: darker = untrained, brighter = expert; blue tint if labor enabled."""
    base = QColor("#1f3a5f") if enabled else QColor("#2a2c30")
    top = QColor("#4fc3f7") if enabled else QColor("#9e9e9e")
    return lerp(base, top, level / 20)


def text_for(bg: QColor) -> QColor:
    return QColor("#111") if bg.lightness() > 150 else QColor("#f0f0f0")


STYLESHEET = """
QWidget { background: #1e1f22; color: #d8d8d8; font-size: 9pt; }
QTableView { gridline-color: #33353a; selection-background-color: #455a64; alternate-background-color: #232428; }
QHeaderView::section { background: #2b2d31; color: #c0c0c0; border: 1px solid #33353a; padding: 2px 4px; }
QTabBar::tab { background: #2b2d31; padding: 5px 12px; border: 1px solid #33353a; }
QTabBar::tab:selected { background: #3a3d42; color: #ffd54f; }
QGroupBox { border: 1px solid #3a3d42; margin-top: 10px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 6px; padding: 0 3px; color: #ffd54f; }
QPushButton { background: #2f3237; border: 1px solid #4a4d52; padding: 4px 10px; }
QPushButton:hover { background: #3a3d42; }
QPushButton:disabled { color: #666; }
QPushButton#approve { background: #2e7d32; color: white; font-weight: bold; }
QPushButton#reject { background: #8e2424; color: white; }
QPushButton#approve:disabled, QPushButton#reject:disabled { background: #2f3237; color: #666; }
QPushButton:checkable:checked { background: #2e7d32; color: white; }
QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget, QTreeWidget, QComboBox {
    background: #17181a; border: 1px solid #3a3d42; }
QCheckBox::indicator { width: 30px; height: 14px; border-radius: 7px; background: #555; }
QCheckBox::indicator:checked { background: #43a047; }
QToolBar { border-bottom: 1px solid #33353a; spacing: 6px; }
QSplitter::handle { background: #33353a; }
"""
