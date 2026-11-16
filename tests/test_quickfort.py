"""Step 2C: Quickfort blueprints on DFHack (dig, then build) with progress read from the zone."""
import json
import subprocess
from pathlib import Path

import pytest

from blueprints import load_catalog, load_sections, parse_sections
from core.alarms import Alarm
from core.autopilot import Autopilot, plan_blueprints
from core.doctrine import AlarmThresholds
from core.doctrine import default_doctrine as FortressDoctrine
from dfhack_bridge.base import BridgeError
from dfhack_bridge.dfhack_bridge import ACTIONS_SCRIPT, DFHackBridge
from dfhack_bridge.dfhack_state import blueprint_job
from dfhack_bridge.protocol import BlueprintJob, FortressState
from tests.test_dfhack_actions import FakeGame

W, H = 12, 10  # zone Z1, a bit larger than bedrooms_3x3 (11x9)


def zone(tiles_char="#", buildings=()):
    return {"id": 200, "name": "DDP Z1 Camere", "type": "Bedroom", "x1": 30, "y1": 40,
            "x2": 30 + W - 1, "y2": 40 + H - 1, "z": 3, "tiles": [tiles_char * W] * H,
            "buildings": list(buildings)}


class QuickfortGame(FakeGame):
    STAT = {"dig": "dig_designated", "build": "build_designated", "place": "place_designated"}

    def __init__(self):
        super().__init__()
        self.raw["zones"] = [zone()]

    def __call__(self, cmd, **kwargs):
        if cmd[3] == str(ACTIONS_SCRIPT):
            payload = json.loads(Path(cmd[4]).read_text())
            if payload["action"] == "apply_blueprint":
                self.actions.append(payload)
                stat = {"label": "designated", "value": len(payload["cells"])}
                Path(cmd[5]).write_text(json.dumps({"ok": True, "stats": {self.STAT[payload["mode"]]: stat}}))
                return subprocess.CompletedProcess(cmd, 0, "", "")
        return super().__call__(cmd, **kwargs)


@pytest.fixture
def game(monkeypatch):
    g = QuickfortGame()
    monkeypatch.setattr(subprocess, "run", g)
    return g


def bridge(tmp_path):
    return DFHackBridge(dfhack_run="dfhack-run", ledger_path=tmp_path / "dfhack_orders.json")


def job(b):
    return next(j for j in b.get_state().blueprints if j.id == "bedrooms-1")


# --------------------------------------------------------------------- blueprint files
def test_parse_sections():
    secs = parse_sections("#dig label(d) x\nd,`,d\n`,d\n#build start(1;1)\n`,b\n")
    assert [(s.mode, s.label, s.cells) for s in secs] == [
        ("dig", "d", [(0, 0, "d"), (2, 0, "d"), (1, 1, "d")]), ("build", "build", [(1, 0, "b")])]
    with pytest.raises(ValueError):
        parse_sections("d,d\n")


@pytest.mark.parametrize("name", sorted(load_catalog()))
def test_catalog_matches_csv(name):
    spec = load_catalog()[name]
    secs = load_sections(name)
    dig = [s for s in secs if s.mode == "dig"]
    assert dig and dig[0].size == tuple(spec["size"])
    assert len(dig[0].cells) == spec["dig_cells"]
    dug = {(x, y) for x, y, _ in dig[0].cells}
    for s in secs:
        if s.mode == "build":  # every building stands on dug floor, inside the blueprint
            for x, y, text in s.cells:
                w, h = (int(n) for n in text[text.index("(") + 1:-1].split("x")) if "(" in text else (1, 1)
                assert all((x + i, y + j) in dug for i in range(w) for j in range(h)), (name, text)


# --------------------------------------------------------------------- progress
def test_progress_from_tiles_and_buildings():
    secs = load_sections("bedrooms_3x3")
    entry = {"blueprint": "bedrooms_3x3", "zone_id": "Z1", "x": 30, "y": 40, "z": 3, "w": 11, "h": 9,
             "phase": "dig"}
    j = blueprint_job("b", entry, zone(), secs)
    assert (j.status, j.progress, j.awaiting_build) == ("digging", 0.0, False)
    half = zone()
    half["tiles"] = ["." * 6 + "#" * (W - 6)] * H
    assert 0 < blueprint_job("b", entry, half, secs).progress < 0.5
    j = blueprint_job("b", entry, zone("."), secs)
    assert (j.status, j.progress, j.awaiting_build) == ("digging", 0.5, True)
    entry["phase"] = "build"
    beds = [{"id": i, "type": "Bed", "x": 31 + 4 * i, "y": 40, "built": True} for i in range(3)]
    outside = [{"id": 9, "type": "Bed", "x": 41, "y": 40, "built": True}]  # zone column 12, outside 11x9
    planned = [{"id": 8, "type": "Door", "x": 31, "y": 43, "built": False}]
    j = blueprint_job("b", entry, zone(".", beds + outside + planned), secs)
    assert (j.status, j.progress) == ("building", 0.5 + 0.5 * 3 / 12)
    all_built = [{"id": i, "type": "X", "x": 30 + x, "y": 40 + y, "built": True}
                 for i, (x, y, _) in enumerate(secs[1].cells)]
    assert blueprint_job("b", entry, zone(".", all_built), secs).status == "done"
    entry["progress"] = 0.7
    assert (blueprint_job("b", entry, None, secs).status, blueprint_job("b", entry, None, secs).progress) == \
        ("building", 0.7)


# --------------------------------------------------------------------- bridge
def test_dig_then_build_then_done(game, tmp_path):
    b = bridge(tmp_path)
    b.enqueue_blueprint("bedrooms-1", "bedrooms_3x3", "Z1")
    dig = game.actions[-1]
    assert (dig["action"], dig["mode"], dig["pos"]) == ("apply_blueprint", "dig", {"x": 30, "y": 40, "z": 3})
    assert len(dig["cells"]) == 71 and {"x": 1, "y": 3, "text": "d"} in dig["cells"]
    j = job(b)
    assert (j.status, j.awaiting_build) == ("digging", False)
    assert b.get_state().zones[0].assigned == "bedrooms-1"

    game.raw["zones"][0]["tiles"] = ["." * W] * H
    assert job(b).awaiting_build
    b.advance_blueprint("bedrooms-1")
    assert [(a["mode"], len(a["cells"])) for a in game.actions[1:]] == [("build", 12)]
    assert (job(b).status, job(b).progress) == ("building", 0.5)
    with pytest.raises(BridgeError, match="not waiting"):
        b.advance_blueprint("bedrooms-1")

    secs = load_sections("bedrooms_3x3")
    game.raw["zones"][0]["buildings"] = [{"id": i, "type": "Bed", "x": 30 + x, "y": 40 + y, "built": True}
                                         for i, (x, y, _) in enumerate(secs[1].cells)]
    assert job(b).status == "done"
    # done is remembered even if the zone is later removed, also across restarts
    game.raw["zones"] = []
    assert job(bridge(tmp_path)).status == "done"
    saved = json.loads((tmp_path / "dfhack_blueprints.json").read_text())
    assert saved["region1#42"]["bedrooms-1"]["phase"] == "done"


def test_place_only_phase_finishes_immediately(game, tmp_path):
    game.raw["zones"][0]["name"] = "DDP Z4 Magazzini"
    b = bridge(tmp_path)
    b.enqueue_blueprint("stock-1", "stockpiles_core", "Z4")
    game.raw["zones"][0]["tiles"] = ["." * W] * H
    b.advance_blueprint("stock-1")
    assert game.actions[-1]["mode"] == "place"
    assert next(j for j in b.get_state().blueprints if j.id == "stock-1").status == "done"


def test_partial_build_is_reported(game, tmp_path, monkeypatch):
    b = bridge(tmp_path)
    b.enqueue_blueprint("bedrooms-1", "bedrooms_3x3", "Z1")
    game.raw["zones"][0]["tiles"] = ["." * W] * H
    b.get_state()
    real = b.run_action
    monkeypatch.setattr(b, "run_action", lambda action, **p: {"ok": True, "stats": {
        "build_designated": {"label": "Buildings designated", "value": 10},
        "build_unsuitable": {"label": "Unsuitable tiles for building", "value": 2}}}
        if p.get("mode") == "build" else real(action, **p))
    with pytest.raises(BridgeError, match="placed 10/12 buildings.*Unsuitable tiles for building: 2"):
        b.advance_blueprint("bedrooms-1")
    assert job(b).status == "building"  # not retried every tick


def test_enqueue_refusals(game, tmp_path):
    b = bridge(tmp_path)
    with pytest.raises(BridgeError, match="zone Z2 not found"):
        b.enqueue_blueprint("w", "workshops_core", "Z2")
    with pytest.raises(BridgeError, match="unknown blueprint"):
        b.enqueue_blueprint("x", "castle", "Z1")
    game.raw["zones"][0]["x2"] = 39  # 10 wide
    with pytest.raises(BridgeError, match=r"does not fit zone Z1 \(10x10\)"):
        b.enqueue_blueprint("bedrooms-1", "bedrooms_3x3", "Z1")
    game.raw["zones"][0]["x2"] = 41
    b.enqueue_blueprint("bedrooms-1", "bedrooms_3x3", "Z1")
    with pytest.raises(BridgeError, match="already applied"):
        b.enqueue_blueprint("bedrooms-1", "bedrooms_3x3", "Z1")
    with pytest.raises(BridgeError, match="already used"):
        b.enqueue_blueprint("dining-1", "dining_hall", "Z1")
    assert sum(a["action"] == "apply_blueprint" for a in game.actions) == 1


# --------------------------------------------------------------------- autopilot
def doctrine_with_blueprints():
    d = FortressDoctrine()
    d.modules.production = d.modules.stockpiles = False
    d.modules.blueprints = True
    d.alarms = AlarmThresholds(food_days_warning=0, food_days_critical=0,  # the fake fortress has no stocks
                               drink_days_warning=0, drink_days_critical=0)
    return d


def test_autopilot_runs_both_phases(game, tmp_path):
    b = bridge(tmp_path)
    d = doctrine_with_blueprints()
    pilot = Autopilot(b, d, dry_run=False)
    pilot.run_tick()
    assert [a["mode"] for a in game.actions if a["action"] == "apply_blueprint"] == ["dig"]
    pilot.run_tick()  # still digging: nothing new
    assert sum(a["action"] == "apply_blueprint" for a in game.actions) == 1
    game.raw["zones"][0]["tiles"] = ["." * W] * H
    result = pilot.run_tick()
    assert [a["mode"] for a in game.actions if a["action"] == "apply_blueprint"] == ["dig", "build"]
    assert any(a.key == "act.advance_blueprint" and a.status == "ok" for a in result.actions)


def test_dry_run_applies_nothing(game, tmp_path):
    pilot = Autopilot(bridge(tmp_path), doctrine_with_blueprints(), dry_run=True)
    result = pilot.run_tick()
    assert any(a.key == "act.enqueue_blueprint" and a.status == "dry_run" for a in result.actions)
    assert not any(a["action"] == "apply_blueprint" for a in game.actions)


def test_advance_ignores_concurrency_but_not_critical_alarms():
    state = FortressState(blueprints=[BlueprintJob(id="bedrooms-1", blueprint="bedrooms_3x3", zone_id="Z1",
                                                   status="digging", progress=0.5, awaiting_build=True)])
    d = FortressDoctrine()
    assert [p.key for p in plan_blueprints(state, d, [])] == ["act.advance_blueprint"]
    critical = Alarm(kind="siege", level="critical", key="alarm.siege")
    assert plan_blueprints(state, d, [critical]) == []
