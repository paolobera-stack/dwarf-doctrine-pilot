"""Background workers: the deterministic autopilot loop and one-shot Gemini requests."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from core.advisor import BudgetExceeded, ChatTurn
from core.autopilot import Autopilot, TickResult
from core.doctrine import FortressDoctrine
from dfhack_bridge.base import BridgeError


class AutopilotWorker(QObject):
    """Runs `Autopilot.run_tick` every `tick_seconds` in its own thread (0 tokens)."""

    tick = Signal(object)  # TickResult
    failed = Signal(str)

    def __init__(self, autopilot: Autopilot):
        super().__init__()
        self.autopilot = autopilot
        self._lock = threading.Lock()
        self._timer: QTimer | None = None

    @Slot()
    def start(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.run_once)
        self._apply_interval()
        self.run_once()

    def _apply_interval(self) -> None:
        if self._timer is not None:
            self._timer.start(int(self.autopilot.doctrine.limits.tick_seconds * 1000))

    @Slot()
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    @Slot()
    def run_once(self) -> None:
        with self._lock:
            try:
                result: TickResult = self.autopilot.run_tick()
                if any(a.status == "ok" for a in result.actions):
                    result.state = self.autopilot.bridge.get_state()  # show the effect immediately
            except (BridgeError, OSError) as exc:
                self.failed.emit(str(exc))
                return
        self.tick.emit(result)

    # Called from the GUI thread; guarded by the lock so a tick never sees half an update.
    def set_doctrine(self, doctrine: FortressDoctrine) -> None:
        with self._lock:
            self.autopilot.set_doctrine(doctrine)
        QTimer.singleShot(0, self, self._apply_interval)

    def set_flags(self, *, enabled: bool | None = None, dry_run: bool | None = None) -> None:
        with self._lock:
            if enabled is not None:
                self.autopilot.enabled = enabled
            if dry_run is not None:
                self.autopilot.dry_run = dry_run
                self.autopilot._last_dry.clear()


class AdvisorThread(QThread):
    """One Gemini (or offline) request; never started automatically by the autopilot."""

    done = Signal(object)  # AdvisorResult
    rate_limited = Signal(int)
    error = Signal(str)

    def __init__(self, advisor, history: list[ChatTurn], message: str, doctrine, state, analysis: bool):
        super().__init__()
        self.args = (advisor, list(history), message, doctrine, state, analysis)

    def run(self) -> None:
        advisor, history, message, doctrine, state, analysis = self.args
        try:
            self.done.emit(advisor.ask(history, message, doctrine, state, analysis=analysis))
        except BudgetExceeded as exc:
            self.rate_limited.emit(exc.retry_in_s)
        except Exception as exc:  # network, quota, schema...
            self.error.emit(f"{type(exc).__name__}: {exc}"[:300])
