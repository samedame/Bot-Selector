"""The live loop: poll every bot's committed state, re-rank, post only when the pick changes."""
from __future__ import annotations

import time as _time
import traceback
from datetime import datetime, time
from typing import Callable, Optional

from .engine import Engine
from .model import Decision
from .render import render
from .store import Store
from .timeutil import now_et

SESSION_END = time(16, 5)
NEEDED_STATUSES = ("live", "paper")


def _needed_errors(engine: Engine, errors: Optional[dict]) -> dict:
    """Read problems on bots the selector actually depends on (superseded / shadow bots are expected to be quiet)."""
    out = {}
    for bot_id, msg in (errors or {}).items():
        bot = engine.registry.bots.get(bot_id)
        if bot is not None and bot.status in NEEDED_STATUSES:
            out[bot_id] = msg
    return out


def step(engine: Engine, store: Store, now: datetime, post: Callable[[str], object],
         alert: Optional[Callable[[str], object]] = None,
         log: Optional[Callable[[str], object]] = None) -> Decision:
    board = store.load_board()
    prev = board.get("pick_key")
    d = engine.decide(now, previous_key=prev)
    key = d.pick.setup.key if d.pick else None

    # A bot we depend on could not be read: say so in Discord once per distinct problem set. Before this a read
    # failure only landed in state/board.json, which nobody sees, so a dead token looked like a quiet day.
    if alert is not None:
        now_err = _needed_errors(engine, engine.errors)
        if now_err and now_err != _needed_errors(engine, board.get("errors")):
            try:
                alert("SELECTOR READ PROBLEM - these bots could not be read, so their alerts are invisible:\n"
                      + "\n".join(f"- {b}: {m[:160]}" for b, m in sorted(now_err.items())))
            except Exception as e:  # noqa: BLE001 - a failed alert must never stop the loop
                if log:
                    log(f"[alert] could not post read problem: {e}")

    if key != prev:
        message = None
        if key is not None:
            message = render(d)
        elif prev is not None:
            message = "NO TRADE - the previous pick has expired or been exited and nothing else clears the bar."
        if message is not None:
            try:
                post(message)
            except Exception as e:  # noqa: BLE001
                # Do NOT record the change: the next poll sees it again and retries the post, instead of the pick
                # being logged as delivered when it never reached Discord (and instead of killing the session).
                if log:
                    log(f"[discord] post failed, will retry next poll: {e}")
                return d
        store.append_decision(d.to_log())
        store.save_board({"pick_key": key, "pick_ts": now.isoformat(),
                          "pick_bot": d.pick.setup.bot_id if d.pick else None,
                          "errors": engine.errors})
    elif board.get("errors") != engine.errors:
        store.save_board({**board, "errors": engine.errors})
    return d


def _summary(d: Decision, engine: Engine, now: datetime) -> str:
    pick = f"{d.pick.setup.bot_id}:{d.pick.setup.label}" if d.pick else "none"
    elig = sum(1 for b in d.board if b.eligible)
    errs = _needed_errors(engine, engine.errors)
    return (f"[{now:%H:%M:%S} ET] pick={pick} board={len(d.board)} eligible={elig}"
            + (f" READ_ERRORS={sorted(errs)}" if errs else ""))


def run_live(engine: Engine, store: Store, post: Callable[[str], object], poll_s: int = 60,
             max_runtime_min: float = 330, now_fn: Callable[[], datetime] = now_et,
             sleep: Callable[[float], None] = _time.sleep, on_change: Optional[Callable[[], None]] = None,
             alert: Optional[Callable[[str], object]] = None,
             log: Optional[Callable[[str], object]] = None) -> int:
    start = now_fn()
    steps = 0
    while True:
        now = now_fn()
        if now.time() >= SESSION_END or now.weekday() >= 5:
            break
        if (now - start).total_seconds() > max_runtime_min * 60:
            break
        before = store.load_board().get("pick_key")
        try:
            d = step(engine, store, now, post, alert=alert, log=log)
            if log:
                log(_summary(d, engine, now))
        except Exception:  # noqa: BLE001 - one bad poll must not end the whole trading session
            if log:
                log(f"[loop] poll failed, continuing:\n{traceback.format_exc()}")
        steps += 1
        if on_change and store.load_board().get("pick_key") != before:
            on_change()
        sleep(poll_s)
    return steps
