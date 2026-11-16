"""Main window: four Therapist-style tabs + "Dottrina & Chat" side panel."""

from __future__ import annotations

import os

from pydantic import ValidationError
from PySide6.QtCore import QThread, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.advisor import AdvisorResult, ChatTurn, OfflineAdvisor
from core.autopilot import Autopilot, TickResult
from core.doctrine import AutomationModules, DoctrineStore, FortressDoctrine, apply_patch, diff_doctrines
from core.i18n import LANGUAGES, language, off_language_changed, on_language_changed, set_language, tr
from dfhack_bridge.base import BridgeError, FortressBridge
from dfhack_bridge.mock_server import EVENT_TEXT
from dfhack_bridge.protocol import FortressState
from ui import models
from ui.doctrine_panel import DoctrinePanel
from ui.widgets import DenseTable
from ui.workers import AdvisorThread, AutopilotWorker


class MainWindow(QMainWindow):
    def __init__(self, bridge: FortressBridge, store: DoctrineStore, advisor, *, dry_run: bool = False,
                 autostart: bool = True):
        super().__init__()
        self.bridge = bridge
        self.store = store
        self.advisor = advisor
        self.doctrine: FortressDoctrine = store.load()
        self.state: FortressState | None = None
        self.history: list[ChatTurn] = []
        self.candidate: FortressDoctrine | None = None
        self._advisor_thread: AdvisorThread | None = None
        self._alert: QMessageBox | None = None
        self.resize(1600, 950)

        self._build_toolbar(dry_run)
        self._build_tabs()
        self.panel = DoctrinePanel()
        self.panel.set_doctrine(self.doctrine)
        self.panel.modules_changed.connect(self._on_modules)
        self.panel.message_sent.connect(self.send_chat)
        self.panel.approved.connect(self.approve_proposal)
        self.panel.rejected.connect(self.reject_proposal)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.tabs)
        split.addWidget(self.panel)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([980, 620])
        self.setCentralWidget(split)

        self.status_conn = QLabel()
        self.status_date = QLabel()
        self.status_alarms = QLabel()
        for w in (self.status_conn, self.status_date):
            self.statusBar().addWidget(w)
        self.statusBar().addPermanentWidget(self.status_alarms)

        # deterministic loop in its own thread
        self.worker = AutopilotWorker(Autopilot(bridge, self.doctrine, dry_run=dry_run))
        self.thread_ = QThread(self)
        self.worker.moveToThread(self.thread_)
        self.thread_.started.connect(self.worker.start)
        self.worker.tick.connect(self.on_tick)
        self.worker.failed.connect(self.on_bridge_error)

        self._lang_cb = lambda _l: self.retranslate()
        on_language_changed(self._lang_cb)
        self.retranslate()
        if isinstance(advisor, OfflineAdvisor):
            self.panel.append_chat("system", tr("chat.offline"))
        self.panel.append_log(tr("log.started", bridge=bridge.name, version=self.doctrine.version,
                                 s=self.doctrine.limits.tick_seconds), "info")
        if autostart:
            self.thread_.start()

    # ------------------------------------------------------------------ layout
    def _build_toolbar(self, dry_run: bool) -> None:
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        self.autopilot_btn = QPushButton()
        self.autopilot_btn.setObjectName("autopilot_toggle")
        self.autopilot_btn.setCheckable(True)
        self.autopilot_btn.setChecked(True)
        self.autopilot_btn.toggled.connect(self._on_autopilot_toggled)
        self.dry_btn = QPushButton()
        self.dry_btn.setCheckable(True)
        self.dry_btn.setChecked(dry_run)
        self.dry_btn.toggled.connect(lambda v: self.worker.set_flags(dry_run=v))
        self.pause_btn = QPushButton()
        self.pause_btn.setObjectName("pause_toggle")
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.analyze_btn = QPushButton()
        self.analyze_btn.clicked.connect(lambda: self.send_chat(tr("chat.analysis_prompt"), analysis=True))
        for w in (self.autopilot_btn, self.dry_btn, self.pause_btn, self.analyze_btn):
            tb.addWidget(w)
        tb.addSeparator()
        self.inject = QComboBox()
        self.inject.activated.connect(self._inject_event)
        if hasattr(self.bridge, "inject_event"):
            tb.addWidget(self.inject)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding,
                             spacer.sizePolicy().verticalPolicy().Preferred)
        tb.addWidget(spacer)
        self.lang_label = QLabel()
        self.lang = QComboBox()
        for code, name in LANGUAGES.items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(list(LANGUAGES).index(language()))
        self.lang.currentIndexChanged.connect(lambda i: set_language(self.lang.itemData(i)))
        tb.addWidget(self.lang_label)
        tb.addWidget(self.lang)

    def _build_tabs(self) -> None:
        self.stock_table = DenseTable(models.StockModel(), "section.stocks")
        self.order_table = DenseTable(models.OrderModel(), "section.orders")
        self.pile_table = DenseTable(models.StockpileModel(), "section.stockpiles")
        self.zone_table = DenseTable(models.ZoneModel(), "section.zones")
        self.bp_table = DenseTable(models.BlueprintModel(), "section.queue")
        self.dwarf_table = DenseTable(models.DwarfModel())
        self.dwarf_table.view.horizontalHeader().setStretchLastSection(False)
        self.logistics_summary = QLabel()
        self.logistics_summary.setStyleSheet("padding: 4px; font-weight: bold;")

        def vsplit(*widgets) -> QSplitter:
            s = QSplitter(Qt.Orientation.Vertical)
            for w in widgets:
                s.addWidget(w)
            return s

        logistics = QWidget()
        lay = QVBoxLayout(logistics)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.logistics_summary)
        lay.addWidget(self.pile_table)

        self.tabs = QTabWidget()
        self.tabs.addTab(vsplit(self.stock_table, self.order_table), "")
        self.tabs.addTab(logistics, "")
        self.tabs.addTab(vsplit(self.bp_table, self.zone_table), "")
        self.tabs.addTab(self.dwarf_table, "")
        self.tables = [self.stock_table, self.order_table, self.pile_table, self.zone_table, self.bp_table,
                       self.dwarf_table]

    # ------------------------------------------------------------------ autopilot ticks
    def on_tick(self, result: TickResult) -> None:
        self.state = result.state
        self.refresh_tables()
        self._update_status(result)
        for a in result.actions:
            level = "critical" if a.module == "emergency" and a.status == "ok" else a.status
            self.panel.append_log(a.render(), level)
        crit = [a for a in result.notifications if a.level == "critical"]
        if crit:
            self.show_alert("; ".join(a.message() for a in crit), paused=result.paused_by_autopilot)

    def on_bridge_error(self, error: str) -> None:
        self.status_conn.setText(tr("status.disconnected", error=error))
        self.status_conn.setStyleSheet("color: #ef5350;")

    def refresh_tables(self) -> None:
        s, d = self.state, self.doctrine
        if s is None:
            return
        self.stock_table.set_rows(models.stock_rows(s, d))
        self.order_table.set_rows(sorted(s.work_orders, key=lambda o: (o.status in ("done", "cancelled"), -o.id)))
        self.pile_table.set_rows(models.stockpile_rows(s, d))
        self.zone_table.set_rows(list(s.zones))
        self.bp_table.set_rows(models.blueprint_rows(s, d))
        self.dwarf_table.set_rows(list(s.dwarves))
        fills = [p.fill_pct for p in s.stockpiles] or [0]
        self.logistics_summary.setText(tr("logistics.summary", ground=s.total_ground_items(),
                                          avg=round(sum(fills) / len(fills)),
                                          links=sum(len(p.links) for p in s.stockpiles)))

    def _update_status(self, result: TickResult | None = None) -> None:
        s = self.state
        self.status_conn.setText(tr("status.connected", bridge=self.bridge.name))
        self.status_conn.setStyleSheet("color: #81c784;")
        if s is not None:
            self.status_date.setText(tr("status.date", year=s.date.year, season=tr(f"season.{s.date.season}"),
                                        day=s.date.day_of_season, pop=s.population,
                                        paused=tr("status.paused") if s.paused else tr("status.running")))
            self.status_date.setStyleSheet("color: #ff8a80; font-weight: bold;" if s.paused else "")
            self.pause_btn.setText(tr("toolbar.resume_game") if s.paused else tr("toolbar.pause_game"))
        if result is not None:
            alarms = result.alarms
            self.status_alarms.setText("  ".join(f"[{tr('level.' + a.level)}] {a.message()}" for a in alarms))
            self.status_alarms.setStyleSheet(
                "color: #ff8a80;" if any(a.level == "critical" for a in alarms) else "color: #ffd54f;")

    # ------------------------------------------------------------------ alerts (pause + notify; no auto-Gemini)
    def show_alert(self, message: str, paused: bool) -> None:
        body = tr("alert.body", message=message)
        if self._alert is not None and self._alert.isVisible():
            self._alert.setText(body)
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(tr("alert.title"))
        box.setText(body)
        analyze = box.addButton(tr("alert.analyze"), QMessageBox.ButtonRole.ActionRole)
        resume = box.addButton(tr("alert.resume"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("alert.dismiss"), QMessageBox.ButtonRole.RejectRole)
        box.setWindowModality(Qt.WindowModality.NonModal)

        def done() -> None:
            clicked = box.clickedButton()
            if clicked is analyze:  # explicit user click: the only way an alarm reaches Gemini
                self.send_chat(tr("alert.analysis_prompt", message=message), analysis=True)
            elif clicked is resume:
                self._set_paused(False)

        box.finished.connect(done)
        self._alert = box
        box.show()

    # ------------------------------------------------------------------ toolbar actions
    def _on_autopilot_toggled(self, on: bool) -> None:
        self.worker.set_flags(enabled=on)
        self.autopilot_btn.setText(tr("toolbar.autopilot_on") if on else tr("toolbar.autopilot_off"))
        self.panel.append_log(tr("log.autopilot", state="ON" if on else "OFF"), "user")

    def _set_paused(self, paused: bool) -> None:
        try:
            self.bridge.set_paused(paused)
        except (BridgeError, OSError) as exc:
            self.on_bridge_error(str(exc))
            return
        self.panel.append_log(tr("log.game_paused") if paused else tr("log.game_resumed"), "user")
        QTimer.singleShot(0, self.worker, self.worker.run_once)

    def _toggle_pause(self) -> None:
        self._set_paused(not (self.state.paused if self.state else False))

    def _inject_event(self, index: int) -> None:
        kind = self.inject.itemData(index)
        if not kind:
            return
        self.inject.setCurrentIndex(0)
        try:
            self.bridge.inject_event(kind)
        except (BridgeError, OSError) as exc:
            self.on_bridge_error(str(exc))
            return
        self.panel.append_log(tr("log.injected", kind=tr(f"event.{kind}")), "user")
        QTimer.singleShot(0, self.worker, self.worker.run_once)

    # ------------------------------------------------------------------ doctrine
    def _on_modules(self, modules: AutomationModules) -> None:
        self.doctrine = self.doctrine.model_copy(update={"modules": modules})
        self.store.save(self.doctrine, archive=False)
        self.worker.set_doctrine(self.doctrine)
        on = [tr(f"module.{m}") for m, v in modules.model_dump().items() if v]
        self.panel.append_log(tr("log.modules", modules=", ".join(on) or "—"), "user")

    def send_chat(self, text: str, analysis: bool = False) -> None:
        if self._advisor_thread is not None and self._advisor_thread.isRunning():
            return
        self.panel.append_chat("user", text)
        self.panel.set_busy(True)
        t = AdvisorThread(self.advisor, self.history, text, self.doctrine, self.state, analysis)
        t.done.connect(lambda r, text=text: self._on_reply(text, r))
        t.rate_limited.connect(self._on_rate_limited)
        t.error.connect(self._on_advisor_error)
        t.finished.connect(lambda: self.panel.set_busy(False))
        self._advisor_thread = t
        t.start()

    def _on_reply(self, user_text: str, result: AdvisorResult) -> None:
        reply = result.reply
        self.history += [ChatTurn("user", user_text), ChatTurn("model", reply.reply)]
        self.panel.append_chat("model", reply.reply)
        b = self.doctrine.gemini
        status = tr("chat.budget", tokens=result.payload_tokens, max=b.max_payload_tokens,
                    day=self.advisor.budget.used_today(), day_max=b.max_requests_per_day)
        self.panel.set_status(status)
        if reply.proposal is None or reply.proposal.is_empty():
            return
        try:
            candidate = apply_patch(self.doctrine, reply.proposal)
        except ValidationError as exc:
            self.panel.append_chat("system", tr("proposal.invalid", error=str(exc.errors()[0]["msg"])))
            return
        entries = diff_doctrines(self.doctrine, candidate)
        if not entries:
            self.panel.append_chat("system", tr("proposal.empty"))
            return
        self.candidate = candidate
        self.panel.show_proposal(entries, self.doctrine.version, self.doctrine.version + 1)

    def _on_rate_limited(self, seconds: int) -> None:
        self.panel.append_chat("system", tr("chat.rate_limited", s=seconds))

    def _on_advisor_error(self, error: str) -> None:
        self.panel.append_chat("system", tr("chat.error", error=error))

    def approve_proposal(self) -> None:
        if self.candidate is None:
            return
        # Switches and limits stay user-owned: keep the current ones, take everything else from the proposal.
        merged = self.candidate.model_copy(update={
            "modules": self.doctrine.modules, "limits": self.doctrine.limits, "gemini": self.doctrine.gemini,
            "version": self.doctrine.version})
        self.doctrine = merged.approved()
        self.store.save(self.doctrine)
        self.worker.set_doctrine(self.doctrine)
        self.candidate = None
        self.panel.clear_proposal()
        self.panel.set_doctrine(self.doctrine)
        msg = tr("proposal.approved", version=self.doctrine.version)
        self.panel.append_chat("system", msg)
        self.panel.append_log(msg, "user")
        self.refresh_tables()

    def reject_proposal(self) -> None:
        self.candidate = None
        self.panel.clear_proposal()
        self.panel.append_chat("system", tr("proposal.rejected"))

    # ------------------------------------------------------------------ i18n / lifecycle
    def retranslate(self) -> None:
        self.setWindowTitle(tr("app.title"))
        for i, key in enumerate(("tab.production", "tab.stockpiles", "tab.blueprints", "tab.dwarves")):
            self.tabs.setTabText(i, tr(key).replace("&", "&&"))
        for t in self.tables:
            t.retranslate()
        self.autopilot_btn.setText(tr("toolbar.autopilot_on") if self.autopilot_btn.isChecked()
                                   else tr("toolbar.autopilot_off"))
        self.dry_btn.setText(tr("toolbar.dry_run"))
        self.analyze_btn.setText(tr("toolbar.analyze"))
        self.lang_label.setText(tr("toolbar.language") + ":")
        self.inject.clear()
        self.inject.addItem(tr("toolbar.inject"), None)
        for kind in EVENT_TEXT:
            self.inject.addItem(tr(f"event.{kind}"), kind)
        self.panel.retranslate()
        self.refresh_tables()
        self._update_status()

    def closeEvent(self, event) -> None:
        off_language_changed(self._lang_cb)
        QTimer.singleShot(0, self.worker, self.worker.stop)
        self.thread_.quit()
        busy = not self.thread_.wait(5000)
        if self._advisor_thread is not None and self._advisor_thread.isRunning():
            busy = True  # a blocking Gemini call cannot be interrupted
        if busy:
            # Never destroy a running QThread (Qt aborts): exit the process once Qt returns.
            QApplication.instance().aboutToQuit.connect(lambda: os._exit(0))
        super().closeEvent(event)
