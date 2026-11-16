from core.alarms import evaluate_alarms
from core.autopilot import Autopilot, blueprint_readiness
from core.doctrine import default_doctrine
from dfhack_bridge.mock_bridge import LocalMockBridge
from dfhack_bridge.mock_server import FortressSim


def make(seed=7, **kw):
    sim = FortressSim(seed=seed, random_events=False)
    bridge = LocalMockBridge(sim)
    clock = {"t": 0.0}
    ap = Autopilot(bridge, default_doctrine(), clock=lambda: clock["t"], **kw)
    return sim, bridge, ap, clock


def test_production_orders_are_idempotent():
    sim, _, ap, _ = make()
    with sim.lock:
        sim.stocks["drink"] = 300  # avoid emergency path
        sim.stocks["prepared_meals"] = 0
    r1 = ap.run_tick()
    meals = [a for a in r1.actions if a.key == "act.create_order" and a.params["item"] == "prepared_meals"]
    assert len(meals) == 1 and meals[0].status == "ok"
    r2 = ap.run_tick()
    assert not [a for a in r2.actions if a.key == "act.create_order" and a.params["item"] == "prepared_meals"]


def test_action_limit_per_tick():
    sim, _, ap, _ = make()
    with sim.lock:
        for k in list(sim.stocks):
            sim.stocks[k] = 0
    ap.doctrine.limits.max_actions_per_tick = 2
    r = ap.run_tick()
    executed = [a for a in r.actions if a.status in ("ok", "failed") and a.key != "act.pause"]
    assert len(executed) == 2  # the safety pause is never subject to the limit
    assert "act.limit" in [a.key for a in r.actions]


def test_dry_run_executes_nothing():
    sim, bridge, ap, _ = make(dry_run=True)
    r = ap.run_tick()
    assert r.actions and all(a.status in ("dry_run", "info") for a in r.actions)
    assert bridge.get_state().work_orders == []
    assert ap.run_tick().actions == []  # unchanged plan is not re-logged


def test_disabled_modules_do_nothing():
    _, bridge, ap, _ = make()
    for m in ("production", "stockpiles", "blueprints", "emergency"):
        setattr(ap.doctrine.modules, m, False)
    r = ap.run_tick()
    assert r.actions == []
    assert bridge.get_state().work_orders == []


def test_critical_drink_triggers_emergency_order_and_pause_with_cooldown():
    sim, bridge, ap, clock = make()
    with sim.lock:
        sim.stocks["drink"] = 0
    r = ap.run_tick()
    keys = [a.key for a in r.actions]
    assert "act.emergency_order" in keys and "act.pause" in keys
    assert r.paused_by_autopilot and bridge.get_state().paused
    assert any(o.source == "emergency" for o in bridge.get_state().work_orders)
    # user resumes; same alarm inside the cooldown must not pause again
    bridge.set_paused(False)
    clock["t"] += 10
    r2 = ap.run_tick()
    assert "act.pause" not in [a.key for a in r2.actions]
    assert not [a for a in r2.actions if a.key == "act.emergency_order"]  # one emergency order at a time
    clock["t"] += ap.doctrine.emergency.notify_cooldown_s
    assert "act.pause" in [a.key for a in ap.run_tick().actions]


def test_critical_event_pauses_without_any_llm():
    sim, bridge, ap, _ = make()
    with sim.lock:
        sim.stocks["drink"] = 500
        sim.stocks["prepared_meals"] = 500
    bridge.inject_event("siege")
    r = ap.run_tick()
    assert any(a.kind == "event:siege" for a in r.notifications)
    assert bridge.get_state().paused
    assert not hasattr(ap, "advisor")


def test_pause_disabled_by_policy():
    sim, bridge, ap, _ = make()
    ap.doctrine.emergency.pause_on_critical = False
    bridge.inject_event("cave_in")
    r = ap.run_tick()
    assert r.notifications and not bridge.get_state().paused


def test_blueprints_respect_preconditions_and_enqueue():
    sim, bridge, ap, _ = make()
    ap.doctrine.modules.blueprints = True
    ap.doctrine.limits.max_actions_per_tick = 50
    state = bridge.get_state()
    statuses = {b.id: blueprint_readiness(b, state)[0] for b in ap.doctrine.blueprint_queue}
    assert "planned" in statuses.values()
    r = ap.run_tick()
    queued = [a for a in r.actions if a.key == "act.enqueue_blueprint"]
    assert len(queued) <= ap.doctrine.limits.max_concurrent_blueprints
    assert queued and bridge.get_state().blueprints


def test_alarms_thresholds():
    sim, bridge, ap, _ = make()
    with sim.lock:
        sim.stocks["drink"] = 0
    kinds = {(a.kind, a.level) for a in evaluate_alarms(bridge.get_state(), ap.doctrine)}
    assert ("drink_low", "critical") in kinds


def test_long_run_is_stable_enough():
    """60 game days with the default doctrine: autopilot keeps orders flowing without exceptions."""
    sim, bridge, ap, clock = make()
    for day in range(60):
        if day % 2 == 0:
            ap.run_tick()
            if bridge.get_state().paused:
                bridge.set_paused(False)
        sim.step_day()
        clock["t"] += 10
    st = bridge.get_state()
    assert st.date.day_of_year == 60
    assert any(o.status == "done" for o in st.work_orders)
