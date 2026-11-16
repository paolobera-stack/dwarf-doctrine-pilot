"""Deterministic autopilot (0 tokens).

Every tick: read state -> evaluate alarms -> plan actions from the *approved*
doctrine -> execute at most `max_actions_per_tick`. Plans are derived from the
game state itself (open orders, queued blueprints), so re-running a tick on an
unchanged state plans nothing new: actions are idempotent.

Critical alarms never reach Gemini automatically: the engine applies the
deterministic emergency rules, pauses the game and notifies the user.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from blueprints import load_catalog
from core.alarms import Alarm, evaluate_alarms
from core.doctrine import BlueprintQueueItem, FortressDoctrine
from core.i18n import tr
from dfhack_bridge.base import BridgeError, FortressBridge
from dfhack_bridge.protocol import FortressState

ActionStatus = Literal["ok", "failed", "dry_run", "info"]
MODULE_ORDER = ("emergency", "production", "stockpiles", "blueprints")
EXPAND_COOLDOWN_TICKS = 5


@dataclass
class PlannedAction:
    module: str
    key: str
    params: dict
    execute: Callable[[FortressBridge], object] | None = None

    @property
    def signature(self) -> tuple:
        return (self.module, self.key, tuple(sorted((k, str(v)) for k, v in self.params.items())))


@dataclass
class ActionRecord:
    timestamp: float
    tick: int
    module: str
    key: str
    params: dict
    status: ActionStatus
    error: str | None = None

    def render(self) -> str:
        prefix = tr("act.dry_run") if self.status == "dry_run" else ""
        text = prefix + tr(self.key, **self.params)
        if self.status == "failed":
            text += "  — " + tr("act.failed", error=self.error)
        return text


@dataclass
class TickResult:
    state: FortressState
    alarms: list[Alarm]
    actions: list[ActionRecord] = field(default_factory=list)
    notifications: list[Alarm] = field(default_factory=list)
    paused_by_autopilot: bool = False


# ----------------------------------------------------------------------------- planners
def plan_production(state: FortressState, doctrine: FortressDoctrine) -> list[PlannedAction]:
    out: list[PlannedAction] = []
    for t in doctrine.stock_targets:
        if not t.enabled:
            continue
        current = state.stock(t.item)
        pending = state.pending_for(t.item) * t.yield_per_job
        if current + pending < t.min:
            jobs = math.ceil((t.target - current - pending) / t.yield_per_job)
            jobs = max(t.batch, math.ceil(jobs / t.batch) * t.batch)
            params = {"job": t.job, "quantity": jobs, "item": t.item, "current": current,
                      "pending": pending, "min": t.min}
            out.append(PlannedAction("production", "act.create_order", params,
                                     lambda b, j=t.job, q=jobs: b.create_work_order(j, q, source="autopilot")))
        elif current > t.max:
            for o in state.work_orders:
                if o.item == t.item and o.source == "autopilot" and o.status in ("queued", "active"):
                    params = {"order_id": o.id, "job": o.job, "item": t.item, "current": current, "max": t.max}
                    out.append(PlannedAction("production", "act.cancel_order", params,
                                             lambda b, oid=o.id: b.cancel_work_order(oid)))
    return out


def plan_stockpiles(state: FortressState, doctrine: FortressDoctrine, last_expand: dict[int, int],
                    planned_jobs: set[str] = frozenset()) -> list[PlannedAction]:
    out: list[PlannedAction] = []
    workshops = {w.name for w in state.workshops}
    open_jobs = {o.job for o in state.work_orders if o.status in ("queued", "active")} | set(planned_jobs)
    for rule in doctrine.stockpile_rules:
        if not rule.enabled:
            continue
        for pile in (p for p in state.stockpiles if p.category == rule.category):
            for ws in rule.link_to_workshops:
                if ws in workshops and ws not in pile.links:
                    out.append(PlannedAction("stockpiles", "act.link_stockpile", {"name": pile.name, "workshop": ws},
                                             lambda b, pid=pile.id, w=ws: b.link_stockpile(pid, w)))
            fill = pile.fill_pct
            if fill <= rule.max_fill_pct:
                continue
            container_target = next((t for t in doctrine.stock_targets if t.job == rule.container_job), None)
            enough = container_target is not None and state.stock(container_target.item) >= container_target.target
            if rule.container_job and rule.container_job not in open_jobs and not enough:
                open_jobs.add(rule.container_job)
                params = {"job": rule.container_job, "quantity": rule.container_batch, "name": pile.name,
                          "fill": fill, "max": rule.max_fill_pct}
                out.append(PlannedAction("stockpiles", "act.container_order", params,
                                         lambda b, j=rule.container_job, q=rule.container_batch:
                                         b.create_work_order(j, q, source="autopilot")))
            recent = state.tick - last_expand.get(pile.id, -10**9) < EXPAND_COOLDOWN_TICKS
            if rule.auto_expand_cells and not recent:
                params = {"name": pile.name, "cells": rule.auto_expand_cells, "fill": fill, "max": rule.max_fill_pct,
                          "stockpile_id": pile.id}
                out.append(PlannedAction("stockpiles", "act.expand_stockpile", params,
                                         lambda b, pid=pile.id, c=rule.auto_expand_cells: b.expand_stockpile(pid, c)))
    return out


@lru_cache(maxsize=1)
def _catalog() -> dict:
    return load_catalog()


def blueprint_readiness(item: BlueprintQueueItem, state: FortressState) -> tuple[str, list[str]]:
    """Return (status, unmet reasons) for a doctrine queue item against the live state.

    status: done/digging/building/queued (from game), missing_zone, blocked, planned (ready).
    """
    live = next((b for b in state.blueprints if b.id == item.id), None)
    if live:
        return live.status, []
    zone = next((z for z in state.zones if z.id == item.zone_id), None)
    if zone is None:
        return "missing_zone", [item.zone_id]
    reasons: list[str] = []
    spec = _catalog().get(item.blueprint)
    if spec is None:
        reasons.append(f"? {item.blueprint}")
    elif spec["size"][0] > zone.w or spec["size"][1] > zone.h:
        reasons.append(f"{spec['size'][0]}x{spec['size'][1]} > {zone.w}x{zone.h}")
    if zone.assigned and zone.assigned != item.id:
        reasons.append(f"{item.zone_id}→{zone.assigned}")
    pre = item.preconditions
    if state.population < pre.min_population:
        reasons.append(f"pop {state.population}<{pre.min_population}")
    for req in pre.min_stocks:
        if state.stock(req.item) < req.min:
            reasons.append(f"{req.item} {state.stock(req.item)}<{req.min}")
    done = {b.id for b in state.blueprints if b.status == "done"}
    for dep in pre.after:
        if dep not in done:
            reasons.append(f"⟂ {dep}")
    return ("blocked" if reasons else "planned"), reasons


def plan_blueprints(state: FortressState, doctrine: FortressDoctrine, alarms: list[Alarm]) -> list[PlannedAction]:
    if any(a.level == "critical" for a in alarms):
        return []
    out = [PlannedAction("blueprints", "act.advance_blueprint", {"id": b.id, "zone": b.zone_id},
                         lambda br, q=b.id: br.advance_blueprint(q))
           for b in state.blueprints if b.awaiting_build]
    active = [b for b in state.blueprints if b.status != "done"]
    if len(active) >= doctrine.limits.max_concurrent_blueprints:
        return out
    for item in sorted(doctrine.blueprint_queue, key=lambda i: -i.priority):
        status, _ = blueprint_readiness(item, state)
        if status == "planned":
            params = {"blueprint": item.blueprint, "zone": item.zone_id, "id": item.id}
            out.append(PlannedAction("blueprints", "act.enqueue_blueprint", params,
                                     lambda b, i=item: b.enqueue_blueprint(i.id, i.blueprint, i.zone_id)))
            break
    return out


def plan_emergency(state: FortressState, doctrine: FortressDoctrine, alarms: list[Alarm]) -> list[PlannedAction]:
    out: list[PlannedAction] = []
    critical = {a.kind for a in alarms if a.level == "critical"}
    pol = doctrine.emergency
    for kind, item, days_target, days_now, per_dwarf in (
        ("food_low", "prepared_meals", pol.emergency_food_days, state.food_days(), state.rates.food_per_dwarf_day),
        ("drink_low", "drink", pol.emergency_drink_days, state.drink_days(), state.rates.drink_per_dwarf_day),
    ):
        target = doctrine.target_for(item)
        if kind not in critical or target is None:
            continue
        if state.pending_for(item, sources=("emergency",)):
            continue
        have = state.food_total() if kind == "food_low" else state.stock(item)
        need = state.population * per_dwarf * days_target - have - state.pending_for(item) * target.yield_per_job
        if need <= 0:
            continue
        jobs = math.ceil(need / target.yield_per_job)
        params = {"job": target.job, "quantity": jobs, "item": item, "days": days_now}
        out.append(PlannedAction("emergency", "act.emergency_order", params,
                                 lambda b, j=target.job, q=jobs: b.create_work_order(j, q, source="emergency")))
    return out


# ----------------------------------------------------------------------------- engine
class AlertManager:
    """Per-kind cooldown so a persisting alarm notifies (and pauses) once per window."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._last: dict[str, float] = {}

    def due(self, alarms: list[Alarm], cooldown_s: float) -> list[Alarm]:
        now = self.clock()
        out = []
        for a in alarms:
            if a.level != "critical":
                continue
            last = self._last.get(a.kind)
            if last is None or now - last >= cooldown_s:
                self._last[a.kind] = now
                out.append(a)
        return out

    def rearm(self, alarms: list[Alarm]) -> None:
        for a in alarms:
            self._last.pop(a.kind, None)


class Autopilot:
    def __init__(self, bridge: FortressBridge, doctrine: FortressDoctrine, *, dry_run: bool = False,
                 enabled: bool = True, clock: Callable[[], float] = time.monotonic):
        self.bridge = bridge
        self.doctrine = doctrine
        self.dry_run = dry_run
        self.enabled = enabled
        self.clock = clock
        self.alerts = AlertManager(clock)
        self._last_expand: dict[int, int] = {}
        self._last_dry: set[tuple] = set()

    @property
    def dry_run(self) -> bool:
        """A read-only bridge (DFHack in Step 2A) always plans without executing."""
        return self._dry_run or getattr(self.bridge, "read_only", False)

    @dry_run.setter
    def dry_run(self, value: bool) -> None:
        self._dry_run = value

    def set_doctrine(self, doctrine: FortressDoctrine) -> None:
        self.doctrine = doctrine
        self._last_dry.clear()

    def plan(self, state: FortressState, alarms: list[Alarm]) -> list[PlannedAction]:
        d, m = self.doctrine, self.doctrine.modules
        planned: list[PlannedAction] = []
        if m.emergency:
            planned += plan_emergency(state, d, alarms)
        if m.production:
            emergency_items = {p.params["item"] for p in planned}
            planned += [p for p in plan_production(state, d)
                        if not (p.key == "act.create_order" and p.params["item"] in emergency_items)]
        if m.stockpiles:
            planned_jobs = {p.params["job"] for p in planned if p.key == "act.create_order"}
            planned += plan_stockpiles(state, d, self._last_expand, planned_jobs)
        if m.blueprints:
            planned += plan_blueprints(state, d, alarms)
        skip = getattr(self.bridge, "unsupported", frozenset())
        return [p for p in planned if p.key not in skip]

    def run_tick(self, state: FortressState | None = None) -> TickResult:
        state = state or self.bridge.get_state()
        alarms = evaluate_alarms(state, self.doctrine)
        result = TickResult(state=state, alarms=alarms)
        result.notifications = self.alerts.due(alarms, self.doctrine.emergency.notify_cooldown_s)
        if not self.enabled:
            return result

        now = self.clock()
        planned = self.plan(state, alarms)
        limit = self.doctrine.limits.max_actions_per_tick
        if self.dry_run:
            sigs = {p.signature for p in planned}
            for p in planned[:limit]:
                if p.signature not in self._last_dry:
                    result.actions.append(ActionRecord(now, state.tick, p.module, p.key, p.params, "dry_run"))
            self._last_dry = sigs
        else:
            for p in planned[:limit]:
                result.actions.append(self._execute(p, state, now))
        if len(planned) > limit and (result.actions or not self.dry_run):
            result.actions.append(ActionRecord(now, state.tick, "autopilot", "act.limit", {"n": limit}, "info"))

        if (result.notifications and self.doctrine.modules.emergency and self.doctrine.emergency.pause_on_critical
                and not state.paused):
            reason = "; ".join(a.message() for a in result.notifications)
            pause = PlannedAction("emergency", "act.pause", {"reason": reason}, lambda b: b.set_paused(True))
            if getattr(self.bridge, "read_only", False):
                result.actions.append(ActionRecord(now, state.tick, "emergency", "act.pause", pause.params, "dry_run"))
            else:
                # The safety pause is harmless, so it runs even in dry-run: it's the user's baseline.
                rec = self._execute(pause, state, now)
                result.actions.append(rec)
                result.paused_by_autopilot = rec.status == "ok"
                if rec.status != "ok":
                    self.alerts.rearm(result.notifications)  # retry the safety pause next tick
        return result

    def _execute(self, p: PlannedAction, state: FortressState, now: float) -> ActionRecord:
        try:
            if p.execute:
                p.execute(self.bridge)
            if p.key == "act.expand_stockpile":
                self._last_expand[p.params["stockpile_id"]] = state.tick
            return ActionRecord(now, state.tick, p.module, p.key, p.params, "ok")
        except BridgeError as exc:
            return ActionRecord(now, state.tick, p.module, p.key, p.params, "failed", str(exc))
