"""Real DFHack bridge — STUB (Step 1).

Planned transport: `dfhack-run` (ships with DFHack) executes the Lua scripts in
`dfhack_bridge/lua/` inside the running game; they print one JSON document on
stdout. Commands map onto native DFHack tools:

  create_work_order  -> `workorder '{"job":"BrewDrink","amount_total":10}'`
  enqueue_blueprint  -> `quickfort run <file> -n /<label> --cursor x,y,z`
  set_paused         -> lua `df.global.pause_state = true|false`
  expand / link      -> lua helpers in ddp_actions.lua (df.building_stockpilest)

The command builders below are real and unit-tested; execution is disabled
until the Lua side is written and verified against a live game.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from dfhack_bridge.base import BridgeError, FortressBridge
from dfhack_bridge.protocol import FortressState, Zone

LUA_DIR = Path(__file__).resolve().parent / "lua"


class DFHackBridge(FortressBridge):
    name = "dfhack"

    def __init__(self, dfhack_run: str | None = None, blueprints_dir: Path | None = None):
        self.dfhack_run = dfhack_run or shutil.which("dfhack-run") or "dfhack-run"
        self.blueprints_dir = blueprints_dir or Path(__file__).resolve().parent.parent / "blueprints"

    # --- command builders (pure, testable) ------------------------------------
    def cmd_get_state(self) -> list[str]:
        return [self.dfhack_run, "lua", "-f", str(LUA_DIR / "ddp_state.lua")]

    def cmd_create_work_order(self, job: str, quantity: int) -> list[str]:
        return [self.dfhack_run, "workorder", json.dumps({"job": job, "amount_total": quantity})]

    def cmd_enqueue_blueprint(self, blueprint_file: str, label: str, zone: Zone) -> list[str]:
        return [
            self.dfhack_run, "quickfort", "run", str(self.blueprints_dir / blueprint_file),
            "-n", f"/{label}", "--cursor", f"{zone.x},{zone.y},{zone.z}",
        ]

    def cmd_set_paused(self, paused: bool) -> list[str]:
        return [self.dfhack_run, "lua", f"df.global.pause_state={'true' if paused else 'false'}"]

    # --- FortressBridge -------------------------------------------------------
    def _not_ready(self) -> BridgeError:
        return BridgeError("DFHack bridge is a stub in Step 1: use the mock server (DDP_BRIDGE=mock)")

    def get_state(self) -> FortressState:
        raise self._not_ready()

    def create_work_order(self, job: str, quantity: int, *, source: str = "autopilot") -> int:
        raise self._not_ready()

    def cancel_work_order(self, order_id: int) -> None:
        raise self._not_ready()

    def expand_stockpile(self, stockpile_id: int, cells: int) -> None:
        raise self._not_ready()

    def link_stockpile(self, stockpile_id: int, workshop: str) -> None:
        raise self._not_ready()

    def enqueue_blueprint(self, queue_id: str, blueprint: str, zone_id: str) -> None:
        raise self._not_ready()

    def set_paused(self, paused: bool) -> None:
        raise self._not_ready()
