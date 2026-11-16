"""Quickfort blueprint assets, shipped as package data."""
from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

BLUEPRINTS_DIR = Path(str(files(__name__)))


def load_catalog() -> dict[str, Any]:
    return json.loads(files(__name__).joinpath("catalog.json").read_text(encoding="utf-8"))
