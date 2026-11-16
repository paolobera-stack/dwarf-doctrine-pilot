"""Custom QAbstractTableModels for the four dense, colour-coded tabs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QBrush, QColor

from core.autopilot import blueprint_readiness
from core.doctrine import FortressDoctrine
from core.i18n import tr
from dfhack_bridge.protocol import ITEM_CATEGORY, LABORS, FortressState
from ui import theme

BAR_ROLE = Qt.ItemDataRole.UserRole + 1  # float 0..1 painted as an in-cell bar
SORT_ROLE = Qt.ItemDataRole.UserRole + 2

_Index = QModelIndex | QPersistentModelIndex


@dataclass
class Column:
    header: str  # i18n key (or literal)
    value: Callable[[Any], Any]
    color: Callable[[Any], QColor | None] | None = None
    bar: Callable[[Any], float | None] | None = None
    sort: Callable[[Any], Any] | None = None
    tooltip: Callable[[Any], str] | None = None
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft
    width: int | None = None


class RowModel(QAbstractTableModel):
    """Rows are plain dicts/objects; columns describe display, colour, bars and sorting."""

    columns: list[Column] = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[Any] = []

    # --- feeding
    def set_rows(self, rows: list[Any]) -> None:
        if len(rows) == len(self._rows):
            self._rows = rows
            if rows:
                self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, len(self.columns) - 1))
        else:
            self.beginResetModel()
            self._rows = rows
            self.endResetModel()

    def row_data(self, row: int) -> Any:
        return self._rows[row]

    def retranslate(self) -> None:
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.columns) - 1)

    # --- Qt API
    def rowCount(self, parent: _Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: _Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(self.columns[section].header)
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.ToolTipRole:
            return tr(self.columns[section].header)
        return None

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = self._rows[index.row()], self.columns[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            v = col.value(row)
            return "" if v is None else str(v)
        if role == SORT_ROLE:
            return (col.sort or col.value)(row)
        if role == Qt.ItemDataRole.BackgroundRole and col.color:
            c = col.color(row)
            return QBrush(c) if c else None
        if role == Qt.ItemDataRole.ForegroundRole and col.color:
            c = col.color(row)
            return QBrush(theme.text_for(c)) if c else None
        if role == BAR_ROLE and col.bar:
            return col.bar(row)
        if role == Qt.ItemDataRole.ToolTipRole and col.tooltip:
            return col.tooltip(row)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(col.align | Qt.AlignmentFlag.AlignVCenter)
        return None


R = Qt.AlignmentFlag.AlignRight
C = Qt.AlignmentFlag.AlignHCenter


def _status_color(key: str) -> QColor | None:
    return theme.STATUS_COLORS.get(key)


# ----------------------------------------------------------------------------- tab 1
def stock_rows(state: FortressState, doctrine: FortressDoctrine) -> list[dict]:
    rows = []
    tracked = {t.item: t for t in doctrine.stock_targets}
    for item in list(tracked) + sorted(i for i in state.stocks if i not in tracked):
        t = tracked.get(item)
        cur = state.stock(item)
        pending = state.pending_for(item) * (t.yield_per_job if t else 1)
        if t is None:
            status = "untracked"
        elif cur == 0 and t.min > 0:
            status = "critical"
        elif cur + pending < t.min:
            status = "low"
        elif cur > t.max:
            status = "excess"
        else:
            status = "ok"
        rows.append({
            "item": item, "cur": cur, "pending": pending, "status": status, "enabled": t.enabled if t else None,
            "min": t.min if t else None, "target": t.target if t else None, "max": t.max if t else None,
            "job": t.job if t else "", "cov": (cur / t.target) if t and t.target else None,
            "category": ITEM_CATEGORY.get(item, "?"),
        })
    return rows


class StockModel(RowModel):
    columns = [
        Column("col.item", lambda r: r["item"], tooltip=lambda r: f'{r["category"]} · {r["job"]}'),
        Column("col.current", lambda r: r["cur"], align=R),
        Column("col.min", lambda r: r["min"], align=R),
        Column("col.target", lambda r: r["target"], align=R),
        Column("col.max", lambda r: r["max"], align=R),
        Column("col.on_order", lambda r: r["pending"] or "", align=R, sort=lambda r: r["pending"]),
        Column("col.coverage", lambda r: f'{r["cov"]:.0%}' if r["cov"] is not None else "",
               bar=lambda r: r["cov"], sort=lambda r: r["cov"] or -1, align=R),
        Column("col.status", lambda r: tr(f'stock.{r["status"]}'), color=lambda r: _status_color(r["status"]),
               sort=lambda r: ["critical", "low", "excess", "ok", "untracked"].index(r["status"])),
    ]


class OrderModel(RowModel):
    columns = [
        Column("col.id", lambda o: o.id, align=R),
        Column("col.job", lambda o: o.job),
        Column("col.item", lambda o: o.item),
        Column("col.quantity", lambda o: o.quantity, align=R),
        Column("col.remaining", lambda o: o.remaining, align=R,
               bar=lambda o: 1 - o.remaining / o.quantity if o.quantity else 1),
        Column("col.source", lambda o: tr(f"source.{o.source}"), color=lambda o: _status_color(o.source)),
        Column("col.status", lambda o: tr(f"order.{o.status}"), color=lambda o: _status_color(o.status)),
    ]


# ----------------------------------------------------------------------------- tab 2
def stockpile_rows(state: FortressState, doctrine: FortressDoctrine) -> list[dict]:
    rows = []
    for s in state.stockpiles:
        rule = doctrine.rule_for(s.category)
        rows.append({"pile": s, "rule": rule, "ground": state.ground_items.get(s.category, 0)})
    return rows


def _rule_text(rule) -> str:
    if rule is None:
        return tr("rule.none")
    parts = [tr("rule.summary", pct=rule.max_fill_pct)]
    if rule.auto_expand_cells:
        parts.append(tr("rule.expand", n=rule.auto_expand_cells))
    if rule.container_job:
        parts.append(tr("rule.container", job=rule.container_job))
    return " · ".join(parts)


def _fill_color(r: dict) -> QColor:
    limit = r["rule"].max_fill_pct if r["rule"] else 90
    pct = r["pile"].fill_pct
    return theme.ramp(pct / max(limit, 1) * 0.5) if pct <= limit else theme.BAD


class StockpileModel(RowModel):
    columns = [
        Column("col.id", lambda r: r["pile"].id, align=R),
        Column("col.name", lambda r: r["pile"].name),
        Column("col.category", lambda r: r["pile"].category),
        Column("col.used", lambda r: f'{r["pile"].used}/{r["pile"].capacity}', align=R, sort=lambda r: r["pile"].used),
        Column("col.fill", lambda r: f'{r["pile"].fill_pct}%', color=_fill_color,
               bar=lambda r: r["pile"].fill_pct / 100, sort=lambda r: r["pile"].fill_pct, align=R),
        Column("col.ground", lambda r: r["ground"] or "", align=R, sort=lambda r: r["ground"],
               color=lambda r: theme.ramp(min(r["ground"] / 100, 1)) if r["ground"] else None),
        Column("col.links", lambda r: ", ".join(r["pile"].links) or "—", sort=lambda r: len(r["pile"].links)),
        Column("col.rule", lambda r: _rule_text(r["rule"])),
    ]


# ----------------------------------------------------------------------------- tab 3
def blueprint_rows(state: FortressState, doctrine: FortressDoctrine) -> list[dict]:
    rows = []
    queued = set()
    for item in sorted(doctrine.blueprint_queue, key=lambda b: -b.priority):
        status, reasons = blueprint_readiness(item, state)
        live = next((b for b in state.blueprints if b.id == item.id), None)
        queued.add(item.id)
        rows.append({"id": item.id, "bp": item.blueprint, "zone": item.zone_id, "prio": item.priority,
                     "status": status, "progress": live.progress if live else 0.0, "item": item, "reasons": reasons})
    for live in state.blueprints:  # jobs in game but no longer in the doctrine
        if live.id not in queued:
            rows.append({"id": live.id, "bp": live.blueprint, "zone": live.zone_id, "prio": None,
                         "status": live.status, "progress": live.progress, "item": None, "reasons": []})
    return rows


def _pre_text(r: dict) -> str:
    if r["reasons"]:
        return "; ".join(r["reasons"])
    item = r["item"]
    if item is None:
        return ""
    pre = item.preconditions
    parts = []
    if pre.min_population:
        parts.append(tr("pre.population", n=pre.min_population))
    if pre.after:
        parts.append(tr("pre.after", ids=", ".join(pre.after)))
    parts += [f"{s.item} ≥ {s.min}" for s in pre.min_stocks]
    return ", ".join(parts) or tr("pre.none")


class BlueprintModel(RowModel):
    columns = [
        Column("col.queue_id", lambda r: r["id"]),
        Column("col.blueprint", lambda r: r["bp"]),
        Column("col.zone", lambda r: r["zone"]),
        Column("col.priority", lambda r: r["prio"], align=R, sort=lambda r: r["prio"] or -1),
        Column("col.status", lambda r: tr(f'bp.{r["status"]}'), color=lambda r: _status_color(r["status"])),
        Column("col.progress", lambda r: f'{r["progress"]:.0%}', bar=lambda r: r["progress"],
               sort=lambda r: r["progress"], align=R),
        Column("col.preconditions", _pre_text,
               color=lambda r: theme.WARN if r["reasons"] else None),
    ]


class ZoneModel(RowModel):
    columns = [
        Column("col.id", lambda z: z.id),
        Column("col.name", lambda z: z.name),
        Column("col.size", lambda z: f"{z.w}×{z.h} @ ({z.x},{z.y},{z.z})", sort=lambda z: z.w * z.h),
        Column("col.assigned", lambda z: z.assigned or tr("bp.free"),
               color=lambda z: theme.INFO if z.assigned else theme.OK),
    ]


# ----------------------------------------------------------------------------- tab 4
LABOR_ABBR = {lab: lab[:3].capitalize() for lab in LABORS}


def _stress_color(d) -> QColor:
    return theme.ramp(min(max(d.stress, 0) / 100_000, 1))


class DwarfModel(RowModel):
    columns = [
        Column("col.name", lambda d: d.name),
        Column("col.profession", lambda d: d.profession),
        Column("col.stress", lambda d: f"{d.stress:,}".replace(",", "."), color=_stress_color,
               sort=lambda d: d.stress, align=R),
        Column("col.activity", lambda d: d.activity, color=lambda d: theme.WARN if d.idle else None),
    ] + [
        Column(
            LABOR_ABBR[lab],
            (lambda lab: lambda d: d.skills.get(lab, "") if d.skills.get(lab) else ("·" if lab in d.labors else ""))(lab),
            color=(lambda lab: lambda d: theme.skill_color(d.skills.get(lab, 0), lab in d.labors))(lab),
            sort=(lambda lab: lambda d: (lab in d.labors, d.skills.get(lab, 0)))(lab),
            tooltip=(lambda lab: lambda d: f"{lab}: {d.skills.get(lab, 0)}{' ✔' if lab in d.labors else ''}")(lab),
            align=C, width=34,
        )
        for lab in LABORS
    ]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.ToolTipRole
                and section >= 4):
            return LABORS[section - 4]
        return super().headerData(section, orientation, role)
