from datetime import timedelta

import pytest

from bot_selector.adapters import history_for
from bot_selector.grade import DISQ_CAP, grade_setup, setup_component, tier_for
from bot_selector.model import Setup
from bot_selector.rank import build_edge, decide, score_setup
from bot_selector.sources import LocalSource

from conftest import et

NOW = et("2026-10-27 10:00:00")


def mk(bot_id, key="k", level=None, ts=None, valid_s=600, **features):
    ts = ts or NOW
    feats = {"late_s": 0.0, **features}
    contracts = feats.pop("contracts", None)
    return Setup(bot_id=bot_id, key=key, ts=ts, valid_until=ts + timedelta(seconds=valid_s),
                 label=f"{bot_id} {key}", symbol="SPY", level=level, features=feats, contracts=contracts)


# ------------------------------------------------------------------ credibility comes from the audit
def test_credibility_is_evidence_times_price_realism(registry):
    assert registry.get("chronos").credibility == pytest.approx((13 / 35) * (5 / 20))
    assert registry.get("ibs").credibility == pytest.approx((30 / 35) * (17 / 20))
    assert registry.get("premium-sleeve").credibility == 0.25          # explicit override, unaudited
    assert registry.get("chronos").credibility < registry.get("ibs").credibility


# ------------------------------------------------------------------ execution + tiers
def test_fresh_alert_beats_stale_alert(registry):
    bot = registry.get("chronos")
    fresh, _ = grade_setup(bot, mk("chronos", expiry_verified=True), [], NOW)
    stale, _ = grade_setup(bot, mk("chronos", expiry_verified=True, ts=NOW - timedelta(seconds=400)), [], NOW)
    assert fresh.exec_score == 100 and stale.exec_score < fresh.exec_score


def test_alert_past_its_window_is_disqualified_and_capped(registry):
    bot = registry.get("chronos")
    g, _ = grade_setup(bot, mk("chronos", expiry_verified=True, ts=NOW - timedelta(seconds=900)), [], NOW)
    assert g.disqualifiers and g.grade <= DISQ_CAP and g.tier == "D"


def test_zero_contracts_cannot_be_averaged_out_of_tier_d(registry):
    bot = registry.get("spybps")
    g, _ = grade_setup(bot, mk("spybps", contracts=0, age_exempt=True), [], NOW)
    assert g.tier == "D" and g.grade <= DISQ_CAP and any("0 contracts" in d for d in g.disqualifiers)


def test_one_contract_is_a_weak_factor_not_a_disqualifier(registry):
    bot = registry.get("spybps")
    g, _ = grade_setup(bot, mk("spybps", contracts=1, age_exempt=True), [], NOW)
    assert not g.disqualifiers and g.grade >= 80


def test_unset_account_size_is_noted_and_never_scored(registry):
    bot = registry.get("premium-sleeve")
    a, _ = grade_setup(bot, mk("premium-sleeve", killswitch=False, contracts=None), [], NOW)
    assert any("not scored" in n for n in a.notes) and a.exec_score == 100


def test_unverified_expiry_costs_points_and_is_named_as_the_drag(registry):
    bot = registry.get("chronos")
    g, _ = grade_setup(bot, mk("chronos"), [], NOW)
    assert g.exec_score < 100 and any("NOT verified" in d for d in g.drags)


def test_missing_inputs_are_neutral_never_a_free_hundred(registry):
    bot = registry.get("condor")
    g, _ = grade_setup(bot, mk("condor"), [], NOW)                   # no pct_of_model, no gate margin
    assert g.exec_score < 100


def test_killswitch_and_nogo_disqualify(registry):
    bot = registry.get("condor")
    assert grade_setup(bot, mk("condor", killswitch=True), [], NOW)[0].tier == "D"
    assert grade_setup(bot, mk("condor", gonogo="NO-GO"), [], NOW)[0].tier == "D"


def test_tier_boundaries():
    assert [tier_for(x) for x in (100, 85, 84, 70, 69, 50, 49, 1)] == ["A", "A", "B", "B", "C", "C", "D", "D"]


# ------------------------------------------------------------------ the setup component is bot-relative
def test_setup_score_ranks_a_module_against_the_bots_own_history(registry, repos):
    bot = registry.get("chronos")
    hist = [r for r in history_for(bot, LocalSource(repos)) if r["date"] < "2026-10-27"]
    best, adj_best, _ = setup_component(bot, mk("chronos", level="Phase1A_GapTrap_Single0DTE"), hist, 15)
    worst, adj_worst, _ = setup_component(bot, mk("chronos", level="Phase1C_BullTrap_PutDebitSpread"), hist, 15)
    assert best > worst and adj_best > 0 > adj_worst


def test_unseen_level_is_scored_as_average_not_as_a_guess(registry, repos):
    bot = registry.get("chronos")
    hist = history_for(bot, LocalSource(repos))
    score, adj, note = setup_component(bot, mk("chronos", level="Phase9_Brand_New"), hist, 15)
    assert adj == 0.0 and "never seen" in note


def test_too_little_own_history_means_no_setup_score(registry):
    bot = registry.get("chronos")
    hist = [{"level": "Phase1A_GapTrap_Single0DTE", "outcome": 0.1, "date": "2026-10-01", "unit": "roi"}] * 3
    assert setup_component(bot, mk("chronos", level="Phase1A_GapTrap_Single0DTE"), hist, 15)[0] is None


def test_a_factor_the_research_rejected_is_never_used(registry, repos):
    """Sniper's setup labels invert out of sample, so its grade is execution-only whatever its ledger says."""
    bot = registry.get("sniper")
    hist = history_for(bot, LocalSource(repos))
    g, adj = grade_setup(bot, mk("sniper", level="A3_VWAP_Exhaust", expiry_verified=True), hist, NOW)
    assert g.setup_score is None and adj == 0.0


def test_dip_call_etf_uses_the_documented_ordering(registry):
    bot = registry.get("dipcalls")
    spy = grade_setup(bot, mk("dipcalls", level="SPY", expiry_verified=True, age_exempt=True), [], NOW)[0]
    iwm = grade_setup(bot, mk("dipcalls", level="IWM", expiry_verified=True, age_exempt=True), [], NOW)[0]
    assert spy.grade > iwm.grade


# ------------------------------------------------------------------ the cross-bot number
def test_live_results_pull_a_bots_edge_toward_what_it_actually_did(registry):
    bot = registry.get("chronos")
    none = build_edge(bot, [], 0.0, 100, 30)
    bad = build_edge(bot, [{"outcome": -0.10, "unit": "roi"}] * 60, 0.0, 100, 30)
    assert none.blended == pytest.approx(0.0701) and bad.blended < 0 and bad.expected < 0


def test_expected_edge_is_documented_times_credibility_times_execution(registry):
    bot = registry.get("ibs")
    e = build_edge(bot, [], 0.0, 80, 30)
    assert e.expected == pytest.approx(0.0068 * bot.credibility * 0.8)


def test_no_documented_edge_means_the_bot_can_be_shown_but_never_picked(registry):
    s = score_setup(registry.get("dipcalls"), mk("dipcalls", level="SPY", expiry_verified=True, age_exempt=True),
                    [], NOW, registry.defaults, set())
    assert not s.eligible and any("no documented per-trade edge" in r for r in s.reasons)


def test_non_live_bots_are_graded_but_never_eligible(registry):
    omega = score_setup(registry.get("omega"), mk("omega", level="P1_OD10_Pullback", data_age_s=600.0), [], NOW,
                        registry.defaults, set())
    spy = score_setup(registry.get("spybps"), mk("spybps", contracts=1, age_exempt=True), [], NOW,
                      registry.defaults, set())
    assert not omega.eligible and omega.reasons[0].startswith("paper")
    assert not spy.eligible and "replaced by premium-sleeve" in spy.reasons[0]


def test_manual_halt_wins(registry):
    s = score_setup(registry.get("ibs"), mk("ibs", age_exempt=True), [], NOW, registry.defaults, {"ibs"})
    assert not s.eligible and any("halted" in r for r in s.reasons)


def collected(*setups):
    out = {}
    for s in setups:
        out.setdefault(s.bot_id, ([], []))[0].append(s)
    return out


def test_pick_orders_on_expected_edge_not_on_grade(registry):
    """Grades are bot-relative, so a grade-100 alert must NOT win on grade alone. Give sniper a tiny documented
    edge: its alert still grades at least as high as IBS's, yet IBS is the pick."""
    registry.get("sniper").claimed_edge = 0.001
    high_grade_weak = mk("sniper", key="a", expiry_verified=True)
    lower_grade_strong = mk("ibs", key="b", age_exempt=True)
    d = decide(registry, collected(high_grade_weak, lower_grade_strong), {}, NOW)
    by = {b.setup.key: b for b in d.board}
    assert by["a"].grade.grade >= by["b"].grade.grade
    assert d.pick.setup.key == "b"


def test_live_results_can_demote_a_bot_with_a_big_documented_edge(registry, repos):
    """Sniper documents +11.5%/trade but its own ledger is negative. Blended in, it must fall below IBS."""
    src = LocalSource(repos)
    hist = {"sniper": history_for(registry.get("sniper"), src)}
    d = decide(registry, collected(mk("sniper", key="a", expiry_verified=True), mk("ibs", key="b", age_exempt=True)),
               hist, NOW)
    assert d.pick.setup.key == "b"


def test_no_trade_when_nothing_clears_the_bar(registry):
    d = decide(registry, collected(mk("omega", key="o", level="P1_OD10_Pullback")), {}, NOW)
    assert d.pick is None and d.board and not d.board[0].eligible


def test_switch_margin_stops_flip_flopping_between_near_ties(registry):
    a = mk("chronos", key="first", expiry_verified=True)
    b = mk("chronos", key="second", expiry_verified=True, ts=NOW + timedelta(seconds=1))
    first = decide(registry, collected(a), {}, NOW)
    assert first.pick.setup.key == "first"
    both = decide(registry, collected(a, b), {}, NOW + timedelta(seconds=2), previous_key="first")
    assert both.pick.setup.key == "first" and both.kept_previous is False       # equal edge: it simply stays


def test_a_much_better_alert_does_take_over(registry):
    registry.get("chronos").claimed_edge = 0.02
    weak = mk("chronos", key="weak", expiry_verified=True)
    strong = mk("ibs", key="strong", age_exempt=True)
    d = decide(registry, collected(weak, strong), {}, NOW, previous_key="weak")
    assert d.pick.setup.key == "strong" and d.kept_previous is False


def test_a_modestly_better_alert_does_not_steal_the_pick(registry):
    """IBS beats this chronos alert by less than the 25% switch margin, so the earlier pick is kept."""
    ibs = build_edge(registry.get("ibs"), [], 0.0, 100, 30).expected
    chronos = registry.get("chronos")
    chronos.claimed_edge = (ibs / 1.10) / chronos.credibility            # IBS is 10% better
    weak = mk("chronos", key="weak", expiry_verified=True)
    strong = mk("ibs", key="strong", age_exempt=True)
    d = decide(registry, collected(weak, strong), {}, NOW, previous_key="weak")
    assert d.pick.setup.key == "weak" and d.kept_previous is True


def test_a_pick_that_expired_is_dropped(registry):
    a = mk("chronos", key="old", expiry_verified=True, valid_s=60)
    d = decide(registry, collected(a), {}, NOW + timedelta(seconds=120), previous_key="old")
    assert d.pick is None


def test_an_exited_position_is_not_a_pick(registry):
    a = mk("chronos", key="done", expiry_verified=True)
    a.closed = True
    assert decide(registry, collected(a), {}, NOW).pick is None
