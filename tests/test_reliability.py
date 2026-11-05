"""Regression tests for the 2026-11-04 'selector never works' investigation.

Root causes covered here:
  1. state push died with 'Author identity unknown' on the Actions runner, so nothing was ever saved
  2. a failed Discord post raised out of the loop and killed the session
  3. read failures were invisible (only in an uncommitted file)
  4. a silent late start looked like 'no trades today'
  5. one unreadable ledger sank the whole EOD scorecard
"""
import os
import subprocess
import sys
from datetime import timedelta

import pytest

from bot_selector import cli
from bot_selector.live import run_live, step
from bot_selector.store import Store

from conftest import et, write


# ------------------------------------------------------------------ 1. git identity
def _bare_env(tmp_path):
    """An environment like a fresh runner: no user.name / user.email anywhere."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR", "GIT_COMMITTER"))}
    env["HOME"] = str(tmp_path / "home")
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    os.makedirs(env["HOME"], exist_ok=True)
    return env


def test_state_push_commits_without_any_git_identity(tmp_path, monkeypatch):
    env = _bare_env(tmp_path)
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    run = lambda *a, cwd=None: subprocess.run(a, cwd=cwd, env=env, check=True, capture_output=True, text=True)
    run("git", "init", "--bare", "-b", "main", str(remote))
    run("git", "init", "-b", "main", str(work))
    run("git", "remote", "add", "origin", str(remote), cwd=work)
    (work / "state").mkdir()
    (work / "README.md").write_text("x")
    # seed commit made with an explicit identity (that is the checkout the runner starts from)
    run("git", "-c", "user.name=seed", "-c", "user.email=s@x", "add", "-A", cwd=work)
    run("git", "-c", "user.name=seed", "-c", "user.email=s@x", "commit", "-m", "seed", cwd=work)
    run("git", "push", "-u", "origin", "main", cwd=work)

    (work / "state" / "board.json").write_text('{"pick_key": "k"}')
    (work / "state" / "decisions.jsonl").write_text('{"ts": "t"}\n')
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SELECTOR_GIT_PUSH", "1")
    monkeypatch.setattr(cli, "ROOT", str(work))
    cli._git_push_state("selector: test")

    log = run("git", "--git-dir", str(remote), "log", "--format=%an <%ae> %s", "main").stdout.splitlines()
    assert log[0] == "bot-selector <bot-selector@users.noreply.github.com> selector: test"
    assert "board.json" in run("git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main").stdout


# ------------------------------------------------------------------ 2. discord outage
def test_a_failed_discord_post_does_not_kill_the_loop_and_is_retried(engine, tmp_path):
    store, logs = Store(str(tmp_path / "s")), []
    calls = {"n": 0}

    def flaky(text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("discord is down")

    t0 = et("2026-10-27 09:48:00")
    step(engine, store, t0, flaky, log=logs.append)
    assert store.read_decisions() == [] and store.load_board() == {}      # nothing recorded as delivered
    step(engine, store, t0 + timedelta(seconds=20), flaky, log=logs.append)
    assert calls["n"] == 2 and len(store.read_decisions()) == 1           # retried, then recorded once
    assert any("post failed" in m for m in logs)


def test_an_unexpected_error_in_one_poll_does_not_end_the_session(engine, tmp_path, monkeypatch):
    store, logs = Store(str(tmp_path / "s")), []
    real = engine.decide
    state = {"n": 0}

    def boom(now, previous_key=None):
        state["n"] += 1
        if state["n"] == 1:
            raise ValueError("bot file drifted")
        return real(now, previous_key=previous_key)
    monkeypatch.setattr(engine, "decide", boom)
    clock_times = [et("2026-10-27 09:48:00")] * 2 + [et("2026-10-27 09:48:30"), et("2026-10-27 16:06:00")]
    it = iter(clock_times)
    n = run_live(engine, store, lambda t: None, now_fn=lambda: next(it), sleep=lambda s: None, log=logs.append)
    assert n == 2 and any("poll failed" in m for m in logs) and store.read_decisions()


# ------------------------------------------------------------------ 3. read problems are loud
def test_a_dead_bot_is_announced_once_not_every_minute(engine, repos, tmp_path):
    write(repos, "apex_chronos/state/sent-2026-10-27.json", "{broken", raw=True)
    store, alerts = Store(str(tmp_path / "s")), []
    t0 = et("2026-10-27 09:48:00")
    for dt in (0, 60, 120):
        step(engine, store, t0 + timedelta(seconds=dt), lambda t: None, alert=alerts.append)
    assert len(alerts) == 1 and "chronos" in alerts[0] and "READ PROBLEM" in alerts[0]


def test_superseded_bots_failing_to_read_do_not_raise_alarms(engine, repos, tmp_path):
    write(repos, "daytrading_bot/state.json", "{broken", raw=True)       # condor is superseded
    alerts = []
    step(engine, Store(str(tmp_path / "s")), et("2026-10-27 09:48:00"), lambda t: None, alert=alerts.append)
    assert alerts == []


# ------------------------------------------------------------------ 4. late start is announced
def test_late_start_is_announced(monkeypatch):
    monkeypatch.delenv("SELECTOR_HANDOFF", raising=False)
    msg = cli.late_start_notice(et("2026-11-04 15:28:00"))
    assert msg and "STARTED LATE at 15:28" in msg
    assert cli.late_start_notice(et("2026-11-04 09:21:00")) is None          # on time
    assert cli.late_start_notice(et("2026-11-04 16:30:00")) is None          # after the session
    assert cli.late_start_notice(et("2026-11-07 11:00:00")) is None          # Saturday
    monkeypatch.setenv("SELECTOR_HANDOFF", "1")
    assert cli.late_start_notice(et("2026-11-04 12:00:00")) is None          # hand-off continuation is not "late"


# ------------------------------------------------------------------ 5. eod survives a bad ledger
def test_eod_scorecard_survives_one_unreadable_ledger(engine, repos, tmp_path, capsys):
    os.remove(os.path.join(repos, "apex_chronos", "ledger", "trades.csv"))
    store = Store(str(tmp_path / "s"))
    decisions, sc = cli._resolve(engine, store)
    assert sc["verdict"].startswith("INSUFFICIENT DATA")
    assert "could not read chronos ledger" in capsys.readouterr().err
