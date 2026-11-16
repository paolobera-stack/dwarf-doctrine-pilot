"""Fortress state snapshot shared by every bridge (mock or real DFHack).

All bridges must return exactly this shape, so the autopilot and the GUI never
know which backend they are talking to.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SEASONS = ("spring", "summer", "autumn", "winter")
DAYS_PER_YEAR = 336

ITEM_CATEGORY: dict[str, str] = {
    "prepared_meals": "food",
    "plants": "food",
    "meat": "food",
    "drink": "drink",
    "wood": "wood",
    "stone": "stone",
    "blocks": "bars_blocks",
    "iron_bars": "bars_blocks",
    "beds": "furniture",
    "barrels": "furniture",
    "bins": "furniture",
    "tables": "furniture",
    "chairs": "furniture",
    "doors": "furniture",
}
EDIBLE_ITEMS = ("prepared_meals", "plants", "meat")

# Work-order jobs understood by every bridge (mock recipes use the same names).
KNOWN_JOBS = (
    "BrewDrink",
    "PrepareMeal",
    "ConstructBed",
    "MakeBarrel",
    "MakeBin",
    "MakeTable",
    "MakeChair",
    "ConstructDoor",
    "ConstructBlocks",
    "SmeltIron",
)

LABORS = (
    "mining",
    "woodcutting",
    "carpentry",
    "masonry",
    "brewing",
    "cooking",
    "farming",
    "fishing",
    "hunting",
    "smithing",
    "hauling",
)

OrderStatus = Literal["queued", "active", "done", "cancelled"]
OrderSource = Literal["autopilot", "user", "emergency"]
BlueprintStatus = Literal["queued", "digging", "building", "done"]
Severity = Literal["info", "warning", "critical"]


class _State(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GameDate(_State):
    year: int = 1
    day_of_year: int = 0

    @property
    def season(self) -> str:
        return SEASONS[min(self.day_of_year // (DAYS_PER_YEAR // 4), 3)]

    @property
    def day_of_season(self) -> int:
        return self.day_of_year % (DAYS_PER_YEAR // 4) + 1


class WorkOrder(_State):
    id: int
    job: str
    item: str
    quantity: int
    remaining: int
    status: OrderStatus = "queued"
    source: OrderSource = "user"


class Stockpile(_State):
    id: int
    name: str
    category: str
    capacity: int
    used: int
    links: list[str] = Field(default_factory=list)

    @property
    def fill_pct(self) -> int:
        return round(100 * self.used / self.capacity) if self.capacity else 100


class Workshop(_State):
    name: str
    count: int = 1


class Zone(_State):
    id: str
    name: str
    x: int
    y: int
    z: int
    w: int
    h: int
    assigned: str | None = None


class BlueprintJob(_State):
    id: str
    blueprint: str
    zone_id: str
    status: BlueprintStatus = "queued"
    progress: float = 0.0


class Dwarf(_State):
    id: int
    name: str
    profession: str
    stress: int = 0
    activity: str = "Idle"
    labors: list[str] = Field(default_factory=list)
    skills: dict[str, int] = Field(default_factory=dict)

    @property
    def idle(self) -> bool:
        return self.activity == "Idle"


class GameEvent(_State):
    id: int
    kind: str
    severity: Severity
    message: str
    day: int
    active: bool = True


class ConsumptionRates(_State):
    food_per_dwarf_day: float = 0.35
    drink_per_dwarf_day: float = 0.4


class FortressState(_State):
    fortress_name: str = "Unnamed"
    date: GameDate = Field(default_factory=GameDate)
    tick: int = 0
    paused: bool = False
    stocks: dict[str, int] = Field(default_factory=dict)
    ground_items: dict[str, int] = Field(default_factory=dict)
    work_orders: list[WorkOrder] = Field(default_factory=list)
    stockpiles: list[Stockpile] = Field(default_factory=list)
    workshops: list[Workshop] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    blueprints: list[BlueprintJob] = Field(default_factory=list)
    dwarves: list[Dwarf] = Field(default_factory=list)
    events: list[GameEvent] = Field(default_factory=list)
    rates: ConsumptionRates = Field(default_factory=ConsumptionRates)

    @property
    def population(self) -> int:
        return len(self.dwarves)

    def stock(self, item: str) -> int:
        return self.stocks.get(item, 0)

    def food_total(self) -> int:
        return sum(self.stock(i) for i in EDIBLE_ITEMS)

    def food_days(self) -> float:
        need = self.population * self.rates.food_per_dwarf_day
        return self.food_total() / need if need else float("inf")

    def drink_days(self) -> float:
        need = self.population * self.rates.drink_per_dwarf_day
        return self.stock("drink") / need if need else float("inf")

    def pending_for(self, item: str, sources: tuple[str, ...] = ("autopilot", "user", "emergency")) -> int:
        return sum(
            o.remaining
            for o in self.work_orders
            if o.item == item and o.status in ("queued", "active") and o.source in sources
        )

    def total_ground_items(self) -> int:
        return sum(self.ground_items.values())

    def active_events(self) -> list[GameEvent]:
        return [e for e in self.events if e.active]
