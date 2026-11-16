"""FortressDoctrine: the shared contract between the user, Gemini and the autopilot.

The doctrine is the *only* thing the deterministic autopilot obeys. Gemini can
only propose a `DoctrinePatch`; nothing changes until the user approves it.
AI-facing models avoid dict fields so they map cleanly onto Gemini's
structured-output schema.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class StockTarget(_Model):
    """Keep `item` between `min` and `max`; refill up to `target` when below `min`."""

    item: str = Field(description="Stock key, e.g. 'drink', 'prepared_meals', 'iron_bars'")
    job: str = Field(description="Work-order job that produces the item, e.g. 'BrewDrink'")
    min: int = Field(ge=0)
    target: int = Field(ge=0)
    max: int = Field(ge=0)
    yield_per_job: int = Field(default=1, ge=1, description="Items produced by one job of the order")
    batch: int = Field(default=1, ge=1, description="Order job counts are rounded up to this")
    enabled: bool = True

    @model_validator(mode="after")
    def _ordered(self) -> StockTarget:
        if not (self.min <= self.target <= self.max):
            raise ValueError(f"{self.item}: required min <= target <= max")
        return self


class StockpileRule(_Model):
    category: str = Field(description="Stockpile category, e.g. 'food', 'stone', 'furniture'")
    max_fill_pct: int = Field(default=85, ge=10, le=100, description="Act above this fill %")
    container_job: str | None = Field(
        default=None, description="Job producing containers for this category (e.g. 'MakeBin')"
    )
    container_batch: int = Field(default=5, ge=1)
    auto_expand_cells: int = Field(default=0, ge=0, description="Cells to add when full; 0 = never")
    link_to_workshops: list[str] = Field(default_factory=list)
    enabled: bool = True


class StockRequirement(_Model):
    item: str
    min: int = Field(ge=0)


class BlueprintPreconditions(_Model):
    min_population: int = Field(default=0, ge=0)
    min_stocks: list[StockRequirement] = Field(default_factory=list)
    after: list[str] = Field(default_factory=list, description="Queue ids that must be completed first")


class BlueprintQueueItem(_Model):
    id: str = Field(description="Unique queue id, e.g. 'bedrooms-1'")
    blueprint: str = Field(description="Blueprint id from blueprints/catalog.json")
    zone_id: str = Field(description="Zone designated by the user in-game")
    priority: int = Field(default=50, ge=0, le=100, description="Higher runs first")
    preconditions: BlueprintPreconditions = Field(default_factory=BlueprintPreconditions)


class AlarmThresholds(_Model):
    food_days_warning: float = Field(default=30, ge=0)
    food_days_critical: float = Field(default=10, ge=0)
    drink_days_warning: float = Field(default=20, ge=0)
    drink_days_critical: float = Field(default=7, ge=0)
    stress_warning: int = Field(default=50_000, ge=0)
    stress_critical: int = Field(default=100_000, ge=0)
    stressed_dwarves_pct_critical: int = Field(default=25, ge=0, le=100)
    idle_pct_warning: int = Field(default=40, ge=0, le=100)
    ground_items_warning: int = Field(default=150, ge=0)
    critical_events: list[str] = Field(
        default_factory=lambda: ["siege", "forgotten_beast", "cave_in", "tantrum_spiral"]
    )

    @model_validator(mode="after")
    def _critical_below_warning(self) -> AlarmThresholds:
        if self.food_days_critical > self.food_days_warning:
            raise ValueError("food_days_critical must be <= food_days_warning")
        if self.drink_days_critical > self.drink_days_warning:
            raise ValueError("drink_days_critical must be <= drink_days_warning")
        if self.stress_critical < self.stress_warning:
            raise ValueError("stress_critical must be >= stress_warning")
        return self


ModuleName = Literal["production", "stockpiles", "blueprints", "emergency"]
MODULES: tuple[ModuleName, ...] = ("production", "stockpiles", "blueprints", "emergency")


class AutomationModules(_Model):
    """Per-module ON/OFF switches. Owned by the user: Gemini patches cannot touch them."""

    production: bool = True
    stockpiles: bool = True
    blueprints: bool = False
    emergency: bool = True


class EmergencyPolicy(_Model):
    pause_on_critical: bool = True
    emergency_food_days: float = Field(default=15, ge=0, description="Days of food to order in a crisis")
    emergency_drink_days: float = Field(default=10, ge=0)
    notify_cooldown_s: int = Field(default=300, ge=0, description="Per-alarm-kind notification cooldown")


class GeminiBudget(_Model):
    """Protects the Free Tier. Gemini is never called automatically, only on user request."""

    max_payload_tokens: int = Field(default=2000, ge=200, le=8000)
    max_requests_per_minute: int = Field(default=5, ge=1)
    max_requests_per_day: int = Field(default=100, ge=1)
    analysis_cooldown_s: int = Field(default=120, ge=0, description="Cooldown for alarm analyses")


class AutopilotLimits(_Model):
    tick_seconds: float = Field(default=5.0, ge=1.0)
    max_actions_per_tick: int = Field(default=5, ge=1)
    max_concurrent_blueprints: int = Field(default=1, ge=1)


class FortressDoctrine(_Model):
    schema_version: int = SCHEMA_VERSION
    version: int = Field(default=1, ge=1)
    name: str = "Dottrina iniziale"
    strategy_notes: str = ""
    stock_targets: list[StockTarget] = Field(default_factory=list)
    stockpile_rules: list[StockpileRule] = Field(default_factory=list)
    blueprint_queue: list[BlueprintQueueItem] = Field(default_factory=list)
    alarms: AlarmThresholds = Field(default_factory=AlarmThresholds)
    modules: AutomationModules = Field(default_factory=AutomationModules)
    emergency: EmergencyPolicy = Field(default_factory=EmergencyPolicy)
    gemini: GeminiBudget = Field(default_factory=GeminiBudget)
    limits: AutopilotLimits = Field(default_factory=AutopilotLimits)
    approved_at: str | None = None

    @model_validator(mode="after")
    def _unique_keys(self) -> FortressDoctrine:
        for label, keys in (
            ("stock_targets.item", [t.item for t in self.stock_targets]),
            ("stockpile_rules.category", [r.category for r in self.stockpile_rules]),
            ("blueprint_queue.id", [b.id for b in self.blueprint_queue]),
        ):
            dupes = {k for k in keys if keys.count(k) > 1}
            if dupes:
                raise ValueError(f"duplicate {label}: {sorted(dupes)}")
        return self

    def target_for(self, item: str) -> StockTarget | None:
        return next((t for t in self.stock_targets if t.item == item), None)

    def rule_for(self, category: str) -> StockpileRule | None:
        return next((r for r in self.stockpile_rules if r.category == category), None)

    def compact_json(self) -> str:
        """Minified JSON without user-owned/implementation fields, for LLM payloads."""
        data = self.model_dump(
            mode="json", exclude={"schema_version", "approved_at", "modules", "gemini", "limits"}
        )
        return json.dumps(data, separators=(",", ":"), ensure_ascii=False)

    def approved(self) -> FortressDoctrine:
        return self.model_copy(
            update={
                "version": self.version + 1,
                "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )


class DoctrinePatch(_Model):
    """A Gemini proposal. Lists are upserted by key; objects replace the whole section."""

    name: str | None = None
    strategy_notes: str | None = None
    upsert_stock_targets: list[StockTarget] = Field(default_factory=list)
    remove_stock_targets: list[str] = Field(default_factory=list, description="Items to drop")
    upsert_stockpile_rules: list[StockpileRule] = Field(default_factory=list)
    remove_stockpile_rules: list[str] = Field(default_factory=list, description="Categories to drop")
    upsert_blueprints: list[BlueprintQueueItem] = Field(default_factory=list)
    remove_blueprints: list[str] = Field(default_factory=list, description="Queue ids to drop")
    alarms: AlarmThresholds | None = None
    emergency: EmergencyPolicy | None = None

    def is_empty(self) -> bool:
        return self == DoctrinePatch()


def _upsert(items: list[Any], new: list[Any], remove: list[str], key: str) -> list[Any]:
    by_key = {getattr(i, key): i for i in items}
    order = [getattr(i, key) for i in items]
    for n in new:
        k = getattr(n, key)
        if k not in by_key:
            order.append(k)
        by_key[k] = n
    return [by_key[k] for k in order if k not in set(remove)]


def apply_patch(doctrine: FortressDoctrine, patch: DoctrinePatch) -> FortressDoctrine:
    """Return a new, re-validated doctrine. The version bump happens on approval."""
    data = doctrine.model_dump()
    if patch.name is not None:
        data["name"] = patch.name
    if patch.strategy_notes is not None:
        data["strategy_notes"] = patch.strategy_notes
    data["stock_targets"] = [
        t.model_dump()
        for t in _upsert(
            doctrine.stock_targets, patch.upsert_stock_targets, patch.remove_stock_targets, "item"
        )
    ]
    data["stockpile_rules"] = [
        r.model_dump()
        for r in _upsert(
            doctrine.stockpile_rules,
            patch.upsert_stockpile_rules,
            patch.remove_stockpile_rules,
            "category",
        )
    ]
    data["blueprint_queue"] = [
        b.model_dump()
        for b in _upsert(doctrine.blueprint_queue, patch.upsert_blueprints, patch.remove_blueprints, "id")
    ]
    if patch.alarms is not None:
        data["alarms"] = patch.alarms.model_dump()
    if patch.emergency is not None:
        data["emergency"] = patch.emergency.model_dump()
    return FortressDoctrine.model_validate(data)


DiffKind = Literal["added", "removed", "changed"]


class DiffEntry(BaseModel):
    path: str
    kind: DiffKind
    old: Any = None
    new: Any = None


_KEYED_LISTS = {"stock_targets": "item", "stockpile_rules": "category", "blueprint_queue": "id"}
_IGNORED = {"version", "approved_at", "schema_version"}


def _diff(path: str, old: Any, new: Any, out: list[DiffEntry]) -> None:
    if isinstance(old, dict) and isinstance(new, dict):
        for k in list(old) + [k for k in new if k not in old]:
            sub = f"{path}.{k}" if path else k
            if not path and k in _IGNORED:
                continue
            if k not in new:
                out.append(DiffEntry(path=sub, kind="removed", old=old[k]))
            elif k not in old:
                out.append(DiffEntry(path=sub, kind="added", new=new[k]))
            elif not path and k in _KEYED_LISTS:
                key = _KEYED_LISTS[k]
                o = {i[key]: i for i in old[k]}
                n = {i[key]: i for i in new[k]}
                for ik in list(o) + [x for x in n if x not in o]:
                    item_path = f"{k}[{ik}]"
                    if ik not in n:
                        out.append(DiffEntry(path=item_path, kind="removed", old=o[ik]))
                    elif ik not in o:
                        out.append(DiffEntry(path=item_path, kind="added", new=n[ik]))
                    else:
                        _diff(item_path, o[ik], n[ik], out)
            else:
                _diff(sub, old[k], new[k], out)
    elif old != new:
        out.append(DiffEntry(path=path, kind="changed", old=old, new=new))


def diff_doctrines(old: FortressDoctrine, new: FortressDoctrine) -> list[DiffEntry]:
    out: list[DiffEntry] = []
    _diff("", old.model_dump(mode="json"), new.model_dump(mode="json"), out)
    return out


def default_doctrine() -> FortressDoctrine:
    return FortressDoctrine(
        name="Avamposto sostenibile",
        strategy_notes="Priorità: cibo e birra stabili, magazzini ordinati, camere per i migranti.",
        stock_targets=[
            StockTarget(item="drink", job="BrewDrink", min=60, target=120, max=250, yield_per_job=5, batch=2),
            StockTarget(item="prepared_meals", job="PrepareMeal", min=40, target=100, max=200, yield_per_job=4, batch=2),
            StockTarget(item="beds", job="ConstructBed", min=2, target=6, max=20, batch=2),
            StockTarget(item="doors", job="ConstructDoor", min=2, target=6, max=20, batch=2),
            StockTarget(item="tables", job="MakeTable", min=9, target=12, max=20, batch=3),
            StockTarget(item="chairs", job="MakeChair", min=9, target=12, max=20, batch=3),
            StockTarget(item="barrels", job="MakeBarrel", min=5, target=15, max=40, batch=5),
            StockTarget(item="bins", job="MakeBin", min=3, target=10, max=30, batch=5),
            StockTarget(item="blocks", job="ConstructBlocks", min=20, target=60, max=150, yield_per_job=4, batch=5),
        ],
        stockpile_rules=[
            StockpileRule(category="food", max_fill_pct=85, container_job="MakeBarrel", auto_expand_cells=10),
            StockpileRule(category="drink", max_fill_pct=85, container_job="MakeBarrel"),
            StockpileRule(category="wood", max_fill_pct=90, link_to_workshops=["Carpenter"]),
            StockpileRule(category="stone", max_fill_pct=90, auto_expand_cells=20, link_to_workshops=["Mason"]),
            StockpileRule(category="furniture", max_fill_pct=80, container_job="MakeBin", auto_expand_cells=6),
        ],
        blueprint_queue=[
            BlueprintQueueItem(id="bedrooms-1", blueprint="bedrooms_3x3", zone_id="Z1", priority=80),
            BlueprintQueueItem(
                id="workshops-1",
                blueprint="workshops_core",
                zone_id="Z2",
                priority=60,
                preconditions=BlueprintPreconditions(min_stocks=[StockRequirement(item="blocks", min=20)]),
            ),
            BlueprintQueueItem(
                id="dining-1",
                blueprint="dining_hall",
                zone_id="Z3",
                priority=40,
                preconditions=BlueprintPreconditions(min_population=12, after=["workshops-1"]),
            ),
        ],
    )


class DoctrineStore:
    """Persists the approved doctrine plus a numbered history of every approval."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "doctrine_approved.json"
        self.history_dir = self.data_dir / "history"

    def load(self) -> FortressDoctrine:
        if self.path.exists():
            return FortressDoctrine.model_validate_json(self.path.read_text(encoding="utf-8"))
        return default_doctrine()

    def save(self, doctrine: FortressDoctrine) -> None:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        payload = doctrine.model_dump_json(indent=2)
        self.path.write_text(payload, encoding="utf-8")
        (self.history_dir / f"doctrine_v{doctrine.version:04d}.json").write_text(payload, encoding="utf-8")
