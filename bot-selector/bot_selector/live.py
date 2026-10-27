"""The live loop: poll every bot's committed state, re-rank, post only when the pick changes."""
from __future__ import annotations

import time as _time
from datetime import datetime, time
from typing import Callable, Optional

from .engine import Engine
from .model import Decision
from .render import render
from .store import Store
from .timeutil import now_et

SESSION_END = time(16, 5)


def step(engine: Engine, store: Store, now: datetime, post: Callable[[str], object]) -> Decision:
    board = store.load_board()
    prev = board.get("pick_key")
    d = engine.decide(now, previous_key=prev)
    key = d.pick.setup.key if d.pick else None
    if key != prev:
        store.append_decision(d.to_log())
        if key is not None:
            post(render(d))
        elif prev is not None:
            post("NO TRADE - the previous pick has expired or been exited and nothing else clears the bar.")
        store.save_board({"pick_key": key, "pick_ts": now.isoformat(),
                          "pick_bot": d.pick.setup.bot_id if d.pick else None,
                          "errors": engine.errors})
    elif board.get("errors") != engine.errors:
        store.save_board({**board, "errors": engine.errors})
    return d


def run_live(engine: Engine, store: Store, post: Callable[[str], object], poll_s: int = 60,
             max_runtime_min: float = 330, now_fn: Callable[[], datetime] = now_et,
             sleep: Callable[[float], None] = _time.sleep, on_change: Optional[Callable[[], None]] = None) -> int:
    start = now_fn()
    steps = 0
    while True:
        now = now_fn()
        if now.time() >= SESSION_END or now.weekday() >= 5:
            break
        if (now - start).total_seconds() > max_runtime_min * 60:
            break
        before = store.load_board().get("pick_key")
        step(engine, store, now, post)
        steps += 1
        if on_change and store.load_board().get("pick_key") != before:
            on_change()
        sleep(poll_s)
    return steps
