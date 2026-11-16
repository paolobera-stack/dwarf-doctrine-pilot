from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class AppConfig:
    language: str = field(default_factory=lambda: os.environ.get("DDP_LANG", "it"))
    gemini_api_key: str | None = field(
        default_factory=lambda: os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    )
    gemini_model: str = field(default_factory=lambda: os.environ.get("DDP_GEMINI_MODEL", "gemini-2.5-flash"))
    bridge: str = field(default_factory=lambda: os.environ.get("DDP_BRIDGE", "mock"))
    mock_host: str = "127.0.0.1"
    mock_port: int = field(default_factory=lambda: int(os.environ.get("DDP_MOCK_PORT", "5577")))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DDP_DATA_DIR", ROOT / "data")))
    blueprints_dir: Path = ROOT / "blueprints"
