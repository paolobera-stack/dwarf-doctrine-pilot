"""Reusable widgets: in-cell bar delegate and a dense, filterable, sortable table."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QLineEdit,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from ui import theme
from ui.models import BAR_ROLE, SORT_ROLE, RowModel


class BarDelegate(QStyledItemDelegate):
    """Paints a thin progress bar under the cell text when the model exposes BAR_ROLE."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        value = index.data(BAR_ROLE)
        if value is None:
            return super().paint(painter, option, index)
        value = max(0.0, float(value))
        painter.save()
        r = option.rect.adjusted(2, 2, -2, -2)
        painter.fillRect(r, QColor("#2a2c30"))
        color = theme.ramp(1 - min(value, 1.0)) if index.data(Qt.ItemDataRole.BackgroundRole) is None else \
            index.data(Qt.ItemDataRole.BackgroundRole).color()
        painter.fillRect(QRect(r.x(), r.y(), int(r.width() * min(value, 1.0)), r.height()), color)
        painter.setPen(theme.TEXT)
        painter.drawText(r.adjusted(3, 0, -3, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                         str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
        painter.restore()


class _Proxy(QSortFilterProxyModel):
    def lessThan(self, left, right) -> bool:
        a, b = left.data(SORT_ROLE), right.data(SORT_ROLE)
        try:
            return (a is None, a) < (b is None, b)
        except TypeError:
            return str(a) < str(b)


class DenseTable(QWidget):
    """Title + filter box + compact QTableView over a RowModel (via sort/filter proxy)."""

    def __init__(self, model: RowModel, title_key: str | None = None, parent=None):
        super().__init__(parent)
        self.model = model
        self.title_key = title_key
        self.proxy = _Proxy(self)
        self.proxy.setSourceModel(model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)

        self.title = QLabel()
        self.title.setStyleSheet("font-weight: bold; color: #ffd54f;")
        self.filter = QLineEdit()
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.proxy.setFilterFixedString)

        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setItemDelegate(BarDelegate(self.view))
        self.view.setSortingEnabled(True)
        self.view.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(20)
        hh = self.view.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        hh.setMinimumSectionSize(28)
        for i, col in enumerate(model.columns):
            if col.width:
                self.view.setColumnWidth(i, col.width)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        if title_key:
            lay.addWidget(self.title)
        lay.addWidget(self.filter)
        lay.addWidget(self.view)
        self.retranslate()
        self._sized = False

    def set_rows(self, rows) -> None:
        self.model.set_rows(rows)
        if not self._sized and rows:
            self._sized = True
            for i, col in enumerate(self.model.columns):
                if not col.width:
                    self.view.resizeColumnToContents(i)

    def retranslate(self) -> None:
        if self.title_key:
            self.title.setText(tr(self.title_key))
        self.filter.setPlaceholderText(tr("filter.placeholder"))
        self.model.retranslate()
