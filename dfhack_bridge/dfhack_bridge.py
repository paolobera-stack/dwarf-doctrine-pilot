"""Real DFHack bridge — READ-ONLY (Step 2A).

Transport: `dfhack-run` (ships with DFHack, next to the game executable) runs
`lua/ddp_state.lua` inside the running game; the script writes one raw JSON
document to a temp file that `dfhack_state.raw_to_state` maps onto FortressState.

Write commands map onto native DFHack tools and are still disabled until the
read side is verified against a live game:

  create_work_order  -> `workorder '{"job":"BrewDrink","amount_total":10}'`
  enqueue_blueprint  -> `quickfort run <file> -n /<label> --cursor x,y,z`
  set_paused         -> lua `df.global.pause_state = true|false`
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from dfhack_bridge.base import BridgeError, FortressBridge
from dfhack_bridge.dfhack_state import RawStateError, raw_to_state
from dfhack_bridge.protocol import FortressState, Zone

LUA_DIR = Path(__file__).resolve().parent / "lua"
STATE_SCRIPT = LUA_DIR / "ddp_state.lua"
# Don't flash a console window every poll when the GUI runs without one (pythonw).
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_dfhack_run(explicit: str | None = None) -> str:
    """Explicit path > DDP_DFHACK_RUN > PATH > `dfhack-run` (resolved by the OS at call time)."""
    for cand in (explicit, os.environ.get("DDP_DFHACK_RUN")):
        if cand:
            return cand
    return shutil.which("dfhack-run") or shutil.which("dfhack-run.exe") or "dfhack-run"


class DFHackBridge(FortressBridge):
    name = "dfhack"
    read_only = True

    def __init__(self, dfhack_run: str | None = None, blueprints_dir: Path | None = None,
                 timeout: float = 10.0):
        self.dfhack_run = find_dfhack_run(dfhack_run)
        self.blueprints_dir = blueprints_dir or Path(__file__).resolve().parent.parent / "blueprints"
        self.timeout = timeout
        self.last_raw: dict[str, Any] | None = None
        self.last_elapsed_ms: int = 0

    # --- command builders (pure, testable) ------------------------------------
    def cmd_get_state(self, out_path: Path | None = None) -> list[str]:
        cmd = [self.dfhack_run, "lua", "-f", str(STATE_SCRIPT)]
        return cmd + [str(out_path)] if out_path else cmd

    def cmd_create_work_order(self, job: str, quantity: int) -> list[str]:
        return [self.dfhack_run, "workorder", json.dumps({"job": job, "amount_total": quantity})]

    def cmd_enqueue_blueprint(self, blueprint_file: str, label: str, zone: Zone) -> list[str]:
        return [
            self.dfhack_run, "quickfort", "run", str(self.blueprints_dir / blueprint_file),
            "-n", f"/{label}", "--cursor", f"{zone.x},{zone.y},{zone.z}",
        ]

    def cmd_set_paused(self, paused: bool) -> list[str]:
        return [self.dfhack_run, "lua", f"df.global.pause_state={'true' if paused else 'false'}"]

    # --- reading --------------------------------------------------------------
    def read_raw(self) -> dict[str, Any]:
        """Run the exporter and return its raw JSON document."""
        fd, name = tempfile.mkstemp(prefix="ddp_state_", suffix=".json")
        os.close(fd)
        out = Path(name)
        out.unlink()
        started = time.monotonic()
        try:
            try:
                proc = subprocess.run(self.cmd_get_state(out), capture_output=True, text=True,
                                      timeout=self.timeout, errors="replace",
                                      creationflags=NO_WINDOW)
            except FileNotFoundError as e:
                raise BridgeError(f"dfhack-run not found ({self.dfhack_run}); set DDP_DFHACK_RUN") from e
            except subprocess.TimeoutExpired as e:
                raise BridgeError(f"dfhack-run timed out after {self.timeout}s") from e
            console = (proc.stdout + proc.stderr).strip()
            if proc.returncode != 0 or not out.exists():
                raise BridgeError(f"ddp_state.lua failed (exit {proc.returncode}): {console[-500:] or 'no output'}")
            raw = json.loads(out.read_text(encoding="utf-8"))
        finally:
            out.unlink(missing_ok=True)
        self.last_elapsed_ms = round((time.monotonic() - started) * 1000)
        self.last_raw = raw
        return raw

    def get_state(self) -> FortressState:
        raw = self.read_raw()
        try:
            return raw_to_state(raw)
        except RawStateError as e:
            raise BridgeError(str(e)) from e

    # --- writing (disabled) ---------------------------------------------------
    def _read_only(self) -> BridgeError:
        return BridgeError("DFHack bridge is read-only for now: actions are planned (dry-run) but not executed")

    def create_work_order(self, job: str, quantity: int, *, source: str = "autopilot") -> int:
        raise self._read_only()

    def cancel_work_order(self, order_id: int) -> None:
        raise self._read_only()

    def expand_stockpile(self, stockpile_id: int, cells: int) -> None:
        raise self._read_only()

    def link_stockpile(self, stockpile_id: int, workshop: str) -> None:
        raise self._read_only()

    def enqueue_blueprint(self, queue_id: str, blueprint: str, zone_id: str) -> None:
        raise self._read_only()

    def set_paused(self, paused: bool) -> None:
        raise self._read_only()
