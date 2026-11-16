"""Simulated fortress that evolves over time, served over the bridge protocol.

Run standalone:  python -m dfhack_bridge.mock_server --speed 1.0 --seed 7
Protocol: one JSON request per line over TCP, one JSON response per line.
  {"cmd": "get_state"}                              -> {"ok": true, "state": {...}}
  {"cmd": "create_work_order", "job": "BrewDrink", "quantity": 10, "source": "autopilot"}
  {"cmd": "cancel_work_order", "order_id": 3}
  {"cmd": "expand_stockpile", "stockpile_id": 2, "cells": 10}
  {"cmd": "link_stockpile", "stockpile_id": 2, "workshop": "Mason"}
  {"cmd": "enqueue_blueprint", "queue_id": "bedrooms-1", "blueprint": "bedrooms_3x3", "zone_id": "Z1"}
  {"cmd": "set_paused", "paused": true}
  {"cmd": "set_speed", "days_per_second": 2.0}
  {"cmd": "inject_event", "kind": "siege"}
  {"cmd": "step", "days": 10}                       (advance even while paused; tests/demos)
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import socketserver
import threading
import time
from dataclasses import dataclass
from typing import Any

from blueprints import load_catalog
from dfhack_bridge.protocol import (
    DAYS_PER_YEAR,
    ITEM_CATEGORY,
    LABORS,
    BlueprintJob,
    ConsumptionRates,
    Dwarf,
    FortressState,
    GameDate,
    GameEvent,
    Stockpile,
    WorkOrder,
    Workshop,
    Zone,
)

log = logging.getLogger("mock_server")



@dataclass(frozen=True)
class Recipe:
    item: str
    out: int
    inputs: dict[str, int]
    labor: str
    workshop: str
    units_per_day: float


RECIPES: dict[str, Recipe] = {
    "BrewDrink": Recipe("drink", 5, {"plants": 1}, "brewing", "Still", 3.0),
    "PrepareMeal": Recipe("prepared_meals", 3, {"plants": 1}, "cooking", "Kitchen", 2.0),
    "ConstructBed": Recipe("beds", 1, {"wood": 1}, "carpentry", "Carpenter", 1.0),
    "MakeBarrel": Recipe("barrels", 1, {"wood": 1}, "carpentry", "Carpenter", 1.5),
    "MakeBin": Recipe("bins", 1, {"wood": 1}, "carpentry", "Carpenter", 1.5),
    "MakeTable": Recipe("tables", 1, {"stone": 1}, "masonry", "Mason", 1.5),
    "MakeChair": Recipe("chairs", 1, {"stone": 1}, "masonry", "Mason", 1.5),
    "ConstructDoor": Recipe("doors", 1, {"stone": 1}, "masonry", "Mason", 1.5),
    "ConstructBlocks": Recipe("blocks", 4, {"stone": 1}, "masonry", "Mason", 1.5),
    "SmeltIron": Recipe("iron_bars", 1, {"stone": 2}, "smithing", "Smelter", 0.5),
}

# Raw gathering per dwarf with the labor enabled, per day, scaled by skill.
GATHERING = {"farming": ("plants", 1.5), "fishing": ("meat", 0.5), "hunting": ("meat", 0.6),
             "woodcutting": ("wood", 1.0), "mining": ("stone", 1.2)}
# Dwarves stop gathering raw materials above these per-capita (or absolute) caps.
GATHER_CAP_PER_DWARF = {"plants": 12, "meat": 4}
GATHER_CAP_ABS = {"wood": 150, "stone": 200}
STARTING_LABORS = ("mining", "woodcutting", "carpentry", "masonry", "brewing", "cooking", "farming")

CONTAINER_FOR = {"food": "barrels", "drink": "barrels"}
CONTAINER_CAPACITY = 8
DEFAULT_CONTAINER = "bins"

FIRST_NAMES = ["Urist", "Kadol", "Bomrek", "Zuglar", "Ingiz", "Litast", "Mafol", "Rigoth", "Ast", "Domas",
               "Nish", "Tobul", "Erith", "Sodel", "Vabok", "Logem", "Melbil", "Thikut", "Ducim", "Fikod"]
LAST_NAMES = ["McAxe", "Ironfist", "Bronzebeard", "Ale-Keeper", "Stonecutter", "Deepdelver", "Oakheart",
              "Gemfinder", "Hammerfall", "Mugbrewer"]
PROFESSION_FOR = {"mining": "Miner", "woodcutting": "Woodcutter", "carpentry": "Carpenter",
                  "masonry": "Mason", "brewing": "Brewer", "cooking": "Cook", "farming": "Farmer",
                  "fishing": "Fisherdwarf", "hunting": "Hunter", "smithing": "Blacksmith",
                  "hauling": "Peasant"}

EVENT_TEXT = {
    "siege": ("critical", "A vile force of darkness has arrived!", 25),
    "forgotten_beast": ("critical", "A forgotten beast has come! A great roaring beast of fire.", 15),
    "cave_in": ("critical", "A section of the cavern has collapsed!", 3),
    "tantrum_spiral": ("critical", "Multiple dwarves are throwing tantrums!", 10),
    "migrants": ("info", "Some migrants have arrived.", 1),
    "caravan": ("info", "A caravan from the Mountainhomes has arrived.", 10),
    "strange_mood": ("warning", "A dwarf is taken by a fey mood!", 8),
}



class FortressSim:
    """Deterministic (seeded) fortress simulation. Thread-safe through `lock`."""

    def __init__(self, seed: int = 7, catalog: dict[str, Any] | None = None, random_events: bool = True):
        self.rng = random.Random(seed)
        self.catalog = catalog if catalog is not None else load_catalog()
        self.random_events = random_events
        self.lock = threading.RLock()
        self.day = 0
        self.tick = 0
        self.paused = False
        self.days_per_second = 0.5
        self._carry = 0.0
        self._next_order_id = 1
        self._next_event_id = 1
        self._next_dwarf_id = 1
        self.bedrooms = 0
        self.dining = False
        self.rates = ConsumptionRates()
        self.stocks: dict[str, float] = {
            "prepared_meals": 30, "plants": 30, "meat": 15, "drink": 45, "wood": 40, "stone": 60,
            "blocks": 8, "iron_bars": 0, "beds": 1, "barrels": 3, "bins": 2, "tables": 2, "chairs": 2, "doors": 2,
        }
        self.stockpiles = [
            Stockpile(id=1, name="Cibo principale", category="food", capacity=90, used=0),
            Stockpile(id=2, name="Cantina", category="drink", capacity=60, used=0),
            Stockpile(id=3, name="Legname", category="wood", capacity=60, used=0, links=["Carpenter"]),
            Stockpile(id=4, name="Pietra grezza", category="stone", capacity=80, used=0),
            Stockpile(id=5, name="Mobili", category="furniture", capacity=25, used=0),
            Stockpile(id=6, name="Barre & blocchi", category="bars_blocks", capacity=40, used=0),
        ]
        self.workshops = [Workshop(name=n) for n in ("Carpenter", "Mason", "Still", "Kitchen")]
        self.zones = [
            Zone(id="Z1", name="Ala dormitori", x=40, y=60, z=-3, w=11, h=7),
            Zone(id="Z2", name="Distretto laboratori", x=55, y=60, z=-3, w=11, h=5),
            Zone(id="Z3", name="Sala comune", x=40, y=70, z=-3, w=9, h=7),
            Zone(id="Z4", name="Riserva magazzini", x=55, y=70, z=-4, w=10, h=6),
        ]
        self.orders: list[WorkOrder] = []
        self.blueprints: list[BlueprintJob] = []
        self.events: list[GameEvent] = []
        self._event_end: dict[int, int] = {}
        self.dwarves: list[Dwarf] = [self._new_dwarf(main) for main in STARTING_LABORS]
        self._refresh_logistics()

    # ------------------------------------------------------------------ helpers
    def _new_dwarf(self, main: str | None = None) -> Dwarf:
        main = main or self.rng.choice([l for l in LABORS if l != "hauling"])
        skills = {l: self.rng.randint(0, 4) for l in LABORS}
        skills[main] = self.rng.randint(5, 15)
        second = self.rng.choice([l for l in LABORS if l not in (main, "hauling")])
        skills[second] = max(skills[second], self.rng.randint(3, 8))
        labors = sorted({main, second, "hauling"})
        d = Dwarf(
            id=self._next_dwarf_id,
            name=f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}",
            profession=PROFESSION_FOR[main],
            stress=self.rng.randint(0, 20_000),
            labors=labors,
            skills=skills,
        )
        self._next_dwarf_id += 1
        return d

    def _add_event(self, kind: str) -> GameEvent:
        severity, message, duration = EVENT_TEXT[kind]
        ev = GameEvent(id=self._next_event_id, kind=kind, severity=severity, message=message, day=self.day)
        self._next_event_id += 1
        self.events.append(ev)
        self._event_end[ev.id] = self.day + duration
        self.events = self.events[-40:]
        return ev

    def _active(self, kind: str) -> bool:
        return any(e.active and e.kind == kind for e in self.events)

    def _workshop_count(self, name: str) -> int:
        return sum(w.count for w in self.workshops if w.name == name)

    def _capacity(self, category: str) -> int:
        return sum(s.capacity for s in self.stockpiles if s.category == category)

    def _refresh_logistics(self) -> None:
        totals: dict[str, int] = {}
        for item, qty in self.stocks.items():
            cat = ITEM_CATEGORY.get(item, "misc")
            totals[cat] = totals.get(cat, 0) + int(qty)
        self.ground: dict[str, int] = {}
        for cat, total in totals.items():
            piles = [s for s in self.stockpiles if s.category == cat]
            remaining = total
            for s in piles:
                s.used = min(s.capacity, remaining)
                remaining -= s.used
            if remaining > 0:
                self.ground[cat] = remaining

    # ------------------------------------------------------------------ simulation
    def advance_real_time(self, seconds: float) -> None:
        with self.lock:
            if self.paused:
                return
            self._carry += seconds * self.days_per_second
            whole = int(self._carry)
            self._carry -= whole
            for _ in range(whole):
                self.step_day()

    def step_day(self) -> None:
        with self.lock:
            self.day += 1
            self.tick += 1
            self._consume()
            busy = self._work_orders()
            self._gather(busy)
            self._blueprints_progress(busy)
            self._stress()
            self._random_events()
            self._resolve_events()
            self._refresh_logistics()

    def _consume(self) -> None:
        pop = len(self.dwarves)
        need_food = pop * self.rates.food_per_dwarf_day
        for item in ("prepared_meals", "meat", "plants"):
            take = min(self.stocks[item], need_food)
            self.stocks[item] -= take
            need_food -= take
        self._starving = need_food > 0.01
        need_drink = pop * self.rates.drink_per_dwarf_day
        take = min(self.stocks["drink"], need_drink)
        self.stocks["drink"] -= take
        self._thirsty = need_drink - take > 0.01

    def _work_orders(self) -> set[int]:
        busy: set[int] = set()
        for d in self.dwarves:
            d.activity = "Idle"
        military = self._active("siege") or self._active("forgotten_beast")
        for order in self.orders:
            if order.status not in ("queued", "active"):
                continue
            recipe = RECIPES[order.job]
            if not self._workshop_count(recipe.workshop):
                continue
            workers = [d for d in self.dwarves if recipe.labor in d.labors and d.id not in busy]
            workers = workers[: self._workshop_count(recipe.workshop)]
            if not workers:
                continue
            order.status = "active"
            linked = any(recipe.workshop in s.links for s in self.stockpiles)
            for w in workers:
                busy.add(w.id)
                w.activity = order.job
                rate = recipe.units_per_day * (1 + w.skills.get(recipe.labor, 0) / 10) * (1.2 if linked else 1.0)
                if military:
                    rate *= 0.3
                units = min(order.remaining, max(1, round(rate)))
                for _ in range(units):
                    if any(self.stocks.get(i, 0) < q for i, q in recipe.inputs.items()):
                        w.activity = f"{order.job} (no materials)"
                        break
                    for i, q in recipe.inputs.items():
                        self.stocks[i] -= q
                    self.stocks[recipe.item] = self.stocks.get(recipe.item, 0) + recipe.out
                    order.remaining -= 1
                if order.remaining <= 0:
                    order.status = "done"
                    break
        self.orders = [o for o in self.orders if o.status in ("queued", "active")] + [
            o for o in self.orders if o.status in ("done", "cancelled")
        ][-15:]
        return busy

    def _gather(self, busy: set[int]) -> None:
        for d in self.dwarves:
            if d.id in busy:
                continue
            options = [l for l in d.labors if l in GATHERING]
            if not options or self.rng.random() < 0.25:
                continue
            labor = max(options, key=lambda l: d.skills.get(l, 0))
            item, base = GATHERING[labor]
            cap = GATHER_CAP_ABS.get(item, GATHER_CAP_PER_DWARF.get(item, 10**9) * len(self.dwarves))
            if self.stocks[item] >= cap:
                continue
            if labor in ("farming",) and GameDate(day_of_year=self.day % DAYS_PER_YEAR).season == "winter":
                continue
            self.stocks[item] += base * (1 + d.skills.get(labor, 0) / 15)
            d.activity = labor.capitalize()

    def _blueprints_progress(self, busy: set[int]) -> None:
        miners = [d for d in self.dwarves if "mining" in d.labors and d.id not in busy]
        builders = [d for d in self.dwarves if d.id not in busy and d.activity == "Idle"]
        for job in self.blueprints:
            spec = self.catalog[job.blueprint]
            if job.status == "queued":
                job.status = "digging"
            if job.status == "digging" and miners:
                dug = sum(2 + d.skills.get("mining", 0) / 5 for d in miners)
                for d in miners:
                    d.activity = f"Dig {job.id}"
                cells_done = job.progress * spec["dig_cells"] + dug
                self.stocks["stone"] += dug * 0.3
                job.progress = min(1.0, cells_done / spec["dig_cells"])
                if job.progress >= 1.0:
                    job.status, job.progress = "building", 0.0
                    if self.rng.random() < 0.1:
                        self._add_event("cave_in")
                miners = []
            elif job.status == "building" and builders:
                mats = spec.get("build_materials", {})
                total = sum(mats.values()) or 1
                step = min(len(builders) * 2 / total, 1.0 - job.progress)
                needed = {i: q * step for i, q in mats.items()}
                if all(self.stocks.get(i, 0) >= q for i, q in needed.items()):
                    for i, q in needed.items():
                        self.stocks[i] -= q
                    job.progress = min(1.0, job.progress + step)
                    for d in builders[:2]:
                        d.activity = f"Build {job.id}"
                if job.progress >= 1.0:
                    job.status = "done"
                    self._apply_effects(spec.get("effects", {}))
                builders = builders[2:]

    def _apply_effects(self, effects: dict[str, Any]) -> None:
        self.bedrooms += effects.get("bedrooms", 0)
        if effects.get("dining"):
            self.dining = True
        for name in effects.get("workshops", []):
            existing = next((w for w in self.workshops if w.name == name), None)
            if existing:
                existing.count += 1
            else:
                self.workshops.append(Workshop(name=name))
        for cat, cells in effects.get("stockpile_cells", {}).items():
            pile = next((s for s in self.stockpiles if s.category == cat), None)
            if pile:
                pile.capacity += cells * 2

    def _stress(self) -> None:
        homeless = max(0, len(self.dwarves) - self.bedrooms)
        for i, d in enumerate(self.dwarves):
            delta = -400
            if self._thirsty:
                delta += 2500
            if self._starving:
                delta += 3500
            if i < homeless:
                delta += 250
            if not self.dining:
                delta += 150
            if self._active("siege") or self._active("forgotten_beast"):
                delta += 600
            if self.stocks["drink"] > len(self.dwarves) * 5:
                delta -= 200
            d.stress = max(0, min(250_000, d.stress + delta + self.rng.randint(-300, 300)))
        stressed = sum(1 for d in self.dwarves if d.stress > 100_000)
        if stressed >= max(2, len(self.dwarves) // 4) and not self._active("tantrum_spiral"):
            self._add_event("tantrum_spiral")

    def _random_events(self) -> None:
        if self.day % (DAYS_PER_YEAR // 4) == 0:
            n = self.rng.randint(2, 6)
            for _ in range(n):
                self.dwarves.append(self._new_dwarf())
            self._add_event("migrants")
        if not self.random_events:
            return
        roll = self.rng.random()
        if self.day > 120 and roll < 0.004 and not self._active("siege"):
            self._add_event("siege")
        elif self.day > 200 and roll < 0.006 and not self._active("forgotten_beast"):
            self._add_event("forgotten_beast")
        elif roll < 0.012:
            self._add_event(self.rng.choice(["caravan", "strange_mood"]))

    def _resolve_events(self) -> None:
        for ev in self.events:
            if ev.active and self.day >= self._event_end.get(ev.id, 0):
                if ev.kind == "tantrum_spiral" and sum(1 for d in self.dwarves if d.stress > 100_000) >= 2:
                    continue
                ev.active = False

    # ------------------------------------------------------------------ commands
    def snapshot(self) -> FortressState:
        with self.lock:
            return FortressState(
                fortress_name="Bembulnish (mock)",
                date=GameDate(year=1 + self.day // DAYS_PER_YEAR, day_of_year=self.day % DAYS_PER_YEAR),
                tick=self.tick,
                paused=self.paused,
                stocks={k: int(v) for k, v in self.stocks.items()},
                ground_items=dict(self.ground),
                work_orders=[o.model_copy() for o in self.orders],
                stockpiles=[s.model_copy(deep=True) for s in self.stockpiles],
                workshops=[w.model_copy() for w in self.workshops],
                zones=[z.model_copy() for z in self.zones],
                blueprints=[b.model_copy() for b in self.blueprints],
                dwarves=[d.model_copy(deep=True) for d in self.dwarves],
                events=[e.model_copy() for e in self.events],
                rates=self.rates.model_copy(),
            )

    def create_work_order(self, job: str, quantity: int, source: str = "user") -> int:
        with self.lock:
            if job not in RECIPES:
                raise ValueError(f"unknown job {job!r}")
            if quantity <= 0:
                raise ValueError("quantity must be positive")
            order = WorkOrder(id=self._next_order_id, job=job, item=RECIPES[job].item,
                              quantity=quantity, remaining=quantity, source=source)
            self._next_order_id += 1
            self.orders.append(order)
            return order.id

    def cancel_work_order(self, order_id: int) -> None:
        with self.lock:
            order = next((o for o in self.orders if o.id == order_id), None)
            if order is None or order.status not in ("queued", "active"):
                raise ValueError(f"no open order #{order_id}")
            order.status = "cancelled"

    def _pile(self, stockpile_id: int) -> Stockpile:
        pile = next((s for s in self.stockpiles if s.id == stockpile_id), None)
        if pile is None:
            raise ValueError(f"no stockpile #{stockpile_id}")
        return pile

    def expand_stockpile(self, stockpile_id: int, cells: int) -> None:
        with self.lock:
            pile = self._pile(stockpile_id)
            container = CONTAINER_FOR.get(pile.category, DEFAULT_CONTAINER)
            pile.capacity += cells
            usable = min(int(self.stocks.get(container, 0)), cells // 2)
            self.stocks[container] = self.stocks.get(container, 0) - usable
            pile.capacity += usable * CONTAINER_CAPACITY
            self._refresh_logistics()

    def link_stockpile(self, stockpile_id: int, workshop: str) -> None:
        with self.lock:
            pile = self._pile(stockpile_id)
            if not self._workshop_count(workshop):
                raise ValueError(f"no workshop {workshop!r}")
            if workshop not in pile.links:
                pile.links.append(workshop)

    def enqueue_blueprint(self, queue_id: str, blueprint: str, zone_id: str) -> None:
        with self.lock:
            if blueprint not in self.catalog:
                raise ValueError(f"unknown blueprint {blueprint!r}")
            zone = next((z for z in self.zones if z.id == zone_id), None)
            if zone is None:
                raise ValueError(f"zone {zone_id!r} not designated")
            if zone.assigned and zone.assigned != queue_id:
                raise ValueError(f"zone {zone_id} already used by {zone.assigned}")
            w, h = self.catalog[blueprint]["size"]
            if w > zone.w or h > zone.h:
                raise ValueError(f"blueprint {blueprint} ({w}x{h}) does not fit zone {zone_id} ({zone.w}x{zone.h})")
            if any(b.id == queue_id for b in self.blueprints):
                raise ValueError(f"{queue_id} already queued")
            zone.assigned = queue_id
            self.blueprints.append(BlueprintJob(id=queue_id, blueprint=blueprint, zone_id=zone_id))

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self.paused = paused

    def inject_event(self, kind: str) -> int:
        with self.lock:
            if kind not in EVENT_TEXT:
                raise ValueError(f"unknown event {kind!r}")
            return self._add_event(kind).id


def dispatch(sim: FortressSim, req: dict[str, Any]) -> dict[str, Any]:
    cmd = req.get("cmd")
    try:
        if cmd == "get_state":
            return {"ok": True, "state": sim.snapshot().model_dump(mode="json")}
        if cmd == "create_work_order":
            return {"ok": True, "order_id": sim.create_work_order(req["job"], int(req["quantity"]),
                                                                  req.get("source", "user"))}
        if cmd == "cancel_work_order":
            sim.cancel_work_order(int(req["order_id"]))
        elif cmd == "expand_stockpile":
            sim.expand_stockpile(int(req["stockpile_id"]), int(req["cells"]))
        elif cmd == "link_stockpile":
            sim.link_stockpile(int(req["stockpile_id"]), req["workshop"])
        elif cmd == "enqueue_blueprint":
            sim.enqueue_blueprint(req["queue_id"], req["blueprint"], req["zone_id"])
        elif cmd == "set_paused":
            sim.set_paused(bool(req["paused"]))
        elif cmd == "set_speed":
            sim.days_per_second = max(0.0, float(req["days_per_second"]))
        elif cmd == "inject_event":
            return {"ok": True, "event_id": sim.inject_event(req["kind"])}
        elif cmd == "step":
            for _ in range(int(req.get("days", 1))):
                sim.step_day()
        else:
            return {"ok": False, "error": f"unknown command {cmd!r}"}
        return {"ok": True}
    except (KeyError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        for raw in self.rfile:
            line = raw.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = dispatch(self.server.sim, req)  # type: ignore[attr-defined]
            except json.JSONDecodeError as exc:
                resp = {"ok": False, "error": f"bad json: {exc}"}
            self.wfile.write((json.dumps(resp, separators=(",", ":")) + "\n").encode())
            self.wfile.flush()


class MockServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, sim: FortressSim, host: str = "127.0.0.1", port: int = 5577):
        super().__init__((host, port), _Handler)
        self.sim = sim
        self._stop = threading.Event()
        self._clock = threading.Thread(target=self._run_clock, daemon=True)

    def _run_clock(self) -> None:
        last = time.monotonic()
        while not self._stop.wait(0.1):
            now = time.monotonic()
            self.sim.advance_real_time(now - last)
            last = now

    def start(self) -> threading.Thread:
        self._clock.start()
        t = threading.Thread(target=self.serve_forever, daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
        self.shutdown()
        self.server_close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Dwarf Fortress mock server for Dwarf Doctrine Pilot")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5577)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--speed", type=float, default=0.5, help="game days per real second")
    p.add_argument("--no-random-events", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    sim = FortressSim(seed=args.seed, random_events=not args.no_random_events)
    sim.days_per_second = args.speed
    server = MockServer(sim, args.host, args.port)
    log.info("mock fortress on %s:%d (speed %.2f days/s, seed %d)", args.host, args.port, args.speed, args.seed)
    server.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
