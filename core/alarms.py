"""Deterministic alarm evaluation: pure function of (state, doctrine)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from core.doctrine import FortressDoctrine
from core.i18n import tr
from dfhack_bridge.protocol import FortressState

Level = Literal["warning", "critical"]


@dataclass(frozen=True)
class Alarm:
    kind: str
    level: Level
    key: str
    params: dict = field(default_factory=dict, compare=False, hash=False)

    def message(self) -> str:
        return tr(self.key, **self.params)


def evaluate_alarms(state: FortressState, doctrine: FortressDoctrine) -> list[Alarm]:
    a = doctrine.alarms
    out: list[Alarm] = []
    if state.population:
        food = state.food_days()
        if food < a.food_days_critical:
            out.append(Alarm("food_low", "critical", "alarm.food_low", {"days": food, "limit": a.food_days_critical}))
        elif food < a.food_days_warning:
            out.append(Alarm("food_low", "warning", "alarm.food_low", {"days": food, "limit": a.food_days_warning}))
        drink = state.drink_days()
        if drink < a.drink_days_critical:
            out.append(Alarm("drink_low", "critical", "alarm.drink_low", {"days": drink, "limit": a.drink_days_critical}))
        elif drink < a.drink_days_warning:
            out.append(Alarm("drink_low", "warning", "alarm.drink_low", {"days": drink, "limit": a.drink_days_warning}))
        stressed = sum(1 for d in state.dwarves if d.stress >= a.stress_critical)
        pct = round(100 * stressed / state.population)
        if stressed and pct >= a.stressed_dwarves_pct_critical:
            out.append(Alarm("stress", "critical", "alarm.stress", {"pct": pct}))
        elif any(d.stress >= a.stress_warning for d in state.dwarves):
            warn_pct = round(100 * sum(1 for d in state.dwarves if d.stress >= a.stress_warning) / state.population)
            out.append(Alarm("stress", "warning", "alarm.stress", {"pct": warn_pct}))
        idle = round(100 * sum(1 for d in state.dwarves if d.idle) / state.population)
        if idle >= a.idle_pct_warning:
            out.append(Alarm("idle", "warning", "alarm.idle", {"pct": idle}))
    ground = state.total_ground_items()
    if ground >= a.ground_items_warning:
        out.append(Alarm("ground", "warning", "alarm.ground", {"n": ground}))
    for ev in state.active_events():
        if ev.kind in a.critical_events:
            out.append(Alarm(f"event:{ev.kind}", "critical", "alarm.event", {"message": ev.message}))
    return out
