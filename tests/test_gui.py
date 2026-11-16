import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from core import i18n  # noqa: E402
from core.advisor import OfflineAdvisor  # noqa: E402
from core.doctrine import DoctrineStore  # noqa: E402
from dfhack_bridge.mock_bridge import LocalMockBridge  # noqa: E402
from dfhack_bridge.mock_server import FortressSim  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

BPS = ["bedrooms_3x3", "workshops_core", "dining_hall", "stockpiles_core"]


@pytest.fixture
def window(qtbot, tmp_path):
    sim = FortressSim(seed=7, random_events=False)
    bridge = LocalMockBridge(sim)
    store = DoctrineStore(tmp_path)
    win = MainWindow(bridge, store, OfflineAdvisor(blueprints=BPS), autostart=False)
    qtbot.addWidget(win)
    win.show()
    return win, sim, bridge, store


def test_tick_fills_all_four_tabs(window):
    win, sim, *_ = window
    win.worker.run_once()
    assert win.tabs.count() == 4
    assert win.tabs.tabText(0).replace("&&", "&") == "Produzione & Work Orders"
    assert win.stock_table.model.rowCount() >= len(win.doctrine.stock_targets)
    assert win.order_table.model.rowCount() > 0  # autopilot created orders
    assert win.pile_table.model.rowCount() == len(sim.stockpiles)
    assert win.bp_table.model.rowCount() == len(win.doctrine.blueprint_queue)
    assert win.dwarf_table.model.rowCount() == len(sim.dwarves)
    assert win.panel.log.toPlainText().count("Ordine") >= 1


def test_module_switch_persists(window):
    win, _, _, store = window
    win.panel.switches["blueprints"].setChecked(True)
    assert win.doctrine.modules.blueprints and store.load().modules.blueprints
    assert win.worker.autopilot.doctrine.modules.blueprints


def test_chat_proposal_approve_flow(window, qtbot):
    win, _, _, store = window
    win.worker.run_once()
    v0 = win.doctrine.version
    win.send_chat("Voglio più birra per l'inverno")
    qtbot.waitUntil(lambda: win.panel.has_proposal(), timeout=5000)
    assert win.panel.approve_btn.isEnabled() and win.panel.diff.topLevelItemCount() > 0
    old_target = win.doctrine.target_for("drink").target
    win.panel.approve_btn.click()
    assert win.doctrine.version == v0 + 1
    assert win.doctrine.target_for("drink").target > old_target
    assert store.load().version == v0 + 1
    assert win.worker.autopilot.doctrine == win.doctrine
    assert not win.panel.has_proposal()


def test_chat_proposal_reject_flow(window, qtbot):
    win, *_ = window
    win.worker.run_once()
    before = win.doctrine
    win.send_chat("più cibo")
    qtbot.waitUntil(lambda: win.panel.has_proposal(), timeout=5000)
    win.panel.reject_btn.click()
    assert win.doctrine == before and not win.panel.has_proposal()


def test_critical_alarm_pauses_and_notifies(window):
    win, _, bridge, _ = window
    bridge.inject_event("siege")
    win.worker.run_once()
    assert bridge.get_state().paused
    assert isinstance(win._alert, QMessageBox) and win._alert.isVisible()
    assert win._advisor_thread is None  # Gemini was NOT called automatically
    win._alert.close()


def test_english_switch(window):
    win, *_ = window
    win.worker.run_once()
    i18n.set_language("en")
    assert win.tabs.tabText(3).replace("&&", "&") == "Dwarves & Roles"
    assert win.panel.approve_btn.text() == "Approve & Automate"
    assert win.stock_table.model.headerData(1, __import__("PySide6.QtCore").QtCore.Qt.Orientation.Horizontal) \
        == "Current"


def test_chat_status_retranslates(qtbot):
    from ui.doctrine_panel import DoctrinePanel

    panel = DoctrinePanel()
    qtbot.addWidget(panel)
    panel.set_status("chat.budget", tokens=10, max=2000, day=1, day_max=100)
    it = panel.chat_status.text()
    i18n.set_language("en")
    panel.retranslate()
    assert panel.chat_status.text() != it
