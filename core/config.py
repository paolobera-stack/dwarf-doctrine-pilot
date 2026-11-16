from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal KEY=VALUE loader (gitignored .env); real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class AppConfig:
    language: str = field(default_factory=lambda: os.environ.get("DDP_LANG", "it"))
    gemini_api_key: str | None = field(
        default_factory=lambda: os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    )
    gemini_model: str = field(default_factory=lambda: os.environ.get("DDP_GEMINI_MODEL", "gemini-3.5-flash"))
    bridge: str = field(default_factory=lambda: os.environ.get("DDP_BRIDGE", "mock"))
    mock_host: str = "127.0.0.1"
    mock_port: int = field(default_factory=lambda: int(os.environ.get("DDP_MOCK_PORT", "5577")))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DDP_DATA_DIR", ROOT / "data")))
    blueprints_dir: Path = ROOT / "blueprints"
