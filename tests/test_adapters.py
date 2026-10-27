"""Adapters against REAL committed bot files (tests/fixtures/repos are copies, not mocks)."""
from bot_selector.adapters import COLLECT, history_for
from bot_selector.sources import LocalSource

from conftest import et, write


def collect(registry, repos, bot_id, now):
    bot = registry.get(bot_id)
    return COLLECT[bot.adapter](bot, LocalSource(repos), et(now), registry.defaults)


def test_chronos_sees_only_what_had_posted(registry, repos):
    setups, _ = collect(registry, repos, "chronos", "2026-10-27 09:50:30")
    labels = {s.symbol for s in setups}
    assert labels == {"NVDA", "AMZN", "SPY"}            # TSLA posts 09:58, META 11:06: not yet
    assert all(not s.closed for s in setups)            # NVDA's exit is 09:54, still in the future


def test_chronos_marks_exited_and_skips_withheld(registry, repos):
    setups, notes = collect(registry, repos, "chronos", "2026-10-27 15:00:00")
    by = {s.symbol: s for s in setups}
    assert by["NVDA"].closed and by["META"].closed and by["AMZN"].closed
    assert "AMD" not in by                               # 'posted': false, the bot itself withheld it
    assert any("withheld" in n for n in notes)


def test_chronos_late_seconds_and_level_come_from_the_file(registry, repos):
    setups, _ = collect(registry, repos, "chronos", "2026-10-27 09:50:30")
    nvda = next(s for s in setups if s.symbol == "NVDA")
    assert nvda.level == "Phase1A_GapTrap_Single0DTE"
    assert nvda.features["late_s"] == 12.5
    assert nvda.features["minute"] == 16


def test_sniper_keeps_its_mode_letter(registry, repos):
    setups, _ = collect(registry, repos, "sniper", "2026-10-27 09:48:00")
    assert len(setups) == 1 and setups[0].features["mode"] == "C" and setups[0].level == "M3_ORB15_BullTrap"


def test_omega_minute_index_becomes_a_clock_time(registry, repos):
    setups, _ = collect(registry, repos, "omega", "2026-10-27 09:50:00")
    assert len(setups) == 1 and setups[0].level == "P1_OD10_Pullback"
    assert setups[0].ts == et("2026-10-27 09:46:00")
    assert setups[0].features["data_age_s"] == 600.0     # the ~10 minute delayed feed the audit found


def test_missing_state_file_is_a_note_not_a_crash(registry, repos):
    setups, notes = collect(registry, repos, "chronos", "2026-11-02 10:00:00")
    assert setups == [] and "no state/sent-2026-11-02.json" in notes[0]


def test_history_loaders_return_outcomes(registry, repos):
    src = LocalSource(repos)
    ch = history_for(registry.get("chronos"), src)
    assert len(ch) == 50 and {"level", "outcome", "date", "ticker", "minute"} <= set(ch[0])
    sn = history_for(registry.get("sniper"), src)
    assert len(sn) == 12 and sn[0]["mode"] in ("A", "B", "C")
    om = history_for(registry.get("omega"), src)
    assert len(om) == 7 and om[0]["unit"] == "R"


def test_condor_reads_gate_margin_and_gonogo(registry, repos):
    setups, _ = collect(registry, repos, "condor", "2026-10-26 09:40:00")
    assert len(setups) == 1
    f = setups[0].features
    assert f["gonogo"] == "GO" and abs(f["gate_margin"] - (0.0993 - 0.0975) / 0.0993) < 1e-9
    assert f["killswitch"] is False and f["pct_of_model"] is not None


def test_condor_nogo_day_is_flagged(registry, repos):
    import json, os
    p = os.path.join(repos, "daytrading_bot", "state.json")
    st = json.load(open(p)); st["open_position"]["date"] = "2026-10-27"
    write(repos, "daytrading_bot/state.json", st)
    setups, _ = collect(registry, repos, "condor", "2026-10-27 09:40:00")
    assert setups[0].features["gonogo"] == "NO-GO"


def test_condor_no_position_today(registry, repos):
    setups, notes = collect(registry, repos, "condor", "2026-10-28 09:40:00")
    assert setups == [] and notes


def test_ibs_no_buy_means_no_setup(registry, repos):
    setups, notes = collect(registry, repos, "ibs", "2026-10-27 09:15:00")
    assert setups == [] and "no BUY" in notes[0]


def test_ibs_buy_signal_is_parsed_and_expires_after_the_open(registry, repos):
    text = ("**IBS callout bot - signals from the Mon Oct 26 close**\n\n__BUY at the Tue Oct 27 open__ (market-on-open)\n\n"
            "```\nSPY: close 765.37  IBS 0.04  [BUY signal]\nQQQ: close 712.20  IBS 0.68\n"
            "IWM: close 276.99  IBS 0.06  [BUY signal]\n```")
    write(repos, "ibs-callout-bot/state.json", {"last_evening": {"bar": "2026-10-26", "text": text}})
    setups, _ = collect(registry, repos, "ibs", "2026-10-27 09:15:00")
    assert sorted(s.symbol for s in setups) == ["IWM", "SPY"]
    assert setups[0].valid_until == et("2026-10-27 10:00:00")


def test_dipcalls_only_fires_below_the_ibs_threshold(registry, repos):
    setups, notes = collect(registry, repos, "dipcalls", "2026-10-26 15:50:00")
    assert setups == [] and "IBS above" in notes[0]
    csv = ("run_time,etf,last,high,low,ibs,prior_high,bar_time\n"
           "2026-10-26T15:46:11-04:00,SPY,765.5,767.7,761.6,0.04,763.4,2026-10-26 15:46:00-04:00\n"
           "2026-10-26T15:46:11-04:00,QQQ,713.4,715.2,705.5,0.81,709.3,2026-10-26 15:46:00-04:00\n")
    write(repos, "robinhood-1500-options-strategy/deliver/signals_log.csv", csv, raw=True)
    setups, _ = collect(registry, repos, "dipcalls", "2026-10-26 15:50:00")
    assert [s.symbol for s in setups] == ["SPY"] and setups[0].level == "SPY"


def test_spybps_position_opened_today(registry, repos):
    import json, os
    p = os.path.join(repos, "spy-bps-bot", "ledger", "state.json")
    setups, _ = collect(registry, repos, "spybps", "2026-10-26 10:00:00")
    assert len(setups) == 1 and setups[0].contracts == 1
    setups, _ = collect(registry, repos, "spybps", "2026-10-27 10:00:00")
    assert setups == []


def test_sleeve_unsized_position_has_no_contract_count(registry, repos):
    pos = [{"id": "ab12", "opened": "2026-10-27", "expiry": "2026-12-04", "short_strike": 756.0,
            "long_strike": 746.0, "contracts": None, "credit": 1.89, "max_loss_per_contract": 811.0,
            "net_vega_per_contract": -11.6, "net_delta_per_contract": 12.0, "status": "open"}]
    write(repos, "premium-sleeve/state/positions.json", pos)
    write(repos, "premium-sleeve/state/equity.json", {"halted": False})
    setups, _ = collect(registry, repos, "premium-sleeve", "2026-10-27 15:45:00")
    assert len(setups) == 1 and setups[0].contracts is None and setups[0].features["killswitch"] is False


def test_status_only_bots_never_produce_setups(registry, repos):
    for bot_id in ("frontier5", "scanner"):
        setups, notes = collect(registry, repos, bot_id, "2026-10-27 10:00:00")
        assert setups == [] and notes


def test_a_malformed_state_file_does_not_take_the_engine_down(engine, repos):
    write(repos, "apex_chronos/state/sent-2026-10-27.json", "{not json", raw=True)
    d = engine.decide(et("2026-10-27 09:50:30"))
    assert "chronos" in engine.errors                    # reported, not raised
    assert all(b.setup.bot_id != "chronos" for b in d.board)


# ------------------------------------------------------------------ sources
def test_a_missing_required_file_is_an_error_not_a_quiet_day(registry, tmp_path):
    from bot_selector.engine import Engine
    from bot_selector.sources import LocalSource
    empty = tmp_path / "nothing"
    empty.mkdir()
    eng = Engine(registry, LocalSource(str(empty)), overrides_path=str(tmp_path / "o.json"))
    from conftest import et
    eng.decide(et("2026-10-27 09:50:00"))
    assert "ibs" in eng.errors and "not found" in eng.errors["ibs"]


def test_each_bot_reads_its_own_branch(registry):
    from bot_selector.engine import Engine
    from bot_selector.sources import GitHubSource
    src = GitHubSource(token="x")
    Engine(registry, src)
    assert src.branches["samedame/robinhood-1500-options-strategy"] == "master"
    assert src.branches[registry.get("chronos").repo] == "main"


def test_selftest_names_the_bots_it_could_not_read(registry, repos, tmp_path):
    from bot_selector.cli import selftest_report
    from bot_selector.engine import Engine
    from bot_selector.sources import LocalSource
    from conftest import et
    good, ok = selftest_report(Engine(registry, LocalSource(repos), overrides_path=str(tmp_path / "o.json")), et("2026-10-27 10:00:00"))
    assert ok and "chronos" in good
    empty = tmp_path / "none"; empty.mkdir()
    bad, ok2 = selftest_report(Engine(registry, LocalSource(str(empty)), overrides_path=str(tmp_path / "o.json")), et("2026-10-27 10:00:00"))
    assert not ok2 and "FAIL" in bad and "READ PROBLEMS" in bad
