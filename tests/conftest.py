import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import i18n  # noqa: E402


@pytest.fixture(autouse=True)
def italian():
    i18n.set_language("it")
    yield
    i18n.set_language("it")
