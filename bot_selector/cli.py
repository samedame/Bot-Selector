"""python -m bot_selector <command>

  once        rank right now and print it (no Discord, no log)
  live        the market-hours loop (what the workflow runs)
  digest      post the fleet status + current board
  selftest    read every bot once (any time of day), report which can't be read, post to Discord
  eod         resolve outcomes into the decision log, post the scorecard
  scorecard   print the scorecard from the log
  replay      re-run past sessions with no look-ahead, from checked-out repos (--local ROOT)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta

from . import discord
from .engine import Engine
from .live import run_live
from .registry import ROOT, load_registry
from .render import digest, render
from .scorecard import attach_outcomes, scorecard
from .sources import GitHubSource, LocalSource
from .store import Store
from .timeutil import ET, at_et, now_et
from datetime import time as dtime


def _engine(args) -> Engine:
    reg = load_registry()
    if getattr(args, "local", None):
        src = LocalSource(args.local)
    else:
        src = GitHubSource(cache_path=os.path.join(ROOT, "state", ".etag_cache.json"))
    return Engine(reg, src)


def _store(args) -> Store:
    return Store(getattr(args, "state", None) or os.path.join(ROOT, "state"))


def _git_push_state(msg: str) -> None:
    if os.environ.get("SELECTOR_GIT_PUSH") != "1":
        return
    try:
        subprocess.run(["git", "add", "state/board.json", "state/decisions.jsonl"], check=True, cwd=ROOT)
        if subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=ROOT).returncode != 0:
            subprocess.run(["git", "commit", "-m", msg], check=True, cwd=ROOT)
            subprocess.run(["git", "pull", "--rebase", "-q"], cwd=ROOT)
            subprocess.run(["git", "push", "-q"], check=True, cwd=ROOT)
    except subprocess.CalledProcessError as e:
        print(f"[git] state push failed (non-fatal): {e}", file=sys.stderr)


def cmd_once(args) -> int:
    eng = _engine(args)
    d = eng.decide(now_et())
    print(digest(d, eng.registry) if args.digest else render(d, eng.registry))
    if eng.errors:
        print("\nread problems:", json.dumps(eng.errors, indent=1))
    return 0


def selftest_report(eng: Engine, now) -> tuple[str, bool]:
    """Try to read every bot once and say, bot by bot, whether it worked. Works any time of day."""
    eng.decide(now)
    lines, ok = [], True
    for b in eng.registry.bots.values():
        err = eng.errors.get(b.id)
        needed = b.status in ("live", "paper")
        if err and needed:
            ok = False
        mark = "FAIL" if err and needed else ("skip" if err else "ok  ")
        lines.append(f"{mark} {b.id:<14} {b.status:<10}" + (f" {err[:110]}" if err else ""))
    head = "SELECTOR SELF-TEST: " + ("all live/paper bots readable" if ok else "READ PROBLEMS")
    return head + "\n" + "\n".join(lines), ok


def cmd_selftest(args) -> int:
    eng = _engine(args)
    text, ok = selftest_report(eng, now_et())
    print(text)
    discord.post("```\n" + text[:1850] + "\n```")
    return 0 if ok else 1


def cmd_live(args) -> int:
    eng, store = _engine(args), _store(args)
    steps = run_live(eng, store, post=discord.post, poll_s=args.poll, max_runtime_min=args.max_runtime_min,
                     on_change=lambda: _git_push_state("selector: pick changed"))
    if isinstance(eng.src, GitHubSource):
        eng.src.save_cache()
    print(f"live loop finished after {steps} polls")
    _git_push_state("selector: end of session")
    return 0


def cmd_digest(args) -> int:
    eng = _engine(args)
    d = eng.decide(now_et())
    discord.post(digest(d, eng.registry))
    return 0


def _resolve(eng: Engine, store: Store) -> tuple[list[dict], dict]:
    decisions = store.read_decisions()
    # use full ledgers here: outcomes of past decisions are exactly what we want to read
    hists = {b.id: __import__("bot_selector.adapters", fromlist=["history_for"]).history_for(b, eng.src)
             for b in eng.registry.bots.values() if b.adapter in ("apex", "omega")}
    attach_outcomes(decisions, hists)
    store.rewrite_decisions(decisions)
    return decisions, scorecard(decisions)


def cmd_eod(args) -> int:
    eng, store = _engine(args), _store(args)
    decisions, sc = _resolve(eng, store)
    discord.post("SELECTOR SCORECARD\n" + json.dumps(sc, indent=1))
    _git_push_state("selector: end-of-day scorecard")
    return 0


def cmd_scorecard(args) -> int:
    eng, store = _engine(args), _store(args)
    _, sc = _resolve(eng, store)
    print(json.dumps(sc, indent=1))
    return 0


def replay_day(eng: Engine, day: date, store: Store) -> list[dict]:
    """Walk one past session alert by alert. Each step sees only what had been posted by then, graded on
    ledger history from strictly before that day."""
    stamps: set[datetime] = set()
    for bot in eng.registry.bots.values():
        if bot.adapter not in ("apex", "omega"):
            continue
        probe = eng.collect(at_et(day, dtime(16, 0)))
        for s in probe.get(bot.id, ([], []))[0]:
            stamps.add(s.ts)
    prev = None
    out = []
    for t in sorted(stamps):
        now = t + timedelta(seconds=1)
        d = eng.decide(now, previous_key=prev)
        key = d.pick.setup.key if d.pick else None
        if key != prev:
            rec = d.to_log()
            store.append_decision(rec)
            out.append(rec)
            prev = key
    return out


def cmd_replay(args) -> int:
    eng = _engine(args)
    tmp = tempfile.mkdtemp(prefix="selector-replay-")
    store = Store(tmp)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    d = start
    while d <= end:
        if d.weekday() < 5:
            replay_day(eng, d, store)
        d += timedelta(days=1)
    decisions = store.read_decisions()
    hists = {b.id: __import__("bot_selector.adapters", fromlist=["history_for"]).history_for(b, eng.src)
             for b in eng.registry.bots.values() if b.adapter in ("apex", "omega")}
    attach_outcomes(decisions, hists)
    sc = scorecard(decisions)
    print(f"replayed {args.start}..{args.end}: {len(decisions)} pick changes logged in {tmp}")
    print(json.dumps(sc, indent=1))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"scorecard": sc, "decisions": decisions}, fh, indent=1)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bot_selector", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--local", help="read bot repos from this directory instead of GitHub")
    p.add_argument("--state", help="state directory (default: ./state)")
    sub = p.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("once"); o.add_argument("--digest", action="store_true"); o.set_defaults(fn=cmd_once)
    lv = sub.add_parser("live"); lv.add_argument("--poll", type=int, default=60)
    lv.add_argument("--max-runtime-min", type=float, default=330); lv.set_defaults(fn=cmd_live)
    sub.add_parser("digest").set_defaults(fn=cmd_digest)
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)
    sub.add_parser("eod").set_defaults(fn=cmd_eod)
    sub.add_parser("scorecard").set_defaults(fn=cmd_scorecard)
    r = sub.add_parser("replay"); r.add_argument("--start", required=True); r.add_argument("--end", required=True)
    r.add_argument("--out"); r.set_defaults(fn=cmd_replay)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
