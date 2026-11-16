"""Real DFHack bridge.

Transport: `dfhack-run` (ships with DFHack) runs our Lua scripts inside the game.
Payloads and results travel through temp JSON files, never the console, so DFHack
diagnostics on stdout can't corrupt them.

  reading -> `lua/ddp_state.lua`, mapped onto FortressState by `dfhack_state.raw_to_state`
  actions -> `lua/ddp_actions.lua` (manager orders, pause, stockpile links)

Orders created by the autopilot are remembered in a local ledger (per fortress),
so it only ever cancels its own orders and never the player's.
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
from blueprints import Section, load_catalog, load_sections
from dfhack_bridge.dfhack_state import (
    ORDER_SPECS,
    WORKSHOP_NAMES,
    RawStateError,
    blueprint_job,
    find_raw_zone,
    fortress_key,
    raw_to_state,
)
from dfhack_bridge.protocol import FortressState

LUA_DIR = Path(__file__).resolve().parent / "lua"
STATE_SCRIPT = LUA_DIR / "ddp_state.lua"
ACTIONS_SCRIPT = LUA_DIR / "ddp_actions.lua"
# Don't flash a console window every poll when the GUI runs without one (pythonw).
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_dfhack_run(explicit: str | None = None) -> str:
    """Explicit path > DDP_DFHACK_RUN > PATH > `dfhack-run` (resolved by the OS at call time)."""
    for cand in (explicit, os.environ.get("DDP_DFHACK_RUN")):
        if cand:
            return cand
    return shutil.which("dfhack-run") or shutil.which("dfhack-run.exe") or "dfhack-run"


class OrderLedger:
    """{fortress key: {order id: {"job": DF job_type, "source": ...}}} persisted as JSON."""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, dict[str, dict[str, str]]] = {}
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def get(self, fort: str, order_id: int) -> dict[str, str] | None:
        return self.data.get(fort, {}).get(str(order_id))

    def add(self, fort: str, order_id: int, job_type: str, source: str) -> None:
        self.data.setdefault(fort, {})[str(order_id)] = {"job": job_type, "source": source}
        self._save()

    def remove(self, fort: str, order_id: int) -> None:
        if self.data.get(fort, {}).pop(str(order_id), None) is not None:
            self._save()

    def prune(self, fort: str, live: dict[int, str]) -> None:
        """Forget orders that are gone, or whose id now belongs to a different job."""
        mine = self.data.get(fort, {})
        stale = [k for k, v in mine.items() if live.get(int(k)) != v["job"]]
        for k in stale:
            del mine[k]
        if stale:
            self._save()

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")


class BlueprintLedger(OrderLedger):
    """{fortress key: {queue id: {blueprint, zone_id, x, y, z, w, h, phase, progress}}} persisted as JSON."""

    def entries(self, fort: str) -> dict[str, dict[str, Any]]:
        return self.data.get(fort, {})

    def put(self, fort: str, queue_id: str, entry: dict[str, Any]) -> None:
        self.data.setdefault(fort, {})[queue_id] = entry
        self._save()


class DFHackBridge(FortressBridge):
    name = "dfhack"
    unsupported = frozenset({"act.expand_stockpile"})

    def __init__(self, dfhack_run: str | None = None, blueprints_dir: Path | None = None,
                 timeout: float = 10.0, ledger_path: Path | None = None,
                 blueprint_ledger_path: Path | None = None):
        self.dfhack_run = find_dfhack_run(dfhack_run)
        self.blueprints_dir = blueprints_dir or Path(__file__).resolve().parent.parent / "blueprints"
        self.timeout = timeout
        self.ledger = OrderLedger(ledger_path)
        if blueprint_ledger_path is None and ledger_path is not None:
            blueprint_ledger_path = ledger_path.with_name("dfhack_blueprints.json")
        self.bp_ledger = BlueprintLedger(blueprint_ledger_path)
        self.catalog = load_catalog()
        self.last_raw: dict[str, Any] | None = None
        self.last_elapsed_ms: int = 0

    # --- command builders (pure, testable) ------------------------------------
    def cmd_get_state(self, out_path: Path | None = None) -> list[str]:
        cmd = [self.dfhack_run, "lua", "-f", str(STATE_SCRIPT)]
        return cmd + [str(out_path)] if out_path else cmd

    def cmd_action(self, payload_path: Path, result_path: Path) -> list[str]:
        return [self.dfhack_run, "lua", "-f", str(ACTIONS_SCRIPT), str(payload_path), str(result_path)]

    # --- transport --------------------------------------------------------------
    def _run(self, cmd: list[str], what: str) -> str:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout,
                                  errors="replace", creationflags=NO_WINDOW)
        except FileNotFoundError as e:
            raise BridgeError(f"dfhack-run not found ({self.dfhack_run}); set DDP_DFHACK_RUN") from e
        except subprocess.TimeoutExpired as e:
            raise BridgeError(f"dfhack-run timed out after {self.timeout}s") from e
        console = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            raise BridgeError(f"{what} failed (exit {proc.returncode}): {console[-500:] or 'no output'}")
        return console

    @staticmethod
    def _temp_path(prefix: str) -> Path:
        fd, name = tempfile.mkstemp(prefix=prefix, suffix=".json")
        os.close(fd)
        path = Path(name)
        path.unlink()
        return path

    # --- reading --------------------------------------------------------------
    def read_raw(self) -> dict[str, Any]:
        """Run the exporter and return its raw JSON document."""
        out = self._temp_path("ddp_state_")
        started = time.monotonic()
        try:
            console = self._run(self.cmd_get_state(out), "ddp_state.lua")
            if not out.exists():
                raise BridgeError(f"ddp_state.lua failed: {console[-500:] or 'no output'}")
            raw = json.loads(out.read_text(encoding="utf-8"))
        finally:
            out.unlink(missing_ok=True)
        self.last_elapsed_ms = round((time.monotonic() - started) * 1000)
        self.last_raw = raw
        return raw

    def get_state(self) -> FortressState:
        raw = self.read_raw()
        try:
            state = raw_to_state(raw)
        except RawStateError as e:
            raise BridgeError(str(e)) from e
        fort = fortress_key(raw)
        if "orders" in raw:  # don't forget our orders when only that section failed
            self.ledger.prune(fort, {o["id"]: o.get("job_type", "") for o in raw["orders"]})
        for o in state.work_orders:
            entry = self.ledger.get(fort, o.id)
            if entry:
                o.source = entry["source"]
        self._track_blueprints(fort, raw, state)
        return state

    def _sections(self, blueprint: str) -> list[Section]:
        if blueprint not in self.catalog:
            raise BridgeError(f"unknown blueprint {blueprint!r}")
        return load_sections(blueprint, self.catalog)

    def _track_blueprints(self, fort: str, raw: dict[str, Any], state: FortressState) -> None:
        zones_ok = "zones" in raw
        for queue_id, entry in self.bp_ledger.entries(fort).items():
            zone = find_raw_zone(raw, entry["zone_id"]) if zones_ok else None
            job = blueprint_job(queue_id, entry, zone, self._sections(entry["blueprint"]))
            progress = round(job.progress, 3)
            if (job.status == "done" and entry.get("phase") != "done") or progress != entry.get("progress"):
                self.bp_ledger.put(fort, queue_id, {**entry, "progress": progress,
                                                    "phase": "done" if job.status == "done" else entry["phase"]})
            state.blueprints.append(job)
            for z in state.zones:
                if z.id == entry["zone_id"]:
                    z.assigned = queue_id

    # --- actions ----------------------------------------------------------------
    def run_action(self, action: str, **params: Any) -> dict[str, Any]:
        payload, result = self._temp_path("ddp_action_"), self._temp_path("ddp_result_")
        try:
            payload.write_text(json.dumps({"action": action, **params}), encoding="utf-8")
            console = self._run(self.cmd_action(payload, result), f"ddp_actions.lua {action}")
            if not result.exists():
                raise BridgeError(f"ddp_actions.lua {action}: no result ({console[-300:] or 'no output'})")
            res = json.loads(result.read_text(encoding="utf-8"))
        finally:
            payload.unlink(missing_ok=True)
            result.unlink(missing_ok=True)
        if not res.get("ok"):
            raise BridgeError(f"{action}: {res.get('error', 'unknown error')}")
        return res

    def _fort(self) -> str:
        return fortress_key(self.last_raw or self.read_raw())

    def create_work_order(self, job: str, quantity: int, *, source: str = "autopilot") -> int:
        spec = ORDER_SPECS.get(job)
        if spec is None:
            raise BridgeError(f"no DFHack order for job {job!r}")
        fort = self._fort()
        order_id = int(self.run_action("create_order", order=spec, amount=int(quantity))["order_id"])
        self.ledger.add(fort, order_id, spec["job"], source)
        return order_id

    def cancel_work_order(self, order_id: int) -> None:
        fort = self._fort()
        entry = self.ledger.get(fort, order_id)
        if entry is None:
            raise BridgeError(f"order {order_id} was not created by the autopilot: not cancelling it")
        self.run_action("cancel_order", order_id=order_id, job_type=entry["job"])
        self.ledger.remove(fort, order_id)

    def link_stockpile(self, stockpile_id: int, workshop: str) -> None:
        raw = self.last_raw or self.read_raw()
        pile = next((p for p in raw.get("stockpiles", []) if p["id"] == stockpile_id), None)
        if pile is None:
            raise BridgeError(f"stockpile {stockpile_id} not found")
        linked = set(pile.get("give_to_workshops", []))
        candidates = [w["id"] for w in raw.get("workshops", [])
                      if w.get("active") and WORKSHOP_NAMES.get(w.get("type", ""), w.get("type")) == workshop]
        if not candidates:
            raise BridgeError(f"no built {workshop} workshop")
        if linked & set(candidates):
            return
        self.run_action("link_stockpile", stockpile_id=stockpile_id, workshop_id=candidates[0])

    def set_paused(self, paused: bool) -> None:
        self.run_action("set_paused", paused=paused)

    def expand_stockpile(self, stockpile_id: int, cells: int) -> None:
        raise BridgeError("expanding stockpiles is not supported on DFHack yet")

    def _quickfort(self, mode: str, sections: list[Section], pos: dict[str, int]) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        for sec in sections:
            if sec.mode == mode and sec.cells:
                res = self.run_action("apply_blueprint", mode=mode, pos=pos,
                                      cells=[{"x": x, "y": y, "text": t} for x, y, t in sec.cells])
                stats.update(res.get("stats") or {})
        return stats

    def enqueue_blueprint(self, queue_id: str, blueprint: str, zone_id: str) -> None:
        """Dig phase: designate the blueprint's #dig section at the zone's top-left corner."""
        raw = self.read_raw()
        fort = fortress_key(raw)
        if queue_id in self.bp_ledger.entries(fort):
            raise BridgeError(f"blueprint {queue_id} already applied")
        sections = self._sections(blueprint)
        zone = find_raw_zone(raw, zone_id)
        if zone is None:
            raise BridgeError(f"zone {zone_id} not found: name an activity zone 'DDP {zone_id} ...'")
        for other in self.bp_ledger.entries(fort).values():
            if other["zone_id"] == zone_id:
                raise BridgeError(f"zone {zone_id} is already used by blueprint {other['blueprint']}")
        w, h = self.catalog[blueprint]["size"]
        zw, zh = zone["x2"] - zone["x1"] + 1, zone["y2"] - zone["y1"] + 1
        if w > zw or h > zh:
            raise BridgeError(f"blueprint {blueprint} ({w}x{h}) does not fit zone {zone_id} ({zw}x{zh})")
        pos = {"x": zone["x1"], "y": zone["y1"], "z": zone["z"]}
        stats = self._quickfort("dig", sections, pos)
        invalid = (stats.get("dig_invalid_tiles") or {}).get("value", 0)
        self.bp_ledger.put(fort, queue_id, {"blueprint": blueprint, "zone_id": zone_id, **pos, "w": w, "h": h,
                                            "phase": "dig", "progress": 0.0, "invalid_tiles": invalid})

    def advance_blueprint(self, queue_id: str) -> None:
        """Build phase: place the #build/#place sections once digging is complete."""
        fort = self._fort()
        entry = self.bp_ledger.entries(fort).get(queue_id)
        if entry is None or entry.get("phase") != "dig":
            raise BridgeError(f"blueprint {queue_id} is not waiting to be built")
        sections = self._sections(entry["blueprint"])
        pos = {"x": entry["x"], "y": entry["y"], "z": entry["z"]}
        stats = {**self._quickfort("build", sections, pos), **self._quickfort("place", sections, pos)}
        expected = sum(len(s.cells) for s in sections if s.mode == "build")
        self.bp_ledger.put(fort, queue_id, {**entry, "phase": "build" if expected else "done",
                                            "progress": 0.5 if expected else 1.0})
        placed = (stats.get("build_designated") or {}).get("value", 0)
        if placed < expected:
            problems = ", ".join(f"{v['label']}: {v['value']}" for v in stats.values()
                                 if isinstance(v, dict) and v.get("value"))
            raise BridgeError(f"blueprint {queue_id}: placed {placed}/{expected} buildings ({problems})")
