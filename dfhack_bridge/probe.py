"""Diagnostic for the live DFHack bridge: read once, save raw + mapped state, print a summary.

    ddp-dfhack-probe [--dfhack-run PATH] [--out DIR]

Run it with Dwarf Fortress + DFHack open and a fortress loaded, then share the
two JSON files written to DIR (default: ./ddp_probe).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dfhack_bridge.base import BridgeError
from dfhack_bridge.dfhack_bridge import DFHackBridge
from dfhack_bridge.dfhack_state import RawStateError, raw_to_state


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Read the live fortress once through DFHack")
    p.add_argument("--dfhack-run", help="path to dfhack-run(.exe); default: DDP_DFHACK_RUN or PATH")
    p.add_argument("--out", type=Path, default=Path("ddp_probe"))
    args = p.parse_args(argv)

    bridge = DFHackBridge(dfhack_run=args.dfhack_run)
    print(f"dfhack-run: {bridge.dfhack_run}")
    try:
        raw = bridge.read_raw()
    except BridgeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "raw_state.json").write_text(json.dumps(raw, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"DFHack {raw.get('dfhack_version', '?')} / DF {raw.get('df_version', '?')}")
    print(f"lua {raw.get('elapsed_ms', '?')} ms, round trip {bridge.last_elapsed_ms} ms")
    for err in raw.get("errors", []):
        print(f"section error: {err}", file=sys.stderr)
    try:
        state = raw_to_state(raw)
    except RawStateError as e:
        print(f"ERROR mapping state: {e}", file=sys.stderr)
        return 3
    (args.out / "state.json").write_text(state.model_dump_json(indent=1), encoding="utf-8")
    print(f"fortress: {state.fortress_name}  year {state.date.year} day {state.date.day_of_year}"
          f"  paused={state.paused}")
    print(f"citizens {state.population}  food {state.food_days():.0f}d  drink {state.drink_days():.0f}d")
    print("stocks: " + ", ".join(f"{k}={v}" for k, v in state.stocks.items()))
    print(f"orders {len(state.work_orders)}  stockpiles {len(state.stockpiles)}  workshops "
          f"{sum(w.count for w in state.workshops)}  DDP zones {len(state.zones)}  ground items "
          f"{state.total_ground_items()}")
    for ev in state.active_events():
        print(f"event: {ev.severity} {ev.kind} — {ev.message}")
    print(f"saved {args.out / 'raw_state.json'} and {args.out / 'state.json'}")
    return 1 if raw.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
