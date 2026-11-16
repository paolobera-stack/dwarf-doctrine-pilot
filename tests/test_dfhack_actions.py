import json
import subprocess
from pathlib import Path

import pytest

from core.autopilot import Autopilot
from core.doctrine import default_doctrine as FortressDoctrine
from dfhack_bridge.base import BridgeError
from dfhack_bridge.dfhack_bridge import ACTIONS_SCRIPT, DFHackBridge
from dfhack_bridge.dfhack_state import ORDER_SPECS, raw_to_state

FAKE_ENV = Path(__file__).parent / "fixtures" / "fake_dfhack.lua"

# Stand-ins for the DFHack modules ddp_actions.lua uses, on top of the read-side fake.
ACTION_ENV = """
local function vec(t)
    return setmetatable(t, {__index = {
        insert = function(self, pos, v) table.insert(self, pos == '#' and #self + 1 or pos, v) end,
        erase = function(self, i) table.remove(self, i) end,
    }})
end
local W = df.global.world
W.manager_orders = {all = vec({}), manager_order_next_id = 50}
deleted = {}
local function order(fields)
    fields.item_conditions, fields.order_conditions = {}, {}
    fields.delete = function(self) deleted[#deleted + 1] = self.id end
    return fields
end
W.manager_orders.all:insert('#', order{id = 7, job_type = 'ConstructBed'})
created = {}
function reqscript(name)
    assert(name == 'workorder')
    return {create_orders = function(list, quiet)
        local it = list[1]
        created[#created + 1] = it
        W.manager_orders.all:insert('#', order{id = W.manager_orders.manager_order_next_id, job_type = it.job})
        W.manager_orders.manager_order_next_id = W.manager_orders.manager_order_next_id + 1
    end}
end
local pile = {id = 100, links = {give_to_workshop = {}}}
local ws = {id = 10, profile = {links = {take_from_pile = {}}}}
df.building = {find = function(id) return ({[100] = pile, [10] = ws})[id] end}
links = {pile = pile.links.give_to_workshop, ws = ws.profile.links.take_from_pile}
df.job_type = setmetatable({}, {__index = function(_, k) return k end})
dfhack.world.SetPauseState = function(v) df.global.pause_state = v end
function require(name)
    if name == 'json' then return json_shim end
    if name == 'utils' then
        return {insert_sorted = function(vector, item, field)
            for _, v in ipairs(vector) do if v[field] == item[field] then return false end end
            table.insert(vector, item)
            return true
        end}
    end
    error('unexpected module ' .. name)
end
"""


@pytest.fixture
def lua(tmp_path):
    lua53 = pytest.importorskip("lupa.lua53")
    rt = lua53.LuaRuntime()
    rt.execute(FAKE_ENV.read_text())

    def to_lua(v):
        if isinstance(v, dict):
            return rt.table_from({k: to_lua(x) for k, x in v.items()})
        if isinstance(v, list):
            return rt.table_from([to_lua(x) for x in v])
        return v

    def to_py(t):
        if lua53.lua_type(t) != "table":
            return t
        keys = list(t.keys())
        if keys and all(isinstance(k, int) for k in keys):
            return [to_py(t[k]) for k in sorted(keys)]
        return {k: to_py(v) for k, v in t.items()}

    rt.globals().json_shim = rt.table_from({
        "decode_file": lambda path: to_lua(json.loads(Path(path).read_text())),
        "encode_file": lambda data, path: Path(path).write_text(json.dumps(to_py(data))),
    })
    rt.execute(ACTION_ENV)
    rt.execute("print = function() end")

    def run(payload: dict) -> dict:
        pin, pout = tmp_path / "in.json", tmp_path / "out.json"
        pin.write_text(json.dumps(payload))
        rt.globals().arg_in, rt.globals().arg_out = str(pin), str(pout)
        rt.execute(f"local f = load({ACTIONS_SCRIPT.read_text()!r}); f(arg_in, arg_out)")
        return json.loads(pout.read_text())

    run.rt = rt
    return run


def test_lua_create_order(lua):
    res = lua({"action": "create_order", "order": ORDER_SPECS["MakeBarrel"], "amount": 5})
    assert res == {"ok": True, "order_id": 50}
    it = lua.rt.eval("created[1]")
    assert (it.job, it.amount_total, it.frequency, it.material_category[1]) == ("MakeBarrel", 5, "OneTime", "wood")


def test_lua_create_order_rejects_bad_amount(lua):
    res = lua({"action": "create_order", "order": ORDER_SPECS["MakeBarrel"], "amount": 0})
    assert not res["ok"] and "amount" in res["error"]


def test_lua_cancel_order_checks_job(lua):
    wrong = lua({"action": "cancel_order", "order_id": 7, "job_type": "MakeBarrel"})
    assert not wrong["ok"] and "not MakeBarrel" in wrong["error"]
    assert lua({"action": "cancel_order", "order_id": 7, "job_type": "ConstructBed"}) == {"ok": True}
    assert lua.rt.eval("#df.global.world.manager_orders.all") == 0 and lua.rt.eval("deleted[1]") == 7
    assert lua({"action": "cancel_order", "order_id": 7}) == {"ok": True, "missing": True}


def test_lua_pause_and_link(lua):
    assert lua({"action": "set_paused", "paused": True}) == {"ok": True, "paused": True}
    assert lua({"action": "link_stockpile", "stockpile_id": 100, "workshop_id": 10})["ok"]
    assert lua({"action": "link_stockpile", "stockpile_id": 100, "workshop_id": 10})["ok"]  # idempotent
    assert lua.rt.eval("#links.pile") == 1 and lua.rt.eval("#links.ws") == 1
    assert not lua({"action": "link_stockpile", "stockpile_id": 100, "workshop_id": 99})["ok"]
    assert "unknown action" in lua({"action": "explode"})["error"]


# --------------------------------------------------------------------- bridge (subprocess faked)
RAW = {
    "schema": 1, "errors": [], "fortress_name": "Testfort", "site_id": 42, "save_dir": "region1",
    "year": 100, "year_tick": 0, "paused": False, "items": {}, "bars": {}, "ground": {},
    "stockpiles": [{"id": 100, "flags": ["food"], "tiles": 10, "used_tiles": 2,
                    "give_to_workshops": [], "take_from_workshops": []}],
    "workshops": [{"id": 10, "kind": "workshop", "type": "Still", "name": "", "active": True}],
    "zones": [], "threats": {},
    "units": [{"id": i, "name": f"Urist {i}", "profession": "Miner", "stress": 0, "job": "",
               "labors": [], "skills": {}} for i in range(7)],
    "orders": [{"id": 7, "job_type": "ConstructBed", "reaction": "", "amount_total": 3, "amount_left": 3}],
}


class FakeGame:
    """Fakes `dfhack-run`: answers the exporter with RAW, actions via `handler`."""

    def __init__(self, raw=None):
        self.raw = json.loads(json.dumps(raw or RAW))
        self.actions: list[dict] = []
        self.next_id = 50

    def __call__(self, cmd, **kwargs):
        if cmd[3] == str(ACTIONS_SCRIPT):
            payload = json.loads(Path(cmd[4]).read_text())
            self.actions.append(payload)
            res = {"ok": True}
            if payload["action"] == "create_order":
                res["order_id"] = self.next_id
                self.raw["orders"].append({"id": self.next_id, "job_type": payload["order"]["job"],
                                           "reaction": payload["order"].get("reaction", ""),
                                           "amount_total": payload["amount"], "amount_left": payload["amount"]})
                self.next_id += 1
            elif payload["action"] == "cancel_order":
                self.raw["orders"] = [o for o in self.raw["orders"] if o["id"] != payload["order_id"]]
            Path(cmd[5]).write_text(json.dumps(res))
        else:
            Path(cmd[4]).write_text(json.dumps(self.raw))
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def game(monkeypatch):
    g = FakeGame()
    monkeypatch.setattr(subprocess, "run", g)
    return g


def test_orders_are_tracked_per_fortress(game, tmp_path):
    ledger = tmp_path / "orders.json"
    b = DFHackBridge(dfhack_run="dfhack-run", ledger_path=ledger)
    oid = b.create_work_order("BrewDrink", 4, source="emergency")
    assert game.actions[-1] == {"action": "create_order", "order": ORDER_SPECS["BrewDrink"], "amount": 4}
    sources = {o.id: (o.job, o.source) for o in b.get_state().work_orders}
    assert sources == {7: ("ConstructBed", "user"), oid: ("BrewDrink", "emergency")}
    # survives a restart
    b2 = DFHackBridge(dfhack_run="dfhack-run", ledger_path=ledger)
    assert {o.id: o.source for o in b2.get_state().work_orders}[oid] == "emergency"
    b2.cancel_work_order(oid)
    assert game.actions[-1] == {"action": "cancel_order", "order_id": oid, "job_type": "CustomReaction"}
    assert json.loads(ledger.read_text()) == {"region1#42": {}}


def test_never_cancels_player_orders(game, tmp_path):
    b = DFHackBridge(dfhack_run="dfhack-run", ledger_path=tmp_path / "o.json")
    b.get_state()
    with pytest.raises(BridgeError, match="not created by the autopilot"):
        b.cancel_work_order(7)
    assert game.actions == []


def test_reused_order_id_is_not_ours(game, tmp_path):
    """Loading an older save can hand our old id to a different (player) order."""
    b = DFHackBridge(dfhack_run="dfhack-run", ledger_path=tmp_path / "o.json")
    oid = b.create_work_order("MakeBarrel", 5)
    game.raw["orders"] = [{"id": oid, "job_type": "ConstructDoor", "reaction": "", "amount_total": 1,
                           "amount_left": 1}]
    assert b.get_state().work_orders[0].source == "user"
    with pytest.raises(BridgeError):
        b.cancel_work_order(oid)


def test_link_stockpile_resolves_workshop(game):
    b = DFHackBridge(dfhack_run="dfhack-run")
    b.get_state()
    b.link_stockpile(100, "Still")
    assert game.actions[-1] == {"action": "link_stockpile", "stockpile_id": 100, "workshop_id": 10}
    with pytest.raises(BridgeError, match="no built Kitchen"):
        b.link_stockpile(100, "Kitchen")


def test_action_error_is_bridge_error(monkeypatch):
    def fail(cmd, **kwargs):
        Path(cmd[-1]).write_text(json.dumps({"ok": False, "error": "Invalid job type"}))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(BridgeError, match="set_paused: Invalid job type"):
        DFHackBridge(dfhack_run="dfhack-run").set_paused(True)


def test_every_doctrine_job_has_an_order_spec():
    jobs = {t.job for t in FortressDoctrine().stock_targets}
    jobs |= {r.container_job for r in FortressDoctrine().stockpile_rules if r.container_job}
    assert jobs <= set(ORDER_SPECS)


# --------------------------------------------------------------------- autopilot on DFHack
def test_dry_run_still_pauses_on_critical(game):
    game.raw["threats"] = {"invaders": 3, "great_dangers": 0, "dangers": 3, "merchants": 0}
    b = DFHackBridge(dfhack_run="dfhack-run")
    pilot = Autopilot(b, FortressDoctrine(), dry_run=True)
    result = pilot.run_tick()
    assert result.paused_by_autopilot
    assert game.actions == [{"action": "set_paused", "paused": True}]  # nothing else executed
    assert any(a.status == "dry_run" and a.key == "act.create_order" for a in result.actions)


def test_unsupported_actions_are_skipped(game):
    game.raw["stockpiles"][0]["used_tiles"] = 10  # full food pile -> would expand
    b = DFHackBridge(dfhack_run="dfhack-run")
    pilot = Autopilot(b, FortressDoctrine(), dry_run=True)
    state = b.get_state()
    assert "act.expand_stockpile" not in {p.key for p in pilot.plan(state, [])}
    b.unsupported = frozenset()
    assert "act.expand_stockpile" in {p.key for p in pilot.plan(state, [])}


def test_live_tick_creates_orders(game, tmp_path):
    b = DFHackBridge(dfhack_run="dfhack-run", ledger_path=tmp_path / "o.json")
    pilot = Autopilot(b, FortressDoctrine(), dry_run=False)
    result = pilot.run_tick()
    created = [a for a in game.actions if a["action"] == "create_order"]
    assert created and all(a.status == "ok" for a in result.actions if a.key == "act.create_order")
    state = b.get_state()
    assert {o.source for o in state.work_orders if o.id >= 50} <= {"autopilot", "emergency"}
    assert len(created) <= 5
    assert raw_to_state(game.raw).pending_for("drink") > 0
