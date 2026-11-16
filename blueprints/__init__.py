"""Quickfort blueprint assets, shipped as package data."""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

BLUEPRINTS_DIR = Path(str(files(__name__)))

_HEADER = re.compile(r"^#(\w+)")
_LABEL = re.compile(r"label\(([^)]*)\)")


def load_catalog() -> dict[str, Any]:
    return json.loads(files(__name__).joinpath("catalog.json").read_text(encoding="utf-8"))


@dataclass
class Section:
    """One `#mode` section of a Quickfort CSV; cells are 0-based (x, y) offsets from the top-left."""

    mode: str
    label: str
    cells: list[tuple[int, int, str]] = field(default_factory=list)

    @property
    def size(self) -> tuple[int, int]:
        if not self.cells:
            return 0, 0
        return max(x for x, _, _ in self.cells) + 1, max(y for _, y, _ in self.cells) + 1


def parse_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    y = 0
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        head = _HEADER.match(row[0].strip())
        if head:
            label = _LABEL.search(row[0])
            sections.append(Section(head.group(1), label.group(1) if label else head.group(1)))
            y = 0
            continue
        if not sections:
            raise ValueError("blueprint data before the first #mode header")
        for x, cell in enumerate(row):
            cell = cell.strip()
            if cell and cell not in ("`", "#", "~"):
                sections[-1].cells.append((x, y, cell))
        y += 1
    return sections


def load_sections(blueprint: str, catalog: dict[str, Any] | None = None) -> list[Section]:
    spec = (catalog or load_catalog())[blueprint]
    return parse_sections(files(__name__).joinpath(spec["file"]).read_text(encoding="utf-8"))
