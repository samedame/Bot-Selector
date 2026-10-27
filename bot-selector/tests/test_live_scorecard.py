import json
import urllib.error
from datetime import timedelta

import pytest

from bot_selector import discord
from bot_selector.adapters import history_for
from bot_selector.engine import Engine
from bot_selector.live import run_live, step
from bot_selector.render import render
from bot_selector.scorecard import MIN_N, attach_outcomes, scorecard
from bot_selector.sources import LocalSource
from bot_selector.store import Store

from conftest import et


# ------------------------------------------------------------------ no look-ahead
def test_a_decision_cannot_see_alerts_that_had_not_posted_yet(engine):
    d = engine.decide(et("2026-10-27 09:48:00"))
    assert {b.setup.symbol for b in d.board} <= {"NVDA", "AVGO"}
    d2 = engine.decide(et("2026-10-27 09:51:00"))
    symbols = {b.setup.symbol for b in d2.board}
    assert {"AMZN", "SPY"} <= symbols and "TSLA" not in symbols           # TSLA posts at 09:58


def test_grading_history_excludes_the_day_being_decided(engine):
    hist = engine.histories(et("2026-10-27 10:00:00"))
    assert hist["chronos"] and all(r["date"] < "2026-10-27" for r in hist["chronos"])
    earlier = engine.histories(et("2026-10-20 10:00:00"))
    assert len(earlier["chronos"]) < len(hist["chronos"])           # a replay of 10-20 knows less than today


def test_replay_of_an_old_day_does_not_use_later_outcomes(registry, repos, tmp_path):
    """Grade the same alert twice with two different futures in the ledger: the grade must not change."""
    import os
    e1 = Engine(registry, LocalSource(repos), overrides_path=str(tmp_path / "overrides.json"))
    g1 = e1.decide(et("2026-10-23 09:50:00"))
    p = os.path.join(repos, "apex_chronos", "ledger", "trades.csv")
    rows = open(p).read().splitlines()
    # make every trade on/after 10-24 a huge winner; a leak would move 10-23's grades
    mutated = [rows[0]] + [r if r.split(",")[1] < "2026-10-24" else r.replace(r.split(",")[7], "5.0", 1) for r in rows[1:]]
    open(p, "w").write("\n".join(mutated) + "\n")
    e2 = Engine(registry, LocalSource(repos), overrides_path=str(tmp_path / "overrides.json"))
    g2 = e2.decide(et("2026-10-23 09:50:00"))
    assert [(b.setup.key, b.grade.grade, b.edge.expected) for b in g1.board] == \
           [(b.setup.key, b.grade.grade, b.edge.expected) for b in g2.board]


# ------------------------------------------------------------------ live loop
class Clock:
    def __init__(self, times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0) if len(self.times) > 1 else self.times[0]


def test_it_posts_only_when_the_pick_changes(engine, tmp_path):
    store, posts = Store(str(tmp_path / "state")), []
    t0 = et("2026-10-27 09:48:00")
    for dt in (0, 10, 20, 1500):                      # same board three times, then every alert has aged out
        step(engine, store, t0 + timedelta(seconds=dt), posts.append)
    assert len(posts) == 2                            # one TRADE THIS, one NO TRADE when it expired
    assert posts[0].startswith("TRADE THIS") and posts[1].startswith("NO TRADE")
    assert [json.loads(l)["pick"] is not None for l in open(store.log_path)] == [True, False]


def test_the_message_shows_its_own_arithmetic(engine):
    text = render(engine.decide(et("2026-10-27 09:48:00")))
    assert "Grade " in text and "for this bot" in text and "Expected edge" in text and "credibility" in text


def test_loop_stops_at_the_close_and_on_weekends(engine, tmp_path):
    store, posts = Store(str(tmp_path / "s")), []
    clock = Clock([et("2026-10-27 16:06:00")])
    assert run_live(engine, store, posts.append, now_fn=clock, sleep=lambda s: None) == 0
    sat = Clock([et("2026-10-24 10:00:00")])
    assert run_live(engine, store, posts.append, now_fn=sat, sleep=lambda s: None) == 0


def test_loop_polls_until_the_close(engine, tmp_path):
    store, posts = Store(str(tmp_path / "s")), []
    clock = Clock([et("2026-10-27 09:48:00"), et("2026-10-27 09:48:30"), et("2026-10-27 09:49:00"), et("2026-10-27 16:06:00")])  # first call is the loop start time
    n = run_live(engine, store, posts.append, now_fn=clock, sleep=lambda s: None, poll_s=0)
    assert n == 2 and posts


def test_unreadable_bot_is_reported_in_the_board_file_not_swallowed(engine, repos, tmp_path):
    from conftest import write
    write(repos, "apex_chronos/state/sent-2026-10-27.json", "{broken", raw=True)
    store = Store(str(tmp_path / "s"))
    step(engine, store, et("2026-10-27 09:48:00"), lambda t: None)
    assert "chronos" in store.load_board()["errors"]


# ------------------------------------------------------------------ discord
class _Resp:
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_discord_retries_transient_failures(monkeypatch):
    calls = {"n": 0}

    def fake(req, timeout=0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError("boom")
        return _Resp()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert discord.post("hi", webhook="https://example.invalid/hook") is True and calls["n"] == 3


def test_discord_gives_up_loudly_on_a_client_error(monkeypatch):
    def fake(req, timeout=0):
        raise urllib.error.HTTPError("u", 404, "nf", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(discord.DiscordError):
        discord.post("hi", webhook="https://example.invalid/hook")


def test_discord_without_a_webhook_does_not_pretend_it_sent(monkeypatch, capsys):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert discord.post("hello") is False
    assert "not sent" in capsys.readouterr().out


# ------------------------------------------------------------------ scorecard
def _dec(i, pick_roi, alt_roi):
    c = lambda k, roi: {"bot": "chronos", "key": k, "outcome": roi, "ts": "2026-10-2%dT09:50:00-04:00" % (i % 9)}
    return {"pick": c(f"p{i}", pick_roi), "board": [c(f"p{i}", pick_roi), c(f"a{i}", alt_roi)]}


def test_scorecard_refuses_a_verdict_on_small_samples():
    sc = scorecard([_dec(i, 0.5, -0.5) for i in range(8)])
    assert sc["n"] == 8 and sc["verdict"].startswith("INSUFFICIENT DATA")


def test_scorecard_says_adds_value_only_when_the_interval_excludes_zero():
    wins = scorecard([_dec(i, 0.3 + 0.01 * (i % 5), -0.2 + 0.01 * (i % 3)) for i in range(MIN_N + 5)])
    assert wins["verdict"].startswith("ADDS VALUE")
    flat = scorecard([_dec(i, 0.1 if i % 2 else -0.1, 0.0) for i in range(MIN_N + 4)])
    assert flat["verdict"].startswith("NOT DEMONSTRATED")
    bad = scorecard([_dec(i, -0.3 + 0.01 * (i % 4), 0.2 + 0.01 * (i % 3)) for i in range(MIN_N + 5)])
    assert bad["verdict"].startswith("HURTS")


def test_scorecard_ignores_decisions_with_no_alternative():
    d = _dec(1, 0.5, 0.1)
    d["board"] = [d["pick"]]
    assert scorecard([d])["n"] == 0


def test_outcomes_are_matched_to_the_bots_own_ledger(registry, repos):
    hist = {"chronos": history_for(registry.get("chronos"), LocalSource(repos))}
    row = next(r for r in hist["chronos"] if r["date"] == "2026-10-23")
    dec = [{"pick": {"bot": "chronos", "key": "k", "ts": f"{row['date']}T09:50:00-04:00", "symbol": row["ticker"],
                     "level": row["level"], "minute": row["minute"], "mode": row["mode"]}, "board": []}]
    assert attach_outcomes(dec, hist) == 1 and dec[0]["pick"]["outcome"] == row["outcome"]
