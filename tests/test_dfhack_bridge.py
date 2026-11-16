import json
import subprocess
from pathlib import Path

import pytest

from dfhack_bridge.base import BridgeError
from dfhack_bridge.dfhack_bridge import STATE_SCRIPT, DFHackBridge
from dfhack_bridge.dfhack_state import RawStateError, parse_zone_name, raw_to_state

FAKE_ENV = Path(__file__).parent / "fixtures" / "fake_dfhack.lua"


def run_exporter(setup: str = "") -> dict:
    lua53 = pytest.importorskip("lupa.lua53")
    lua = lua53.LuaRuntime()
    lua.execute(FAKE_ENV.read_text())
    lua.execute(setup)
    lua.execute("_ddp_out = {}; print = function(s) _ddp_out[#_ddp_out + 1] = s end")
    lua.execute(STATE_SCRIPT.read_text())
    out = lua.eval("_ddp_out")
    assert len(out) == 1, "exporter must print exactly one document"
    return json.loads(out[1])


@pytest.fixture(scope="module")
def raw() -> dict:
    return run_exporter()


def test_exporter_raw_document(raw):
    assert raw["schema"] == 1 and raw["errors"] == []
    assert raw["fortress_name"] == 'Fakefort "Test"'
    assert raw["items"]["DRINK"] == 20 and raw["items"]["MEAT"] == 2  # rotten excluded
    assert raw["items"]["BED"] == 1  # the built bed is not stock
    assert raw["bars"] == {"INORGANIC:IRON": 4, "INORGANIC:COPPER": 2}
    assert raw["ground"] == {"WOOD": 1, "BOULDER": 1}  # forbidden and stockpiled items excluded
    food, drink, wood = raw["stockpiles"]
    assert (food["tiles"], food["used_tiles"]) == (5, 1)
    assert drink["drink_only"] and not food["drink_only"]
    assert wood["give_to_workshops"] == [10]
    assert [z["name"] for z in raw["zones"]] == ["DDP Z1 Camere"]
    assert raw["threats"] == {"invaders": 1, "great_dangers": 0, "dangers": 1, "merchants": 1}


def test_exporter_isolates_failing_sections():
    raw = run_exporter("df.global.world.manager_orders = nil")
    assert len(raw["errors"]) == 1 and raw["errors"][0].startswith("orders:")
    state = raw_to_state(raw)
    assert state.work_orders == [] and state.population == 2


def test_raw_to_state(raw):
    s = raw_to_state(raw)
    assert s.fortress_name == 'Fakefort "Test"'
    assert (s.date.year, s.date.day_of_year, s.paused) == (105, 40, False)
    assert s.stocks["drink"] == 20 and s.stocks["meat"] == 2 and s.stocks["iron_bars"] == 4
    assert s.ground_items == {"wood": 1, "stone": 1}
    orders = {o.id: o for o in s.work_orders}
    assert (orders[7].job, orders[7].item, orders[7].remaining, orders[7].status) == ("BrewDrink", "drink", 4, "active")
    assert (orders[8].job, orders[8].status) == ("ConstructBed", "queued")
    assert (orders[9].job, orders[9].item, orders[9].status) == ("MakeCrafts", "", "done")
    assert s.pending_for("drink") == 4
    piles = {p.id: p for p in s.stockpiles}
    assert piles[100].name == "Stockpile #1" and piles[100].category == "food"
    assert piles[101].category == "drink" and piles[101].links == ["Still"]
    assert piles[102].category == "wood" and piles[102].links == ["Carpenter"]
    assert {w.name: w.count for w in s.workshops} == {"Carpenter": 1, "Still": 1, "Smelter": 1}  # kitchen unbuilt
    assert [(z.id, z.name, z.w, z.h, z.z) for z in s.zones] == [("Z1", "Camere", 10, 5, 3)]
    miner, brewer = s.dwarves
    assert (miner.activity, miner.labors, miner.skills) == ("Dig", ["hauling", "mining"], {"mining": 7})
    assert (brewer.activity, brewer.stress, brewer.skills) == ("Idle", 0, {"brewing": 5, "cooking": 2})
    assert {e.kind: e.severity for e in s.events} == {"siege": "critical", "caravan": "info"}


def test_tantrum_spiral_event(raw):
    raw = json.loads(json.dumps(raw))
    for u in raw["units"]:
        u["stress"] = 150_000
    assert "tantrum_spiral" in {e.kind for e in raw_to_state(raw).events}


def test_raw_to_state_rejects_bad_documents(raw):
    with pytest.raises(RawStateError):
        raw_to_state({**raw, "schema": 99})
    with pytest.raises(RawStateError):
        raw_to_state({**raw, "errors": ["meta: attempt to index a nil value"]})


@pytest.mark.parametrize("name,expected", [
    ("DDP Z1 Camere", ("Z1", "Camere")),
    ("ddp:z2", ("Z2", "z2")),
    ("DDP-Storage Magazzino nord", ("STORAGE", "Magazzino nord")),
    ("Pascolo", None),
])
def test_parse_zone_name(name, expected):
    assert parse_zone_name(name) == expected


class FakeRun:
    def __init__(self, raw: dict | None, returncode: int = 0, console: str = ""):
        self.raw, self.returncode, self.console, self.calls = raw, returncode, console, []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self.raw is not None:
            Path(cmd[-1]).write_text(json.dumps(self.raw), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, self.returncode, self.console, "")


def test_bridge_get_state_reads_temp_file(monkeypatch, raw):
    fake = FakeRun(raw, console="ddp_state ok 3ms errors=0")
    monkeypatch.setattr(subprocess, "run", fake)
    b = DFHackBridge(dfhack_run="/opt/df/dfhack-run")
    state = b.get_state()
    cmd = fake.calls[0]
    assert cmd[:3] == ["/opt/df/dfhack-run", "lua", "-f"] and cmd[3].endswith("ddp_state.lua")
    assert not Path(cmd[4]).exists()  # temp file cleaned up
    assert state.population == 2 and b.last_raw == raw


@pytest.mark.parametrize("fake,match", [
    (FakeRun(None, 1, "dfhack-run: could not connect to DFHack"), "could not connect"),
    (FakeRun(None, 0, "Lua error"), "no output|Lua error"),
    (FakeRun({"schema": 99}), "schema"),
])
def test_bridge_errors_are_bridge_errors(monkeypatch, fake, match):
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(BridgeError, match=match):
        DFHackBridge(dfhack_run="dfhack-run").get_state()


def test_bridge_missing_executable():
    with pytest.raises(BridgeError, match="DDP_DFHACK_RUN"):
        DFHackBridge(dfhack_run="/nonexistent/dfhack-run").get_state()


def test_dfhack_run_env_override(monkeypatch):
    monkeypatch.setenv("DDP_DFHACK_RUN", r"C:\Games\DF\dfhack-run.exe")
    assert DFHackBridge().dfhack_run == r"C:\Games\DF\dfhack-run.exe"
