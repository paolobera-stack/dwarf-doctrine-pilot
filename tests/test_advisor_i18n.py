from types import SimpleNamespace

import pytest

from core import i18n
from core.advisor import (
    AdvisorReply,
    BudgetExceeded,
    GeminiAdvisor,
    OfflineAdvisor,
    RequestBudget,
    summarize_state,
)
from core.doctrine import DoctrinePatch, GeminiBudget, apply_patch, default_doctrine
from dfhack_bridge.mock_server import FortressSim

BPS = ["bedrooms_3x3", "workshops_core", "dining_hall", "stockpiles_core"]


def test_every_key_is_localized():
    for key in i18n.all_keys():
        entry = i18n._STRINGS[key]
        assert entry.get("it") and entry.get("en"), key


def test_language_switch_notifies():
    seen = []
    i18n.on_language_changed(seen.append)
    i18n.set_language("en")
    assert i18n.tr("tab.dwarves") == "Dwarves & Roles" and seen[-1] == "en"
    i18n.set_language("it")
    assert i18n.tr("tab.dwarves") == "Nani & Ruoli"


def test_request_budget():
    t = {"now": 0.0}
    b = RequestBudget(clock=lambda: t["now"])
    cfg = GeminiBudget(max_requests_per_minute=2, max_requests_per_day=3, analysis_cooldown_s=100)
    for _ in range(2):
        b.check(cfg)
        b.record()
    with pytest.raises(BudgetExceeded):
        b.check(cfg)
    t["now"] = 61
    b.check(cfg, analysis=True)
    b.record(analysis=True)
    with pytest.raises(BudgetExceeded):
        b.check(cfg)  # daily cap of 3 reached
    assert b.used_today() == 3


def test_offline_advisor_proposes_valid_patch():
    st = FortressSim().snapshot()
    d = default_doctrine()
    res = OfflineAdvisor(blueprints=BPS).ask([], "Servono più birra e magazzini", d, st)
    assert res.offline and res.reply.proposal is not None
    new = apply_patch(d, res.reply.proposal)
    assert new.target_for("drink").target >= d.target_for("drink").target
    assert any(b.blueprint == "stockpiles_core" for b in new.blueprint_queue)


def test_state_summary_is_compact():
    st = FortressSim().snapshot()
    s = summarize_state(st, default_doctrine(), level=2)
    assert set(s) >= {"pop", "food_days", "drink_days", "stocks", "alarms"}


@pytest.fixture
def fake_gemini(monkeypatch):
    calls = []

    class Models:
        def count_tokens(self, model, contents):
            return SimpleNamespace(total_tokens=len(contents) // 4)

        def generate_content(self, model, contents, config):
            calls.append((contents, config))
            reply = AdvisorReply(reply="ok", proposal=DoctrinePatch(strategy_notes="more ale"))
            return SimpleNamespace(parsed=reply, text=reply.model_dump_json())

    import google.genai

    monkeypatch.setattr(google.genai, "Client", lambda api_key: SimpleNamespace(models=Models()))
    return calls


def test_gemini_payload_under_budget_and_structured(fake_gemini):
    adv = GeminiAdvisor(api_key="test", blueprints=BPS)
    st = FortressSim().snapshot()
    d = default_doctrine()
    history = [SimpleNamespace(role="user", text="x" * 400)] * 20
    system, user, tokens = adv.build_payload(history, "Strategia?", d, st)
    assert tokens <= d.gemini.max_payload_tokens
    res = adv.ask([], "Strategia?", d, st)
    assert res.reply.proposal.strategy_notes == "more ale"
    contents, config = fake_gemini[0]
    assert config.response_mime_type == "application/json" and config.response_json_schema == AdvisorReply.model_json_schema()
    assert "DOCTRINE=" in contents and "STATE=" in contents
