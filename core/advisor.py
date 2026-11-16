"""Strategic loop: Gemini negotiates doctrine changes on demand (never automatically).

Every request is a compact, self-contained payload (< `max_payload_tokens`):
system rules + minified doctrine + compact state summary + a few recent turns.
Gemini answers with structured JSON (`AdvisorReply`); a proposal is only a
`DoctrinePatch` that the user must approve.
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from core.alarms import evaluate_alarms
from core.autopilot import blueprint_readiness
from core.doctrine import (
    BlueprintQueueItem,
    DoctrinePatch,
    EmergencyPolicy,
    FortressDoctrine,
    GeminiBudget,
)
from core.i18n import language
from dfhack_bridge.protocol import KNOWN_JOBS, FortressState


class AdvisorReply(BaseModel):
    reply: str = Field(description="Short answer to the user, in the user's language")
    proposal: DoctrinePatch | None = Field(default=None, description="Doctrine change, only if useful")


@dataclass
class ChatTurn:
    role: str  # "user" | "model"
    text: str


@dataclass
class AdvisorResult:
    reply: AdvisorReply
    payload_tokens: int
    offline: bool = False


class BudgetExceeded(RuntimeError):
    def __init__(self, retry_in_s: int):
        super().__init__(f"retry in {retry_in_s}s")
        self.retry_in_s = retry_in_s


class RequestBudget:
    """Client-side Free Tier guard: requests per minute / per day + analysis cooldown."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._minute: deque[float] = deque()
        self._day: deque[float] = deque()
        self._last_analysis = -1e18

    def used_today(self) -> int:
        self._trim(self.clock())
        return len(self._day)

    def _trim(self, now: float) -> None:
        while self._minute and now - self._minute[0] >= 60:
            self._minute.popleft()
        while self._day and now - self._day[0] >= 86_400:
            self._day.popleft()

    def check(self, budget: GeminiBudget, *, analysis: bool = False) -> None:
        now = self.clock()
        self._trim(now)
        if analysis and now - self._last_analysis < budget.analysis_cooldown_s:
            raise BudgetExceeded(int(budget.analysis_cooldown_s - (now - self._last_analysis)) + 1)
        if len(self._minute) >= budget.max_requests_per_minute:
            raise BudgetExceeded(int(60 - (now - self._minute[0])) + 1)
        if len(self._day) >= budget.max_requests_per_day:
            raise BudgetExceeded(int(86_400 - (now - self._day[0])) + 1)

    def record(self, *, analysis: bool = False) -> None:
        now = self.clock()
        self._minute.append(now)
        self._day.append(now)
        if analysis:
            self._last_analysis = now


def summarize_state(state: FortressState, doctrine: FortressDoctrine, level: int = 0) -> dict:
    """Compact state for the LLM. Higher `level` = more aggressive trimming."""
    tracked = {t.item for t in doctrine.stock_targets}
    summary: dict = {
        "date": f"Y{state.date.year} {state.date.season} d{state.date.day_of_season}",
        "pop": state.population,
        "food_days": round(state.food_days(), 1),
        "drink_days": round(state.drink_days(), 1),
        "stocks": {k: v for k, v in sorted(state.stocks.items()) if k in tracked or level == 0},
        "alarms": [f"{a.level}:{a.kind}" for a in evaluate_alarms(state, doctrine)],
        "events": [e.kind for e in state.active_events()],
    }
    if level <= 1:
        summary["stockpiles"] = {s.category: f"{s.fill_pct}%" for s in state.stockpiles}
        summary["ground"] = state.total_ground_items()
        summary["blueprints"] = {b.id: blueprint_readiness(b, state)[0] for b in doctrine.blueprint_queue}
        summary["zones"] = {z.id: f"{z.w}x{z.h}{'*' if z.assigned else ''}" for z in state.zones}
        summary["workshops"] = sorted({w.name for w in state.workshops})
    if level == 0 and state.dwarves:
        stress = sorted(d.stress for d in state.dwarves)
        summary["stress"] = {"median": stress[len(stress) // 2], "max": stress[-1]}
        summary["idle"] = sum(1 for d in state.dwarves if d.idle)
        summary["orders_open"] = sum(1 for o in state.work_orders if o.status in ("queued", "active"))
    return summary


SYSTEM_PROMPT = """You are the strategic advisor of a Dwarf Fortress co-pilot (DFHack).
A deterministic autopilot executes ONLY the user-approved FortressDoctrine; you never act on the game.
Rules:
- Reply briefly in {lang}. Explain trade-offs with numbers from STATE.
- Propose changes ONLY via `proposal` (a DoctrinePatch): upsert lists by key (item/category/id), remove by key; omit untouched fields.
- Upserted stock targets must be complete objects with min <= target <= max.
- Use only these jobs: {jobs}. Blueprints: {blueprints}. Use only zones present in STATE.zones (free ones have no '*').
- Never change automation switches; the user owns them.
- If no change is needed, leave `proposal` null."""


def _estimate_tokens(text: str) -> int:
    return len(text) // 3 + 1


@dataclass
class GeminiAdvisor:
    api_key: str
    model: str = "gemini-2.5-flash"
    blueprints: list[str] = field(default_factory=list)
    budget: RequestBudget = field(default_factory=RequestBudget)
    history_turns: int = 4

    def __post_init__(self) -> None:
        from google import genai

        self._client = genai.Client(api_key=self.api_key)

    def build_payload(self, history: list[ChatTurn], message: str, doctrine: FortressDoctrine,
                      state: FortressState | None) -> tuple[str, str, int]:
        lang = "Italian" if language() == "it" else "English"
        system = SYSTEM_PROMPT.format(lang=lang, jobs=",".join(KNOWN_JOBS), blueprints=",".join(self.blueprints))
        max_tokens = doctrine.gemini.max_payload_tokens
        turns = history[-self.history_turns:]
        for level in range(3):
            state_json = json.dumps(summarize_state(state, doctrine, level), separators=(",", ":")) if state else "{}"
            while True:
                convo = "\n".join(f"{t.role.upper()}: {t.text[:400]}" for t in turns)
                user = f"DOCTRINE={doctrine.compact_json()}\nSTATE={state_json}\nHISTORY:\n{convo}\nUSER: {message}"
                tokens = self.count_tokens(system + user)
                if tokens <= max_tokens or not turns:
                    break
                turns = turns[1:]
            if tokens <= max_tokens:
                return system, user, tokens
        raise ValueError(f"payload {tokens} tokens > {max_tokens} even after trimming")

    def count_tokens(self, text: str) -> int:
        try:
            return int(self._client.models.count_tokens(model=self.model, contents=text).total_tokens)
        except Exception:  # offline / quota: fall back to a conservative estimate
            return _estimate_tokens(text)

    def ask(self, history: list[ChatTurn], message: str, doctrine: FortressDoctrine,
            state: FortressState | None, *, analysis: bool = False) -> AdvisorResult:
        from google.genai import types

        self.budget.check(doctrine.gemini, analysis=analysis)
        system, user, tokens = self.build_payload(history, message, doctrine, state)
        self.budget.record(analysis=analysis)
        resp = self._client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=AdvisorReply,
                temperature=0.3,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        reply = resp.parsed if isinstance(resp.parsed, AdvisorReply) else AdvisorReply.model_validate_json(resp.text)
        return AdvisorResult(reply=reply, payload_tokens=tokens)


@dataclass
class OfflineAdvisor:
    """Keyword-driven stand-in used when no API key is configured (demo & tests)."""

    blueprints: list[str] = field(default_factory=list)
    budget: RequestBudget = field(default_factory=RequestBudget)

    def ask(self, history: list[ChatTurn], message: str, doctrine: FortressDoctrine,
            state: FortressState | None, *, analysis: bool = False) -> AdvisorResult:
        self.budget.check(doctrine.gemini, analysis=analysis)
        self.budget.record(analysis=analysis)
        it = language() == "it"
        text = message.lower()
        patch = DoctrinePatch()
        notes: list[str] = []
        pop = state.population if state else 10

        def scale(item: str, per_dwarf_days: float) -> None:
            t = doctrine.target_for(item)
            if t is None or state is None:
                return
            rate = state.rates.drink_per_dwarf_day if item == "drink" else state.rates.food_per_dwarf_day
            target = max(round(t.target * 1.5), round(pop * rate * per_dwarf_days))
            patch.upsert_stock_targets.append(
                t.model_copy(update={"min": round(target * 0.5), "target": target, "max": max(t.max, target * 2)})
            )
            notes.append(f"{item}: target {t.target}→{target}")

        if re.search(r"birr|bevand|alcol|drink|booze|ale", text) or "drink_low" in text:
            scale("drink", 30)
        if re.search(r"cibo|pasti|fame|food|meal", text) or "food_low" in text:
            scale("prepared_meals", 40)
        if re.search(r"magazzin|stockpile|storage|spazio", text) and state:
            free = next((z for z in state.zones if not z.assigned), None)
            if free and "stockpiles_core" in self.blueprints:
                patch.upsert_blueprints.append(
                    BlueprintQueueItem(id="storage-1", blueprint="stockpiles_core", zone_id=free.id, priority=70)
                )
                notes.append(f"stockpiles_core → {free.id}")
        if re.search(r"assedio|siege|difes|beast|bestia|allarme|alarm", text):
            patch.emergency = EmergencyPolicy(**{**doctrine.emergency.model_dump(),
                                                 "emergency_food_days": doctrine.emergency.emergency_food_days + 10,
                                                 "emergency_drink_days": doctrine.emergency.emergency_drink_days + 10})
            notes.append("emergency reserves +10d")

        if patch.is_empty():
            days = f"{state.food_days():.0f}/{state.drink_days():.0f}" if state else "?"
            reply = (f"(offline) Pop {pop}, giorni cibo/bevande {days}. Nessuna modifica proposta: "
                     "chiedimi di birra, cibo, magazzini o difesa." if it else
                     f"(offline) Pop {pop}, food/drink days {days}. No change proposed: "
                     "ask me about drink, food, storage or defence.")
            return AdvisorResult(AdvisorReply(reply=reply), payload_tokens=0, offline=True)
        reply = ("(offline) Propongo: " if it else "(offline) I propose: ") + "; ".join(notes)
        return AdvisorResult(AdvisorReply(reply=reply, proposal=patch), payload_tokens=0, offline=True)


def make_advisor(api_key: str | None, model: str, blueprints: list[str]) -> GeminiAdvisor | OfflineAdvisor:
    if api_key:
        return GeminiAdvisor(api_key=api_key, model=model, blueprints=blueprints)
    return OfflineAdvisor(blueprints=blueprints)
