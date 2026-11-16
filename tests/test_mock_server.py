import pytest

from dfhack_bridge.base import BridgeError
from dfhack_bridge.dfhack_bridge import DFHackBridge
from dfhack_bridge.mock_bridge import MockBridge
from dfhack_bridge.mock_server import FortressSim, MockServer, dispatch


@pytest.fixture
def server():
    sim = FortressSim(seed=1, random_events=False)
    sim.days_per_second = 0
    srv = MockServer(sim, "127.0.0.1", 0)
    srv.start()
    yield srv
    srv.stop()


def test_tcp_roundtrip(server):
    b = MockBridge("127.0.0.1", server.server_address[1])
    st = b.get_state()
    assert st.population > 0 and st.stockpiles and st.zones
    oid = b.create_work_order("BrewDrink", 3)
    assert any(o.id == oid for o in b.get_state().work_orders)
    b.set_paused(True)
    assert b.get_state().paused
    b.inject_event("siege")
    assert any(e.kind == "siege" for e in b.get_state().active_events())


def test_errors_are_reported(server):
    b = MockBridge("127.0.0.1", server.server_address[1])
    with pytest.raises(BridgeError):
        b.create_work_order("NotAJob", 1)
    assert dispatch(server.sim, {"cmd": "nope"})["ok"] is False


def test_simulation_is_deterministic_and_evolves():
    a, b = FortressSim(seed=3), FortressSim(seed=3)
    for _ in range(40):
        a.step_day()
        b.step_day()
    assert a.snapshot().model_dump() == b.snapshot().model_dump()
    assert a.snapshot().date.day_of_year == 40
    start = FortressSim(seed=3).snapshot().stocks
    assert a.snapshot().stocks != start


def test_dfhack_bridge_is_a_stub():
    b = DFHackBridge(dfhack_run="dfhack-run")
    assert b.cmd_get_state()[:2] == ["dfhack-run", "lua"]
    with pytest.raises(BridgeError):
        b.get_state()
