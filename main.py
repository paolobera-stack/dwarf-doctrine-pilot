"""Dwarf Doctrine Pilot — entry point.

    python main.py                  # GUI + embedded mock fortress (if no mock server is running)
    python main.py --lang en        # English UI
    python main.py --dry-run        # plan actions without executing them
    DDP_BRIDGE=dfhack python main.py  # DFHack bridge (stub in Step 1)
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys

from core.advisor import make_advisor
from core.config import AppConfig
from core.doctrine import DoctrineStore
from core.i18n import set_language


def _mock_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def build_bridge(cfg: AppConfig, embedded: bool, seed: int, speed: float):
    """Return (bridge, server_or_None)."""
    if cfg.bridge == "dfhack":
        from dfhack_bridge.dfhack_bridge import DFHackBridge

        return DFHackBridge(blueprints_dir=cfg.blueprints_dir), None
    from dfhack_bridge.mock_bridge import MockBridge
    from dfhack_bridge.mock_server import FortressSim, MockServer

    if not embedded and _mock_reachable(cfg.mock_host, cfg.mock_port):
        return MockBridge(cfg.mock_host, cfg.mock_port), None
    sim = FortressSim(seed=seed)
    sim.days_per_second = speed
    server = MockServer(sim, cfg.mock_host, 0)
    server.start()
    return MockBridge(cfg.mock_host, server.server_address[1]), server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Dwarf Fortress doctrine co-pilot")
    p.add_argument("--lang", choices=["it", "en"])
    p.add_argument("--embedded", action="store_true", help="always start an in-process mock fortress")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--speed", type=float, default=0.5, help="embedded mock: game days per second")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--screenshot", help="save a window screenshot after --screenshot-delay seconds and exit")
    p.add_argument("--screenshot-delay", type=float, default=6.0)
    p.add_argument("--tab", type=int, default=0, help="initial tab index")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    cfg = AppConfig()
    set_language(args.lang or cfg.language)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from ui.main_window import MainWindow
    from ui.theme import STYLESHEET

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)

    bridge, server = build_bridge(cfg, args.embedded, args.seed, args.speed)
    catalog = json.loads((cfg.blueprints_dir / "catalog.json").read_text(encoding="utf-8"))
    advisor = make_advisor(cfg.gemini_api_key, cfg.gemini_model, list(catalog))
    win = MainWindow(bridge, DoctrineStore(cfg.data_dir), advisor, dry_run=args.dry_run)
    win.tabs.setCurrentIndex(args.tab)
    win.show()

    if args.screenshot:
        def shoot() -> None:
            win.grab().save(args.screenshot)
            app.quit()

        QTimer.singleShot(int(args.screenshot_delay * 1000), shoot)
    code = app.exec()
    if server is not None:
        server.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
