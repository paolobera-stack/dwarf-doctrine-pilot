"""Map the raw export of `lua/ddp_state.lua` onto the shared FortressState.

The Lua side only reports DF facts (enum names, tile counts); every DF→DDP
naming decision lives here so it is unit-testable without the game.
"""

from __future__ import annotations

import re
import zlib
from typing import Any

from dfhack_bridge.protocol import (
    DAYS_PER_YEAR,
    Dwarf,
    FortressState,
    GameDate,
    GameEvent,
    Stockpile,
    WorkOrder,
    Workshop,
    Zone,
)

SCHEMA = 1
TICKS_PER_DAY = 1200

STOCK_ITEMS: dict[str, tuple[str, ...]] = {
    "drink": ("DRINK",),
    "prepared_meals": ("FOOD",),
    "plants": ("PLANT", "PLANT_GROWTH"),
    "meat": ("MEAT", "FISH"),
    "wood": ("WOOD",),
    "stone": ("BOULDER",),
    "blocks": ("BLOCKS",),
    "beds": ("BED",),
    "barrels": ("BARREL",),
    "bins": ("BIN",),
    "tables": ("TABLE",),
    "chairs": ("CHAIR",),
    "doors": ("DOOR",),
}
BAR_ITEMS = {"iron_bars": "INORGANIC:IRON"}
ITEM_NAME_BY_TYPE = {t: item for item, types in STOCK_ITEMS.items() for t in types}

# DDP job -> manager order in the format of DFHack's order library (data/orders/*.json).
# Furniture that can be either: wood for beds/barrels/bins, rock for the mason's goods.
ORDER_SPECS: dict[str, dict[str, Any]] = {
    "BrewDrink": {"job": "CustomReaction", "reaction": "BREW_DRINK_FROM_PLANT"},
    "PrepareMeal": {"job": "PrepareMeal", "meal_ingredients": 2},  # easy meal: 2 ingredients
    "ConstructBed": {"job": "ConstructBed", "material_category": ["wood"]},
    "MakeBarrel": {"job": "MakeBarrel", "material_category": ["wood"]},
    "MakeBin": {"job": "ConstructBin", "material_category": ["wood"]},
    "MakeTable": {"job": "ConstructTable", "material": "INORGANIC"},
    "MakeChair": {"job": "ConstructThrone", "material": "INORGANIC"},
    "ConstructDoor": {"job": "ConstructDoor", "material": "INORGANIC"},
    "ConstructBlocks": {"job": "ConstructBlocks", "material": "INORGANIC"},
    "SmeltIron": {"job": "SmeltOre", "material": "INORGANIC:HEMATITE"},
}


def fortress_key(raw: dict[str, Any]) -> str:
    """Identifies one fortress across sessions: save folder + site id."""
    return f"{raw.get('save_dir') or '?'}#{raw.get('site_id', -1)}"


# DF job (job_type, reaction) -> DDP job; DDP job -> stock item it produces.
DF_JOBS: dict[tuple[str, str], str] = {
    ("CustomReaction", "BREW_DRINK_FROM_PLANT"): "BrewDrink",
    ("CustomReaction", "BREW_DRINK_FROM_PLANT_GROWTH"): "BrewDrink",
    ("PrepareMeal", ""): "PrepareMeal",
    ("ConstructBed", ""): "ConstructBed",
    ("MakeBarrel", ""): "MakeBarrel",
    ("ConstructBin", ""): "MakeBin",
    ("ConstructTable", ""): "MakeTable",
    ("ConstructThrone", ""): "MakeChair",
    ("ConstructDoor", ""): "ConstructDoor",
    ("ConstructBlocks", ""): "ConstructBlocks",
    ("SmeltOre", ""): "SmeltIron",
}
JOB_ITEM = {
    "BrewDrink": "drink",
    "PrepareMeal": "prepared_meals",
    "ConstructBed": "beds",
    "MakeBarrel": "barrels",
    "MakeBin": "bins",
    "MakeTable": "tables",
    "MakeChair": "chairs",
    "ConstructDoor": "doors",
    "ConstructBlocks": "blocks",
    "SmeltIron": "iron_bars",
}

WORKSHOP_NAMES = {"Carpenters": "Carpenter", "Masons": "Mason", "Craftsdwarfs": "Craftsdwarf",
                  "Farmers": "Farmer", "MetalsmithsForge": "Forge", "MagmaForge": "Forge",
                  "MagmaSmelter": "Smelter"}

PILE_CATEGORIES = {"food", "furniture", "wood", "stone", "bars_blocks"}

LABOR_MAP = {
    "MINE": "mining", "CUTWOOD": "woodcutting", "CARPENTER": "carpentry", "MASON": "masonry",
    "BREWER": "brewing", "COOK": "cooking", "PLANT": "farming", "FISH": "fishing", "HUNT": "hunting",
    "FORGE_WEAPON": "smithing", "FORGE_ARMOR": "smithing", "FORGE_FURNITURE": "smithing",
    "METAL_CRAFT": "smithing", "HAUL_STONE": "hauling", "HAUL_WOOD": "hauling", "HAUL_ITEM": "hauling",
    "HAUL_FOOD": "hauling", "HAUL_FURNITURE": "hauling",
}
SKILL_MAP = {
    "MINING": "mining", "WOODCUTTING": "woodcutting", "CARPENTRY": "carpentry", "MASONRY": "masonry",
    "BREWING": "brewing", "COOK": "cooking", "PLANT": "farming", "FISH": "fishing", "SNEAK": "hunting",
    "FORGE_WEAPON": "smithing", "FORGE_ARMOR": "smithing", "FORGE_FURNITURE": "smithing",
}

# Same tantrum-spiral rule as the mock: >= max(2, pop/4) citizens above this raw DF stress.
TANTRUM_STRESS = 100_000

ZONE_NAME = re.compile(r"^DDP[\s:_-]*(\S+)\s*(.*)$", re.IGNORECASE)


class RawStateError(ValueError):
    """The exporter output is unusable (wrong schema or a required section failed)."""


def _workshop_name(raw: dict[str, Any]) -> str:
    return WORKSHOP_NAMES.get(raw.get("type", ""), raw.get("type", "?"))


def _pile_category(raw: dict[str, Any]) -> str:
    flags = [f for f in raw.get("flags", []) if f]
    if raw.get("drink_only"):
        return "drink"
    if len(flags) == 1:
        return flags[0]
    return "mixed" if flags else "none"


def _event(kind: str, severity: str, message: str, day: int) -> GameEvent:
    return GameEvent(id=zlib.crc32(kind.encode()) & 0x7FFFFFFF, kind=kind, severity=severity,
                     message=message, day=day)


def parse_zone_name(name: str) -> tuple[str, str] | None:
    """`DDP Z1 Camere` -> ("Z1", "Camere"); names without the DDP prefix are ignored."""
    m = ZONE_NAME.match(name.strip())
    if not m:
        return None
    return m.group(1).upper(), (m.group(2) or m.group(1)).strip()


def raw_to_state(raw: dict[str, Any]) -> FortressState:
    if raw.get("schema") != SCHEMA:
        raise RawStateError(f"unsupported ddp_state schema {raw.get('schema')!r} (expected {SCHEMA})")
    failed = {e.split(":", 1)[0] for e in raw.get("errors", [])}
    if "meta" in failed:
        raise RawStateError("; ".join(raw["errors"]))

    items: dict[str, int] = raw.get("items") or {}
    bars: dict[str, int] = raw.get("bars") or {}
    stocks = {item: sum(int(items.get(t, 0)) for t in types) for item, types in STOCK_ITEMS.items()}
    stocks.update({item: int(bars.get(tok, 0)) for item, tok in BAR_ITEMS.items()})

    ground: dict[str, int] = {}
    for tname, n in (raw.get("ground") or {}).items():
        key = ITEM_NAME_BY_TYPE.get(tname, tname.lower())
        ground[key] = ground.get(key, 0) + int(n)

    ws_raw = raw.get("workshops") or []
    ws_by_id = {w["id"]: _workshop_name(w) for w in ws_raw}
    counts: dict[str, int] = {}
    for w in ws_raw:
        if w.get("active", True):
            counts[_workshop_name(w)] = counts.get(_workshop_name(w), 0) + 1
    workshops = [Workshop(name=n, count=c) for n, c in sorted(counts.items())]

    stockpiles = []
    for p in raw.get("stockpiles") or []:
        links = {ws_by_id[i] for i in p.get("give_to_workshops", []) + p.get("take_from_workshops", [])
                 if i in ws_by_id}
        stockpiles.append(Stockpile(
            id=p["id"], name=p.get("name") or f"Stockpile #{p.get('number', p['id'])}",
            category=_pile_category(p), capacity=int(p.get("tiles", 0)), used=int(p.get("used_tiles", 0)),
            links=sorted(links),
        ))

    zones = []
    for z in raw.get("zones") or []:
        parsed = parse_zone_name(z.get("name", ""))
        if parsed:
            zid, zname = parsed
            zones.append(Zone(id=zid, name=zname, x=z["x1"], y=z["y1"], z=z["z"],
                              w=z["x2"] - z["x1"] + 1, h=z["y2"] - z["y1"] + 1))

    orders = []
    for o in raw.get("orders") or []:
        key = (o.get("job_type", ""), o.get("reaction", "") if o.get("job_type") == "CustomReaction" else "")
        job = DF_JOBS.get(key) or o.get("reaction") or o.get("job_type", "?")
        left, total = int(o.get("amount_left", 0)), int(o.get("amount_total", 0))
        status = "done" if left <= 0 else ("active" if o.get("active") else "queued")
        orders.append(WorkOrder(id=o["id"], job=job, item=JOB_ITEM.get(job, ""), quantity=total,
                                remaining=max(left, 0), status=status, source="user"))

    dwarves = []
    for u in raw.get("units") or []:
        skills: dict[str, int] = {}
        for s, lvl in (u.get("skills") or {}).items():
            if s in SKILL_MAP:
                skills[SKILL_MAP[s]] = max(skills.get(SKILL_MAP[s], 0), int(lvl))
        labors = sorted({LABOR_MAP[lab] for lab in u.get("labors", []) if lab in LABOR_MAP})
        dwarves.append(Dwarf(id=u["id"], name=u.get("name") or f"#{u['id']}", profession=u.get("profession", ""),
                             stress=max(0, int(u.get("stress", 0))), activity=u.get("job") or "Idle",
                             labors=labors, skills=skills))

    tick = int(raw.get("year_tick", 0))
    day = min(tick // TICKS_PER_DAY, DAYS_PER_YEAR - 1)
    events: list[GameEvent] = []
    threats = raw.get("threats") or {}
    if threats.get("invaders"):
        events.append(_event("siege", "critical", f"{threats['invaders']} invaders on the map", day))
    if threats.get("great_dangers"):
        events.append(_event("forgotten_beast", "critical", f"{threats['great_dangers']} great dangers visible", day))
    if threats.get("merchants"):
        events.append(_event("caravan", "info", "Merchants at the depot", day))
    stressed = sum(1 for d in dwarves if d.stress > TANTRUM_STRESS)
    if dwarves and stressed >= max(2, len(dwarves) // 4):
        events.append(_event("tantrum_spiral", "critical", f"{stressed} dwarves near breaking point", day))

    return FortressState(
        fortress_name=raw.get("fortress_name") or "Fortress",
        date=GameDate(year=int(raw.get("year", 1)), day_of_year=day),
        tick=int(raw.get("year", 0)) * DAYS_PER_YEAR + day,  # game days, like the mock
        paused=bool(raw.get("paused", False)),
        stocks=stocks, ground_items=ground, work_orders=orders, stockpiles=stockpiles,
        workshops=workshops, zones=zones, dwarves=dwarves, events=events,
    )
