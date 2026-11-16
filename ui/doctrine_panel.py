"""Side panel "Dottrina & Chat": directives + module switches, Gemini chat, proposal diff, live log."""

from __future__ import annotations

import html
import json
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.doctrine import AutomationModules, DiffEntry, FortressDoctrine
from core.i18n import tr

MODULES = ("production", "stockpiles", "blueprints", "emergency")
_DIFF_COLORS = {"added": "#2e7d32", "removed": "#8e2424", "changed": "#8a6d00"}


def _short(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):
        return ", ".join(f"{k}={_short(x)}" for k, x in v.items() if x not in (None, [], {}, ""))
    if isinstance(v, list):
        return "[" + ", ".join(_short(x) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False) if isinstance(v, str) else str(v)


class DoctrinePanel(QWidget):
    modules_changed = Signal(object)  # AutomationModules
    message_sent = Signal(str)
    approved = Signal()
    rejected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doctrine: FortressDoctrine | None = None
        self._proposal: tuple[list[DiffEntry], int, int] | None = None
        self._chat: list[tuple[str, str, str]] = []  # (role, text, css colour)

        # --- top: directives + switches
        self.directives_box = QGroupBox()
        self.modules_label = QLabel()
        self.switches: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        for i, m in enumerate(MODULES):
            cb = QCheckBox()
            cb.setObjectName(f"module_{m}")
            cb.toggled.connect(self._on_switch)
            self.switches[m] = cb
            grid.addWidget(cb, i // 2, i % 2)
        self.directives = QTreeWidget()
        self.directives.setHeaderHidden(True)
        self.directives.setIndentation(12)
        top = QVBoxLayout(self.directives_box)
        top.addWidget(self.modules_label)
        top.addLayout(grid)
        top.addWidget(self.directives)

        # --- middle: chat
        self.chat_box = QGroupBox()
        self.chat_view = QTextBrowser()
        self.chat_view.setOpenExternalLinks(False)
        self.chat_input = QLineEdit()
        self.chat_input.setObjectName("chat_input")
        self.chat_input.returnPressed.connect(self._send)
        self.send_btn = QPushButton()
        self.send_btn.setObjectName("chat_send")
        self.send_btn.clicked.connect(self._send)
        self.chat_status = QLabel()
        self.chat_status.setStyleSheet("color: #8a8d93;")
        self.chat_status.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.chat_input)
        row.addWidget(self.send_btn)
        mid = QVBoxLayout(self.chat_box)
        mid.addWidget(self.chat_view)
        mid.addLayout(row)
        mid.addWidget(self.chat_status)

        # --- bottom: proposal + log
        self.proposal_box = QGroupBox()
        self.proposal_summary = QLabel()
        self.diff = QTreeWidget()
        self.diff.setColumnCount(3)
        self.diff.setRootIsDecorated(False)
        self.diff.setObjectName("proposal_diff")
        self.approve_btn = QPushButton()
        self.approve_btn.setObjectName("approve")
        self.approve_btn.clicked.connect(self.approved)
        self.reject_btn = QPushButton()
        self.reject_btn.setObjectName("reject")
        self.reject_btn.clicked.connect(self.rejected)
        btns = QHBoxLayout()
        btns.addWidget(self.approve_btn, 2)
        btns.addWidget(self.reject_btn, 1)
        prop = QVBoxLayout(self.proposal_box)
        prop.addWidget(self.proposal_summary)
        prop.addWidget(self.diff)
        prop.addLayout(btns)

        self.log_box = QGroupBox()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        self.log.setObjectName("autopilot_log")
        self.log.setStyleSheet("font-family: monospace; font-size: 8pt;")
        QVBoxLayout(self.log_box).addWidget(self.log)

        split = QSplitter(Qt.Orientation.Vertical)
        for w in (self.directives_box, self.chat_box, self.proposal_box, self.log_box):
            split.addWidget(w)
        split.setSizes([230, 260, 200, 200])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 11pt; font-weight: bold; color: #ffd54f;")
        lay.addWidget(self.title)
        lay.addWidget(split)
        self.clear_proposal()
        self.retranslate()

    # ------------------------------------------------------------------ doctrine
    def set_doctrine(self, d: FortressDoctrine) -> None:
        self._doctrine = d
        for m, cb in self.switches.items():
            cb.blockSignals(True)
            cb.setChecked(getattr(d.modules, m))
            cb.blockSignals(False)
        self._render_directives()

    def _render_directives(self) -> None:
        d = self._doctrine
        if d is None:
            return
        self.directives_box.setTitle(tr("panel.directives", version=d.version, name=d.name))
        self.directives.clear()

        def group(key: str, lines: list[tuple[str, bool]]) -> None:
            g = QTreeWidgetItem([f"{tr(key)} ({len(lines)})"])
            g.setForeground(0, QBrush(QColor("#ffd54f")))
            for text, enabled in lines:
                it = QTreeWidgetItem([text])
                if not enabled:
                    it.setForeground(0, QBrush(QColor("#666")))
                g.addChild(it)
            self.directives.addTopLevelItem(g)
            g.setExpanded(True)

        group("directive.group.stocks", [
            (tr("directive.stock", item=t.item, min=t.min, target=t.target, max=t.max), t.enabled)
            for t in d.stock_targets])
        group("directive.group.stockpiles", [
            (tr("directive.stockpile", category=r.category, pct=r.max_fill_pct), r.enabled) for r in d.stockpile_rules])
        group("directive.group.blueprints", [
            (tr("directive.blueprint", id=b.id, zone=b.zone_id, priority=b.priority), True)
            for b in sorted(d.blueprint_queue, key=lambda b: -b.priority)])
        a = d.alarms
        group("directive.group.alarms", [
            (tr("directive.alarms", food=a.food_days_critical, drink=a.drink_days_critical, stress=a.stress_critical),
             True)])

    def _on_switch(self) -> None:
        self.modules_changed.emit(AutomationModules(**{m: cb.isChecked() for m, cb in self.switches.items()}))

    # ------------------------------------------------------------------ chat
    def _send(self) -> None:
        text = self.chat_input.text().strip()
        if text and self.send_btn.isEnabled():
            self.chat_input.clear()
            self.message_sent.emit(text)

    def append_chat(self, role: str, text: str) -> None:
        colors = {"user": "#90caf9", "model": "#a5d6a7", "system": "#ffcc80"}
        self._chat.append((role, text, colors.get(role, "#ddd")))
        self._render_chat()

    def _render_chat(self) -> None:
        parts = []
        for role, text, color in self._chat:
            who = {"user": tr("chat.you"), "model": "Gemini", "system": "•"}.get(role, role)
            body = html.escape(text).replace("\n", "<br>")
            parts.append(f'<p><b style="color:{color}">{who}:</b> {body}</p>')
        self.chat_view.setHtml("".join(parts))
        self.chat_view.verticalScrollBar().setValue(self.chat_view.verticalScrollBar().maximum())

    def set_busy(self, busy: bool) -> None:
        self.send_btn.setEnabled(not busy)
        self.chat_input.setEnabled(not busy)
        if busy:
            self.set_status("chat.thinking")

    def set_status(self, key: str | None, **kwargs) -> None:
        """Status is kept as an i18n key so a language switch re-renders it."""
        self._status = (key, kwargs) if key else None
        self.chat_status.setText(tr(key, **kwargs) if key else "")

    # ------------------------------------------------------------------ proposal
    def show_proposal(self, entries: list[DiffEntry], old_version: int, new_version: int) -> None:
        self._proposal = (entries, old_version, new_version)
        self._render_proposal()

    def clear_proposal(self) -> None:
        self._proposal = None
        self._render_proposal()

    def has_proposal(self) -> bool:
        return self._proposal is not None

    def _render_proposal(self) -> None:
        self.diff.clear()
        pending = self._proposal is not None
        self.approve_btn.setEnabled(pending)
        self.reject_btn.setEnabled(pending)
        if not pending:
            self.proposal_summary.setText(tr("proposal.none"))
            return
        entries, old_v, new_v = self._proposal
        self.proposal_summary.setText(tr("proposal.summary", old=old_v, new=new_v, n=len(entries)))
        for e in entries:
            if e.kind == "changed":
                cols = [e.path, _short(e.old), "→ " + _short(e.new)]
            elif e.kind == "added":
                cols = [f"+ {e.path}", tr("diff.added"), _short(e.new)]
            else:
                cols = [f"− {e.path}", tr("diff.removed"), _short(e.old)]
            it = QTreeWidgetItem(cols)
            for c in range(3):
                it.setForeground(c, QBrush(QColor("#f0f0f0")))
                it.setToolTip(c, " ".join(cols))
            it.setBackground(0, QBrush(QColor(_DIFF_COLORS[e.kind])))
            self.diff.addTopLevelItem(it)
        for c in range(3):
            self.diff.resizeColumnToContents(c)

    # ------------------------------------------------------------------ log
    def append_log(self, text: str, level: str = "info") -> None:
        color = {"ok": "#a5d6a7", "dry_run": "#90caf9", "failed": "#ef9a9a", "critical": "#ff8a80",
                 "info": "#bdbdbd", "user": "#ffcc80"}.get(level, "#bdbdbd")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.appendHtml(f'<span style="color:#777">{ts}</span> '
                            f'<span style="color:{color}">{html.escape(text)}</span>')

    # ------------------------------------------------------------------ i18n
    def retranslate(self) -> None:
        if getattr(self, "_status", None):
            self.set_status(self._status[0], **self._status[1])
        self.title.setText(tr("panel.title"))
        self.modules_label.setText(tr("panel.modules"))
        for m, cb in self.switches.items():
            cb.setText(tr(f"module.{m}"))
        self.chat_box.setTitle(tr("chat.title"))
        self.chat_input.setPlaceholderText(tr("chat.placeholder"))
        self.send_btn.setText(tr("chat.send"))
        self.proposal_box.setTitle(tr("proposal.title"))
        self.diff.setHeaderLabels(["", "", ""])
        self.approve_btn.setText(tr("proposal.approve"))
        self.reject_btn.setText(tr("proposal.reject"))
        self.log_box.setTitle(tr("log.title"))
        self._render_directives()
        self._render_proposal()
        self._render_chat()
